-- SPDX-License-Identifier: GPL-3.0-or-later
-- One local takeover, tied to server/account/device. Nothing runs at suspend.
local Device = require("device")
local DataStorage = require("datastorage")
local NetworkMgr = require("ui/network/manager")
local lfs = require("libs/libkoreader-lfs")
local util = require("util")
local sha256 = require("ffi/sha2").sha256
local PNG = require("sleep_screen_png")
local ffiUtil = require("ffi/util")
local logger = require("logger")
local Sleep = {}
local KEY = "cwngsync_sleep_screen"
local MAX = 8 * 1024 * 1024
local KEYS = { "screensaver_type", "screensaver_document_cover", "screensaver_img_background",
    "screensaver_show_message", "screensaver_stretch_images", "screensaver_rotate_auto_for_best_fit" }

local function copy(value)
    if type(value) ~= "table" then return value end
    local result = {}
    for key, item in pairs(value) do result[key] = copy(item) end
    return result
end
local function equal(a, b)
    if type(a) ~= type(b) then return false end
    if type(a) ~= "table" then return a == b end
    for k, v in pairs(a) do if not equal(v, b[k]) then return false end end
    for k in pairs(b) do if a[k] == nil then return false end end
    return true
end
local function snapshot()
    local result = {}
    for _, key in ipairs(KEYS) do
        local present = G_reader_settings:has(key)
        result[key] = { present = present }
        if present then result[key].value = copy(G_reader_settings:readSetting(key)) end
    end
    return result
end
local function validSnapshot(value)
    if type(value) ~= "table" then return false end
    for _, key in ipairs(KEYS) do
        local item = value[key]
        if type(item) ~= "table" or type(item.present) ~= "boolean"
            or (item.present and item.value == nil) then return false end
    end
    return true
end
local function restore(value)
    for _, key in ipairs(KEYS) do
        if value[key].present then G_reader_settings:saveSetting(key, copy(value[key].value))
        else G_reader_settings:delSetting(key) end
    end
end
local function persist()
    local settings = G_reader_settings
    -- LuaSettings.flush neither checks write/close nor publishes atomically.
    -- Publish the whole current table on the same filesystem, verifying the
    -- serialized candidate before the checked rename. The caller's journal
    -- makes interruption of the two separate settings commits recoverable.
    if settings.file then
        local temporary = settings.file .. ".cwng-sleep-screen.part"
        local generated, contents = pcall(function() return "return " .. require("dump")(settings.data, nil, true) .. "\n" end)
        if not generated then return false end
        local file = io.open(temporary, "wb")
        if not file then return false end
        local written = file:write(contents)
        local flushed = file:flush()
        local synced = not ffiUtil.fsyncOpenedFile or ffiUtil.fsyncOpenedFile(file, true)
        local closed = file:close()
        if not written or not flushed or not synced or not closed then os.remove(temporary); return false end
        local checked, candidate = pcall(dofile, temporary)
        if not checked or not equal(settings.data, candidate) or not os.rename(temporary, settings.file) then
            os.remove(temporary); return false
        end
        if ffiUtil.fsyncDirectory and not ffiUtil.fsyncDirectory(settings.file) then return false end
        local loaded, stored = pcall(dofile, settings.file)
        return loaded and equal(settings.data, stored)
    end
    -- A wrapped settings implementation may expose its durable projection.
    -- KOReader's real global settings always have a file.
    local ok, result = pcall(settings.flush, settings)
    return ok and result ~= false and type(settings.durable) == "table" and equal(settings.data, settings.durable)
end
local function root()
    return DataStorage:getFullDataDir() .. "/cwngsync/sleep-screen/"
end
local function owned(path)
    if type(path) ~= "string" or path:sub(1, #root()) ~= root() then return false end
    return path:sub(#root() + 1):match("^screen%-[%w_-]+%.png$") ~= nil
end
local function cleanup(state, keep)
    if state and owned(state.active_path) and state.active_path ~= keep then os.remove(state.active_path) end
    -- Includes orphaned publish files after an interrupted first installation.
    local ok, iterator, directory = pcall(lfs.dir, root())
    if ok and iterator then
        for filename in iterator, directory do
            local path = root() .. filename
            if path ~= keep and (owned(path) or filename == "screen-temporary.part") then os.remove(path) end
        end
    end
end
local function stateValid(state)
    if type(state) ~= "table" or state.version ~= 1 or type(state.owner) ~= "string" then return false end
    if state.original and not validSnapshot(state.original) then return false end
    if state.phase and (state.phase ~= "apply" or not validSnapshot(state.rollback)
        or type(state.previous) ~= "table" or state.previous.version ~= 1 or state.previous.owner ~= state.owner
        or state.previous.phase or (state.previous.original and not validSnapshot(state.previous.original))) then return false end
    -- A copied configuration may have an absolute image path in a different
    -- data directory. Restore its snapshot, but never delete that foreign file.
    if state.active_path and (type(state.active_path) ~= "string" or not state.original) then return false end
    return true
end
local function stateLoad()
    local state = G_reader_settings:readSetting(KEY)
    if state == nil then return nil end
    if not stateValid(state) then return nil, "persistence_failed" end
    return copy(state)
end
local function save(state)
    if equal(G_reader_settings:readSetting(KEY), state) then return true end
    if state then G_reader_settings:saveSetting(KEY, copy(state)) else G_reader_settings:delSetting(KEY) end
    return persist()
end
local function recover(state)
    if not state or not state.phase then return state end
    local current = snapshot()
    restore(state.rollback)
    local previous = state.previous
    if not save(previous) then
        restore(current); G_reader_settings:saveSetting(KEY, state)
        return nil, "persistence_failed"
    end
    cleanup(state, previous.active_path)
    return previous
end
local function scope(plugin)
    local s = plugin.settings or {}
    local server = tostring(s.server or ""):gsub("/+$", "")
        :gsub("^(https?://)([^/]+)", function(scheme, host) return scheme:lower() .. host:lower() end)
    local user, id = tostring(s.username or ""), tostring(plugin.device_id or "")
    return #server .. ":" .. server .. #user .. ":" .. user .. #id .. ":" .. id
end
local function transaction(state, nextstate, mutate, original)
    local journal = copy(state)
    if original then journal.original = copy(original) end
    journal.phase, journal.rollback, journal.previous = "apply", snapshot(), copy(state)
    if not save(journal) then G_reader_settings:saveSetting(KEY, state); return false, true end
    mutate()
    if save(nextstate) then return true end
    restore(journal.rollback)
    local rolled_back = save(state)
    if not rolled_back then G_reader_settings:saveSetting(KEY, journal) end
    return false, rolled_back
end
local function remove(state, receipt)
    if not state then return true end
    local nextstate = { version = 1, owner = state.owner, pending = receipt,
        manifest = state.manifest, etag = state.etag }
    if not transaction(state, nextstate, function() if state.original then restore(state.original) end end) then
        return false, "restore_failed"
    end
    cleanup(state)
    return true, nextstate
end
local function method(name)
    local fn = Device[name]
    return type(fn) == "function" and fn(Device) == true
end
local function report(plugin)
    local platform = "other"
    for _, pair in ipairs({ { "isKindle", "kindle" }, { "isKobo", "kobo" }, { "isPocketBook", "pocketbook" },
        { "isAndroid", "android" }, { "isRemarkable", "remarkable" }, { "isEmulator", "emulator" } }) do
        if method(pair[1]) then platform = pair[2]; break end
    end
    local supported = (platform == "kindle" or platform == "kobo" or platform == "emulator") and method("supportsScreensaver")
    local width, height
    if supported and Device.screen then
        local w, h = Device.screen:getWidth(), Device.screen:getHeight()
        if type(w) == "number" and type(h) == "number" and w > 0 and h > 0
            and w % 1 == 0 and h % 1 == 0 and w <= 4096 and h <= 4096 and w * h <= 8000000 then
            width, height = math.min(w, h), math.max(w, h)
        end
    end
    return { platform = platform, supported = supported, plugin_version = tostring(plugin.version),
        screen_width = width, screen_height = height }
end
local function validManifest(manifest)
    if type(manifest) ~= "table" or type(manifest.revision) ~= "string"
        or #manifest.revision > 128 or not manifest.revision:match("^[%w_-]+$") then return false end
    if manifest.state == "none" or manifest.state == "remove" or manifest.state == "waiting_for_device" then return true end
    return manifest.state == "image" and manifest.content_type == "image/png"
        and type(manifest.size) == "number" and manifest.size > 0 and manifest.size <= MAX and manifest.size % 1 == 0
        and type(manifest.sha256) == "string" and #manifest.sha256 <= 128
        and type(manifest.width) == "number" and type(manifest.height) == "number"
        and manifest.width >= 1 and manifest.height >= 1 and manifest.width <= 4096 and manifest.height <= 4096
        and manifest.width % 1 == 0 and manifest.height % 1 == 0 and manifest.width * manifest.height <= 8000000
        and manifest.download_path == "/syncs/sleep-screen/image?revision=" .. manifest.revision
end
local function intact(path, manifest)
    if not owned(path) then return false end
    local f = io.open(path, "rb")
    if not f then return false end
    local bytes = f:read(MAX + 1)
    f:close()
    if not bytes or #bytes ~= manifest.size or sha256(bytes) ~= manifest.sha256 then return false end
    return PNG.validate(bytes, manifest.width, manifest.height)
end
local function acknowledge(client, plugin, state)
    if not state.pending then return true end
    local s = plugin.settings
    local ok = client:ack_sleep_screen(s.username, s.password, Device.model, plugin.device_id, state.pending)
    if ok then
        local nextstate = copy(state); nextstate.pending = nil
        if save(nextstate) then state.pending = nil end
    end
    return ok
end
local busy = false
function Sleep:recoverSleepScreen()
    local state, error = stateLoad()
    if error then return false, error end
    state, error = recover(state)
    if error then return false, error end
    if state and state.owner ~= scope(self) then
        local ok, why = remove(state)
        if not ok then return false, why end
        if not save(nil) then return false, "persistence_failed" end
    end
    return true
end
function Sleep:restoreSleepScreen()
    local state, error = stateLoad()
    if error then return false, error end
    state, error = recover(state)
    if error then return false, error end
    local ok, why = remove(state)
    if not ok then return false, why end
    if not save(nil) then return false, "restore_failed" end
    return true
end
function Sleep:syncSleepScreen()
    if busy or not self:isConfigured() or not NetworkMgr:isConnected() then return end
    busy = true
    local attempted_revision, failure_client
    local called, success, reason = pcall(function()
        local recovered, recovery_error = self:recoverSleepScreen()
        if not recovered then return false, recovery_error end
        local state = stateLoad() or { version = 1, owner = scope(self) }
        local client = require("CWNGSyncClient"):new{ service_url = self.settings.server .. "/kosync", service_spec = self.path .. "/api.json" }
        failure_client = client
        local s, capability = self.settings, report(self)
        local ok, _, err = client:report_sleep_screen_device(s.username, s.password, Device.model, self.device_id, capability)
        if not ok then return false, err end
        local manifest
        ok, manifest, err = client:get_sleep_screen_manifest(s.username, s.password, Device.model, self.device_id, state.etag)
        if not ok then return false, err end
        if type(manifest) == "table" and manifest.status == 304 then
            manifest = state.manifest
            if not validManifest(manifest) then
                ok, manifest, err = client:get_sleep_screen_manifest(s.username, s.password, Device.model, self.device_id)
                if not ok then return false, err end
            end
        end
        if not validManifest(manifest) then return false, "invalid_manifest" end
        attempted_revision = manifest.revision
        state.manifest, state.etag = copy(manifest), manifest.etag
        if manifest.state == "none" or manifest.state == "waiting_for_device" then
            if not save(state) then return false, "persistence_failed" end
            return true
        elseif manifest.state == "remove" then
            if not (state.removed_revision == manifest.revision and not state.original) then
                local removed, nextstate = remove(state, { revision = manifest.revision, status = "removed" })
                if not removed then return false, nextstate end
                state = nextstate; state.removed_revision = manifest.revision
                if not save(state) then return false, "persistence_failed" end
            end
            acknowledge(client, self, state)
            return true
        end
        if not capability.supported then return false, "unsupported" end
        if manifest.width ~= capability.screen_width or manifest.height ~= capability.screen_height then return false, "invalid_manifest" end
        if state.active_revision == manifest.revision and intact(state.active_path, manifest) then
            if not save(state) then return false, "persistence_failed" end
            acknowledge(client, self, state)
            return true
        end
        util.makePath(root())
        local target = root() .. "screen-temporary.part"
        os.remove(target)
        local downloaded, response, download_error = client:download_sleep_screen_image(s.username, s.password,
            Device.model, self.device_id, manifest.download_path, target)
        if not downloaded then return false, download_error or "download_failed" end
        if type(response) == "table" and response.status == 304 then os.remove(target); return false, "download_failed" end
        -- A transport may return binary bytes rather than writing the sink.
        if type(response) == "string" then
            local f = io.open(target, "wb")
            if not f then return false, "storage_failed" end
            local written = #response <= MAX and f:write(response)
            local closed = f:close()
            if not written or not closed then os.remove(target); return false, "storage_failed" end
        end
        local f = io.open(target, "rb")
        if not f then return false, "storage_failed" end
        local bytes = f:read(MAX + 1); f:close()
        if not bytes or #bytes ~= manifest.size then os.remove(target); return false, "invalid_image" end
        if sha256(bytes) ~= manifest.sha256 then os.remove(target); return false, "checksum_mismatch" end
        if not PNG.validate(bytes, manifest.width, manifest.height) then os.remove(target); return false, "invalid_image" end
        -- Also exercise KOReader's renderer where installed, before changing
        -- preferences. Portable validation above remains a genuine L8 decode.
        local has_renderer, renderer = pcall(require, "ui/renderimage")
        if has_renderer then
            local decoded, image = pcall(renderer.renderImageFile, renderer, target, false)
            if not decoded or not image then os.remove(target); return false, "invalid_image" end
            local checked, correct = pcall(function() return image:getWidth() == manifest.width and image:getHeight() == manifest.height end)
            pcall(image.free, image)
            if not checked or not correct then os.remove(target); return false, "invalid_image" end
        end
        local image_file = io.open(target, "rb")
        if not image_file then os.remove(target); return false, "storage_failed" end
        local synced = not ffiUtil.fsyncOpenedFile or ffiUtil.fsyncOpenedFile(image_file, true)
        local closed = image_file:close()
        if not synced or not closed then os.remove(target); return false, "storage_failed" end
        local path = root() .. "screen-" .. manifest.revision .. ".png"
        -- On recovery of a missing file with the same revision, keep a distinct
        -- publish name so failed persistence never destroys a working image.
        if path == state.active_path then path = root() .. "screen-" .. manifest.revision .. "-recovered.png" end
        if not os.rename(target, path) then os.remove(target); return false, "storage_failed" end
        if ffiUtil.fsyncDirectory and not ffiUtil.fsyncDirectory(path) then os.remove(path); return false, "storage_failed" end
        local nextstate = copy(state)
        nextstate.original = nextstate.original or snapshot()
        nextstate.active_path, nextstate.active_revision = path, manifest.revision
        nextstate.pending = { revision = manifest.revision, status = "applied" }
        nextstate.removed_revision = nil
        -- The original snapshot itself is included in the journal persisted
        -- before the first of the six settings is overwritten.
        local installed, rolled_back = transaction(state, nextstate, function()
            local values = { "document_cover", path, "white", false, false, false }
            for i, key in ipairs(KEYS) do G_reader_settings:saveSetting(key, values[i]) end
        end, nextstate.original)
        if not installed then
            -- A failed directory fsync after settings rename may already have
            -- published the new path. Retain its validated bytes unless the
            -- preceding preferences have been verifiably persisted again.
            if rolled_back then os.remove(path) end
            return false, "persistence_failed"
        end
        cleanup(state, path)
        acknowledge(client, self, nextstate)
        return true
    end)
    busy = false
    if not called or not success then
        local error = called and reason or "persistence_failed"
        local allowed = { download_failed=true, invalid_image=true, checksum_mismatch=true, storage_failed=true,
            persistence_failed=true, unsupported=true, invalid_manifest=true, restore_failed=true }
        if allowed[error] and attempted_revision and failure_client then
                local s = self.settings
                pcall(function()
                    failure_client:ack_sleep_screen(s.username, s.password, Device.model, self.device_id,
                        { revision = attempted_revision, status = "failed", error = error })
                end)
        end
        logger.warn("CWNGSync: sleep screen kept for a later sync", allowed[error] and error or "network_unavailable")
    end
    return called and success, reason
end
return Sleep

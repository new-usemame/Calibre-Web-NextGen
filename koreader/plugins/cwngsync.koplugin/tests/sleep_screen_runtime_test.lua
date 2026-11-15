-- Drive the shipped plugin class through its online and manual-sync entry
-- points. The fake transport returns the protocol's report/manifest/image/ack
-- payloads; assertions observe KOReader settings, persisted settings, and files.
package.path = table.concat({ "../?.lua", "./?.lua", package.path }, ";")

local function copy(value)
    if type(value) ~= "table" then return value end
    local result = {}
    for key, item in pairs(value) do result[key] = copy(item) end
    return result
end

local function assertEqual(actual, expected, message)
    if actual ~= expected then
        error(string.format("%s\nexpected: %s\nactual: %s",
            message, tostring(expected), tostring(actual)), 2)
    end
end

local function readFile(path)
    local file = assert(io.open(path, "rb"))
    local bytes = file:read("*a")
    file:close()
    return bytes
end

local originals = {
    screensaver_type = "random_image",
    screensaver_img_background = "black",
    screensaver_show_message = true,
    screensaver_stretch_images = true,
    screensaver_rotate_auto_for_best_fit = true,
    screensaver_message = "Good night, reader",
    screensaver_dir = "/user/pictures",
    screensaver_cycle_index = 4,
    screensaver_stretch_limit_percentage = 31,
}

local settings = { data = copy(originals), durable = copy(originals) }
function settings:has(key) return self.data[key] ~= nil end
function settings:hasNot(key) return self.data[key] == nil end
function settings:readSetting(key, default)
    local value = self.data[key]
    if value == nil then return default end
    return value
end
function settings:saveSetting(key, value) self.data[key] = value end
function settings:delSetting(key) self.data[key] = nil end
function settings:flush()
    self.durable = copy(self.data)
    return true
end
_G.G_reader_settings = settings

local root = os.tmpname()
os.remove(root)
assert(os.execute("mkdir -p " .. root))
local first_image = readFile("fixtures/sleep-screen.png")
local replacement_image = readFile("fixtures/sleep-screen-replacement.png")
local asset_by_revision = { ["revision-1"] = first_image, ["revision-2"] = replacement_image }
local desired = {
    revision = "revision-1", state = "image", width = 8, height = 12,
    content_type = "image/png", size = #first_image, sha256 = "digest-1",
    download_path = "/syncs/sleep-screen/image?revision=revision-1", etag = '"manifest-1"',
}
local acked = {}

local function callbackOf(arguments)
    for index = #arguments, 1, -1 do
        if type(arguments[index]) == "function" then return arguments[index] end
    end
end

local transport = setmetatable({}, {
    __index = function(_, method)
        return function(_, ...)
            local arguments = { ... }
            local callback = callbackOf(arguments)
            local name = tostring(method):lower()
            local answer
            if name:find("ack", 1, true) then
                answer = { revision = desired.revision, status = desired.state == "remove" and "removed" or "applied" }
                acked[#acked + 1] = answer
            elseif name:find("image", 1, true) or name:find("download", 1, true) or name:find("asset", 1, true) then
                answer = asset_by_revision[desired.revision]
                for _, argument in ipairs(arguments) do
                    if type(argument) == "string" and argument:match("%.part$") then
                        local file = assert(io.open(argument, "wb"))
                        file:write(answer or "")
                        file:close()
                    end
                end
            elseif name:find("report", 1, true) or name:find("device", 1, true)
                    or name:find("capabil", 1, true) then
                answer = {
                    platform = "kindle", supported = true, screen_width = 8, screen_height = 12,
                    plugin_version = "5.0.0", state = "none",
                }
            else
                answer = copy(desired)
            end
            if callback then callback(true, answer, nil) end
            return true, answer, nil
        end
    end,
})

local function install(name, value)
    package.preload[name] = function() return value end
    package.loaded[name] = nil
end

local connected = true
install("device", {
    model = "Kindle Paperwhite", home_dir = root,
    isKindle = function() return true end,
    isKobo = function() return false end,
    isEmulator = function() return false end,
    supportsScreensaver = function() return true end,
    screen = { getWidth = function() return 8 end, getHeight = function() return 12 end },
})
install("datastorage", { getFullDataDir = function() return root end, getSettingsDir = function() return root .. "/settings" end })
install("ui/network/manager", {
    isConnected = function() return connected end,
    runWhenConnected = function(_, fn) if connected then fn() end end,
    willRerunWhenOnline = function() return false end,
})
install("ui/uimanager", {
    nextTick = function(_, fn) fn() end,
    scheduleIn = function() end,
    unschedule = function() end,
    show = function() end,
})
install("logger", { info = function() end, warn = function() end, dbg = function() end })
install("gettext", function(text) return text end)
install("json", { encode = function() return "{}" end, decode = function() return {} end })
install("random", { uuid = function() return "runtime-test-uuid" end })
install("ffi/sha2", { md5 = function() return "stable-test-md5" end, sha256 = function() return "digest-1" end })
install("libs/libkoreader-lfs", {
    attributes = function(path)
        if path == root or path:match("/cwngsync$") or path:match("/sleep%-screen$") then
            return { mode = "directory" }
        end
        local file = io.open(path, "rb")
        if not file then return nil end
        local size = file:seek("end")
        file:close()
        return { mode = "file", size = size, modification = 0 }
    end,
    mkdir = function(path) return os.execute("mkdir -p " .. path) end,
    rmdir = function(path) return os.execute("rmdir " .. path) end,
    dir = function() return function() return nil end end,
})
install("util", {
    directoryExists = function(path)
        return path == root or path:match("/cwngsync$") ~= nil or path:match("/sleep%-screen$") ~= nil
    end,
    makePath = function(path) return os.execute("mkdir -p " .. path) end,
    writeToFile = function(contents, path)
        local file = io.open(path, "wb")
        if not file then return false end
        local ok = file:write(contents)
        local closed = file:close()
        return ok ~= nil and closed ~= nil
    end,
})
install("ffi/util", { template = function(text) return text end, df = function() return 1024 * 1024, 1024 * 1024, 1024 * 1024 end })
install("ui/widget/container/widgetcontainer", {
    extend = function(_, class) class.__index = class; return class end,
})
install("ui/widget/infomessage", { new = function(_, fields) return fields end })
install("ui/widget/confirmbox", { new = function(_, fields) return fields end })
install("ui/event", {})
install("event", {})
install("dispatcher", {})
install("pluginloader", {})
install("migration", {})
install("Migration", { canStart = function() return true end, migrateSettings = function() return nil end })
install("device_identity", { settle = function() return false end })
install("ffi/archiver", {})
install("docsettings", {})
install("sync_logic", {})
install("cwng_pending", {
    dropOtherAccounts = function() return 0 end,
    record = function() end,
    settle = function() return false end,
})
install("cwng_library", {})
install("cwng_home", {})
install("cwng_setup", {})
install("cwng_setup_flow", {})
install("cwng_library_runtime", {
    isConfigured = function(self)
        return self.settings and self.settings.server and self.settings.username and self.settings.password
    end,
    libraryEnabled = function(self) return self.settings and self.settings.library_enabled == true end,
})
install("booklist", {})
install("ui/widget/booklist", {})
install("ui/widget/confirmbox", {})
install("ui/widget/multiinputdialog", {})
install("ui/widget/buttondialog", {})
install("ui/widget/inputdialog", {})
install("apps/reader/readerui", {})
install("apps/filemanager/filemanager", {})
install("apps/filemanager/filemanagerutil", {})
install("ui/time", { s = function(value) return value end })
install("time", { s = function(value) return value end })
install("bit", {})
install("optmath", {})
install("Spore", {})
install("CWNGSyncClient", { new = function() return transport end })
install("server_capabilities", { gatesEnforced = function() return false end, remember = function() end })

local Plugin = require("main")
local plugin = setmetatable({
    ui = { document = true, menu = { registerToMainMenu = function() end } },
    path = ".",
    device_id = "sleep-reader-1",
    settings = {
        server = "https://cwng.invalid", username = "reader", password = "app-password",
        auto_sync = true, library_enabled = false,
    },
}, Plugin)
plugin.flushPending = function(_, done) if done then done(true) end end
plugin.getDeliveryRootPath = function() return nil end
plugin.collectDeliveries = function() end
plugin.hasCurrentDocument = function() return false end
plugin.showConnectChoices = function() error("runtime test should remain connected") end

local function assertManaged(path, expected_message)
    assertEqual(settings:readSetting("screensaver_type"), "document_cover",
        expected_message .. ": KOReader selects its document-cover image renderer")
    local active = settings:readSetting("screensaver_document_cover")
    assert(type(active) == "string" and active:sub(1, #root) == root,
        expected_message .. ": the configured image belongs to KOReader's data directory")
    local file = assert(io.open(active, "rb"), expected_message .. ": the managed image exists")
    file:close()
    assertEqual(settings:readSetting("screensaver_img_background"), "white", expected_message .. ": white background")
    assertEqual(settings:readSetting("screensaver_show_message"), false, expected_message .. ": no overlay message")
    assertEqual(settings:readSetting("screensaver_stretch_images"), false, expected_message .. ": aspect is preserved")
    assertEqual(settings:readSetting("screensaver_rotate_auto_for_best_fit"), false, expected_message .. ": no extra rotation")
    assertEqual(settings.durable.screensaver_type, "document_cover", expected_message .. ": settings reached durable storage")
    assertEqual(settings.durable.screensaver_document_cover, active, expected_message .. ": image path reached durable storage")
    assertEqual(settings.durable.screensaver_img_background, "white", expected_message .. ": background reached durable storage")
    assertEqual(settings.durable.screensaver_show_message, false, expected_message .. ": message state reached durable storage")
    assertEqual(settings.durable.screensaver_stretch_images, false, expected_message .. ": stretch state reached durable storage")
    assertEqual(settings.durable.screensaver_rotate_auto_for_best_fit, false, expected_message .. ": rotation state reached durable storage")
    return active
end

-- The first call uses the real online entry point and a registered-device
-- manifest. Baseline fails at assertManaged because today's runtime leaves the
-- user's random-image settings alone and never fetches or installs the asset.
local passed, failure = xpcall(function()
plugin:onDeviceOnline(true)
local first_active = assertManaged("first sync", "online sync applies the saved sleep screen")
assertEqual(settings:readSetting("screensaver_message"), originals.screensaver_message,
    "takeover leaves the user's message text alone")
assertEqual(settings:readSetting("screensaver_dir"), originals.screensaver_dir,
    "takeover leaves the random-image directory alone")
assertEqual(settings:readSetting("screensaver_cycle_index"), originals.screensaver_cycle_index,
    "takeover leaves random-image cycling alone")

settings:saveSetting("screensaver_type", "random_image")
plugin:onDeviceOnline(true)
assertEqual(settings:readSetting("screensaver_type"), "random_image",
    "a repeated manifest does not continually overwrite a user's later setting change")

desired = {
    revision = "revision-2", state = "image", width = 8, height = 12,
    content_type = "image/png", size = #replacement_image, sha256 = "digest-1",
    download_path = "/syncs/sleep-screen/image?revision=revision-2", etag = '"manifest-2"',
}
plugin:syncEverythingNow()
local replacement_active = assertManaged("manual Sync now replaces the image", "manual Sync now installs a replacement")
assert(replacement_active ~= first_active, "replacement uses a new managed image path")
assertEqual(settings.durable.screensaver_message, originals.screensaver_message,
    "replacement keeps the first pre-CWNG snapshot rather than snapshotting CWNG settings")

desired = { revision = "revision-3", state = "remove", etag = '"manifest-3"' }
connected = false
plugin:syncEverythingNow()
assertEqual(settings:readSetting("screensaver_type"), "document_cover",
    "offline manual sync leaves the last working image installed")
assertEqual(settings:readSetting("screensaver_document_cover"), replacement_active,
    "offline removal does not delete the active image")
connected = true
plugin:onDeviceOnline(true)
for key, value in pairs(originals) do
    assertEqual(settings:readSetting(key), value, "remove restores original setting " .. key)
    assertEqual(settings.durable[key], value, "remove durably restores original setting " .. key)
end
assertEqual(settings:readSetting("screensaver_document_cover"), nil,
    "remove deletes a previously absent document-cover setting")
assertEqual(settings.durable.screensaver_document_cover, nil,
    "remove durably deletes the previously absent document-cover setting")
assertEqual(settings:readSetting("screensaver_message"), originals.screensaver_message,
    "remove keeps unrelated user settings unchanged")
assert(#acked > 0, "the runtime acknowledges a persisted apply or restore")
end, debug.traceback)
os.execute("rm -rf " .. root)
if not passed then error(failure, 0) end
print("sleep screen runtime: online apply, manual replacement, and removal passed")

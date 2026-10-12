-- SPDX-License-Identifier: GPL-3.0-or-later
-- KOReader's built-in Progress sync cannot identify its GETs. Disable its
-- automatic requests once for a matching server, while keeping manual sync.
-- Source: koreader/plugins/kosync.koplugin/main.lua: settings in kosync.lua,
-- readSetting("settings"), custom_server and auto_sync.
local Guard = {}
local SEEN_KEY = "cwngsync_builtin_sync_guard"

local function endpoint(url)
    if type(url) ~= "string" then return nil end
    url = url:match("^%s*(.-)%s*$"):gsub("/+$", "")
    local scheme, authority, path = url:match("^(https?)://([^/]+)(.*)$")
    if not scheme then return nil end
    return scheme:lower() .. "://" .. authority:lower() .. path
end

function Guard.check(args)
    -- A live guard may have been installed before the latest auth reply.
    -- Make that wrapper dormant too, without changing a reader's settings.
    local builtin = args.builtin
    if builtin and builtin.cwng_pull_guard then
        builtin.cwng_pull_guard.enabled = false
    end
    if args.share_place_gates ~= true then return false end
    local server = endpoint(args.server)
    if not server then return false end
    local store = builtin and builtin.settings_obj or args.open_settings()
    local settings = builtin and builtin.settings or store:readSetting("settings")
    if type(settings) ~= "table"
            or endpoint(settings.custom_server) ~= server .. "/kosync" then
        return false
    end
    if builtin and builtin.getProgress and not builtin.cwng_pull_guard then
        -- onReaderReady may already have queued an anonymous automatic pull;
        -- its closure does not recheck auto_sync. Guard the live entry point
        -- too, including on later starts where the setting is already off.
        local get_progress = builtin.getProgress
        local guard = { enabled = true, server = server .. "/kosync" }
        builtin.getProgress = function(self, ensure_networking, interactive, ...)
            if not guard.enabled or endpoint(self.settings.custom_server) ~= guard.server
                    or interactive or self.settings.auto_sync then
                return get_progress(self, ensure_networking, interactive, ...)
            end
        end
        builtin.cwng_pull_guard = guard
    elseif builtin and builtin.cwng_pull_guard then
        builtin.cwng_pull_guard.enabled = true
        builtin.cwng_pull_guard.server = server .. "/kosync"
    end
    if not settings.auto_sync then return false end
    local seen = args.reader_settings:readSetting(SEEN_KEY) or {}
    if seen[server] then return false end
    settings.auto_sync = false
    store:saveSetting("settings", settings)
    store:flush()
    if builtin then
        builtin.settings = settings
        builtin:registerEvents()
        if builtin.periodic_push_task then args.unschedule(builtin.periodic_push_task) end
        builtin.periodic_push_scheduled = false
        builtin.updated = nil
    end
    seen[server] = true
    args.reader_settings:saveSetting(SEEN_KEY, seen)
    args.reader_settings:flush()
    args.notice()
    return true
end

return Guard

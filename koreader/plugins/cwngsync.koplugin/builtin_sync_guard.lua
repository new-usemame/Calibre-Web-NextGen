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
    local server = endpoint(args.server)
    if not server then return false end
    local builtin = args.builtin
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
        builtin.getProgress = function(self, ensure_networking, interactive, ...)
            if interactive or self.settings.auto_sync then
                return get_progress(self, ensure_networking, interactive, ...)
            end
        end
        builtin.cwng_pull_guard = true
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

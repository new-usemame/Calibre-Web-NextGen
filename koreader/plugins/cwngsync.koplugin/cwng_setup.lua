--[[
Connecting a device to a CWNG server, the parts that decide. The screens and
network calls live in setup_flow.lua.

Three ways in, each needing something different:
  * a ready-made plugin downloaded from the CWNG website carries setup.json
    with the server and an app password: needs a computer, no typing;
  * pairing: the device shows a code (and a QR code), the reader approves it
    on the website: needs only Wi-Fi and a signed-in browser;
  * typing the address and a password, as before.
]]

local Setup = {}

Setup.BUNDLE_FILE = "setup.json"

-- Applied once, after the first successful connection. Automatic everything:
-- progress, highlights and read status sync without a button; a newer position
-- from another device is taken silently, an older one never.
Setup.PLUGIN_DEFAULTS = {
    auto_sync = true,
    sync_forward = 2,   -- SYNC_STRATEGY.SILENT
    sync_backward = 3,  -- SYNC_STRATEGY.DISABLE
    sync_annotations = true,
    pages_before_update = 5,
}
Setup.DEFAULTS_VERSION = 1

local function trim(value)
    return (value:gsub("^%s+", ""):gsub("%s+$", ""))
end

-- Accept what a person types: a bare host, a host:port, or a full URL with a
-- trailing slash or the /kosync suffix the old instructions asked for.
function Setup.normalizeServer(input)
    if type(input) ~= "string" then return nil end
    local value = trim(input)
    if value == "" or value:find("%s") then return nil end
    -- The scheme and host name are the same in any case; a path is not.
    value = value:gsub("^[%a][%w+.-]*://[^/?#]*", string.lower)
    if not value:match("^https?://") then
        if value:match("^%a[%w+.-]*://") then return nil end
        value = "http://" .. value
    end
    value = value:gsub("/+$", ""):gsub("/kosync$", ""):gsub("/+$", "")
    local host = value:match("^https?://([^/?#]+)")
    if not host or host == "" or host:match("^:") then return nil end
    return value
end

-- Typed without a scheme, an address is tried as http first: a CWNG server's
-- own port. This is the https address to try when that fails, since a server
-- behind a proxy that speaks only https refuses http, and browsers hide the
-- https:// the reader used. Nil when the reader typed a scheme.
function Setup.secureAlternative(input)
    if type(input) ~= "string" or trim(input):match("^%a[%w+.-]*://") then return nil end
    local server = Setup.normalizeServer(input)
    return server and ("https://" .. server:sub(#"http://" + 1)) or nil
end

-- Which account a server address and username name. The library and the queued
-- reading belong to one account, and a change of account hands them over, so
-- the same account typed another way must give the same key: a trailing slash,
-- a capital in the host, or a username in other case (the server matches
-- usernames without case).
function Setup.accountKey(server, username)
    return (Setup.normalizeServer(server) or tostring(server)) .. "|" .. tostring(username):lower()
end

-- setup.json, as written by the website's "ready-made plugin" download.
function Setup.parseBundle(text, decode)
    if type(text) ~= "string" or text == "" then return nil, "empty setup file" end
    local ok, data = pcall(decode, text)
    if not ok or type(data) ~= "table" then return nil, "unreadable setup file" end
    local server = Setup.normalizeServer(data.server)
    if not server then return nil, "setup file has no usable server address" end
    if type(data.username) ~= "string" or data.username == ""
            or type(data.password) ~= "string" or data.password == "" then
        return nil, "setup file has no sign-in details"
    end
    return { server = server, username = data.username, password = data.password }
end

-- Interpret one poll of the pairing request.
--   pending  - keep waiting
--   approved - credentials = { server, username, password }
--   denied / expired - stop and say so
--   error    - a transient failure; keep waiting unless it persists
function Setup.pairingOutcome(ok, body, reason, fallback_server)
    if type(body) == "table" then
        local status = body.status
        if status == "approved" then
            if type(body.username) == "string" and body.username ~= ""
                    and type(body.password) == "string" and body.password ~= "" then
                -- The address this device reached the server at, not the one
                -- the server reports for itself: behind a proxy that does not
                -- pass on its scheme and host, that is an inside address, or
                -- plain http where the device used https, and the app
                -- password would go out in the clear from then on.
                return {
                    state = "approved",
                    credentials = {
                        server = fallback_server or Setup.normalizeServer(body.server),
                        username = body.username,
                        password = body.password,
                    },
                }
            end
            return { state = "error", message = "approval arrived without sign-in details" }
        elseif status == "pending" or status == "denied" or status == "expired" then
            return { state = status }
        end
    end
    if reason == "HTTP 410" then return { state = "expired" } end
    if reason == "HTTP 404" then return { state = "expired" } end
    return { state = "error", message = reason or "no response from server" }
end

-- The page a phone lands on from the QR code, with the code already filled in.
function Setup.verifyLink(verify_url, server, user_code)
    local base = type(verify_url) == "string" and verify_url ~= "" and verify_url
        or (server .. "/pair")
    if base:sub(1, 1) == "/" then base = server .. base end
    local code = (user_code or ""):gsub("[^%w]", "")
    local separator = base:find("?", 1, true) and "&" or "?"
    return base .. separator .. "code=" .. code
end

-- Plugin settings to set on first connection. Returns the keys it changed, and
-- never touches a settings table that has already been through it.
function Setup.applyPluginDefaults(settings)
    if (settings.defaults_version or 0) >= Setup.DEFAULTS_VERSION then return {} end
    local changed = {}
    for key, value in pairs(Setup.PLUGIN_DEFAULTS) do
        settings[key] = value
        changed[#changed + 1] = key
    end
    settings.defaults_version = Setup.DEFAULTS_VERSION
    table.sort(changed)
    return changed
end

-- KOReader settings that make the library the home screen: open on it, stay
-- in it, most recent first. "Recent" is the modification time, which the
-- plugin sets when a library book is opened or downloaded (and to the date
-- added for a cover). Not KOReader's "last read date": that is the access
-- time, and e-reader storage (Kindle's FUSE /mnt/us) updates it on every
-- read, so indexing covers alone reshuffles the whole grid.
function Setup.readerDefaults(library_root)
    return {
        home_dir = library_root,
        lock_home_folder = true,
        start_with = "filemanager",
        collate = "date",
        reverse_collate = false,
    }
end

return Setup

package.path = table.concat({
    "./?.lua",
    "../?.lua",
    package.path,
}, ";")

local Setup = require("cwng_setup")

local function assertEqual(actual, expected, message)
    if actual ~= expected then
        error(string.format("%s\nexpected: %s\nactual: %s",
            message, tostring(expected), tostring(actual)), 2)
    end
end

-- A tiny JSON-object decoder standing in for KOReader's json module: enough
-- for flat objects of strings, and it raises on anything else, as json does.
local function decode(text)
    local body = text:match("^%s*{(.*)}%s*$")
    if not body then error("not an object") end
    local out = {}
    for key, value in body:gmatch('"([^"]+)"%s*:%s*"([^"]*)"') do out[key] = value end
    return out
end

local function testTypedAddressesBecomeOneCanonicalForm()
    assertEqual(Setup.normalizeServer("books.example.org"), "http://books.example.org", "bare host")
    assertEqual(Setup.normalizeServer("  https://books.example.org/ "), "https://books.example.org", "slash + spaces")
    assertEqual(Setup.normalizeServer("http://192.168.1.20:8083/kosync"), "http://192.168.1.20:8083", "old /kosync suffix")
    assertEqual(Setup.normalizeServer("https://example.org/cwng/"), "https://example.org/cwng", "subpath kept")
    assertEqual(Setup.normalizeServer("ftp://example.org"), nil, "other schemes refused")
    assertEqual(Setup.normalizeServer("http://"), nil, "no host")
    assertEqual(Setup.normalizeServer("my server"), nil, "spaces")
    assertEqual(Setup.normalizeServer(nil), nil, "nothing typed")
end

local function testReadyMadeBundleNeedsServerAndCredentials()
    local config = Setup.parseBundle(
        '{"server": "http://192.168.1.20:8083/", "username": "reader", "password": "tok"}', decode)
    assertEqual(config and config.server, "http://192.168.1.20:8083", "server normalized")
    assertEqual(config.username, "reader", "username")
    assertEqual(config.password, "tok", "password")

    local missing, why = Setup.parseBundle('{"server": "http://x"}', decode)
    assertEqual(missing, nil, "a bundle without credentials is refused")
    assertEqual(why, "setup file has no sign-in details", "and says why")
    assertEqual((Setup.parseBundle('{"server": "http://x", "username": "reader"}', decode)), nil,
        "a username without its password is refused")
    assertEqual((Setup.parseBundle("not json", decode)), nil, "garbage refused")
    assertEqual((Setup.parseBundle("", decode)), nil, "empty refused")
    assertEqual((Setup.parseBundle('{"server": "", "username": "a", "password": "b"}', decode)), nil,
        "no server refused")
end

local function testPairingOutcomes()
    local approved = Setup.pairingOutcome(true,
        { status = "approved", server = "http://srv:8083/", username = "reader", password = "app" },
        nil, "http://fallback")
    assertEqual(approved.state, "approved", "approved")
    assertEqual(approved.credentials.server, "http://srv:8083", "server from approval")
    assertEqual(approved.credentials.password, "app", "password handed over")

    local no_server = Setup.pairingOutcome(true,
        { status = "approved", username = "reader", password = "app" }, nil, "http://typed")
    assertEqual(no_server.credentials.server, "http://typed", "falls back to the address it asked")

    assertEqual(Setup.pairingOutcome(true, { status = "approved", username = "reader" }).state,
        "error", "approval without a password is not a connection")
    assertEqual(Setup.pairingOutcome(true, { status = "pending" }).state, "pending", "pending")
    assertEqual(Setup.pairingOutcome(false, { status = "denied" }, "HTTP 403").state, "denied", "denied")
    assertEqual(Setup.pairingOutcome(false, nil, "HTTP 410").state, "expired", "gone = expired")
    assertEqual(Setup.pairingOutcome(false, nil, "timeout").state, "error", "transient")
end

local function testVerifyLinkCarriesTheCode()
    assertEqual(Setup.verifyLink("http://srv/devices?pair=1", "http://srv", "K7M4-QX2P"),
        "http://srv/devices?pair=1&code=K7M4QX2P", "code appended to the server's link")
    assertEqual(Setup.verifyLink("/devices", "http://srv", "AB-CD"),
        "http://srv/devices?code=ABCD", "relative link made absolute")
    assertEqual(Setup.verifyLink(nil, "http://srv", "ABCD"),
        "http://srv/pair?code=ABCD", "no link from server")
end

local function testDefaultsApplyExactlyOnce()
    local settings = { auto_sync = false, sync_annotations = false }
    local changed = Setup.applyPluginDefaults(settings)
    assertEqual(settings.auto_sync, true, "auto sync on")
    assertEqual(settings.sync_annotations, true, "highlights on")
    assertEqual(settings.sync_forward, 2, "newer positions taken silently")
    assertEqual(settings.sync_backward, 3, "older positions never")
    assertEqual(#changed > 0, true, "reports what it changed")
    settings.auto_sync = false -- the reader turned it off later
    assertEqual(#Setup.applyPluginDefaults(settings), 0, "second run changes nothing")
    assertEqual(settings.auto_sync, false, "a later choice is respected")
end

local function testReaderDefaultsMakeTheLibraryHome()
    local defaults = Setup.readerDefaults("/mnt/us/cwng-library")
    assertEqual(defaults.home_dir, "/mnt/us/cwng-library", "home")
    assertEqual(defaults.lock_home_folder, true, "locked")
    assertEqual(defaults.start_with, "filemanager", "opens on it")
    assertEqual(defaults.collate, "date", "most recent first, by the time the plugin controls")
    assertEqual(defaults.reverse_collate, false, "newest at the top")
end

testTypedAddressesBecomeOneCanonicalForm()
testReadyMadeBundleNeedsServerAndCredentials()
testPairingOutcomes()
testVerifyLinkCarriesTheCode()
testDefaultsApplyExactlyOnce()
testReaderDefaultsMakeTheLibraryHome()
print("cwng_setup_test.lua: all tests passed")

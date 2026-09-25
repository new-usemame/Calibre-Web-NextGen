-- The address a reader types when connecting.
--
-- Typed without http:// or https://, an address is tried as http: a CWNG
-- server's own port. A server behind a proxy that speaks only https (Caddy's
-- `tls internal` on a home network is common) refuses that with a 400, and
-- browsers hide the https:// they used, so the reader has no way to know what
-- to add. The flow is driven here through its real dialogs' callbacks, with
-- KOReader's widgets and the server stubbed.

package.path = table.concat({
    "../?.lua",
    "./?.lua",
    package.path,
}, ";")

local function stub(name, value)
    package.loaded[name] = value
end

local shown, dialogs = {}, {}
local typed = ""
-- The fake server: which base URLs answer, which refuse the password, and
-- what each request asked.
local answering, refusing, asked = {}, {}, {}

stub("ui/widget/buttondialog", { new = function(_, fields) dialogs[#dialogs + 1] = fields; return fields end })
stub("ui/widget/inputdialog", { new = function(_, fields)
    fields.getInputText = function() return typed end
    fields.onShowKeyboard = function() end
    dialogs[#dialogs + 1] = fields
    return fields
end })
stub("ui/widget/infomessage", { new = function(_, fields) return fields end })
stub("cwng_pairing_screen", { new = function(_, fields) fields.pairing_screen = true; return fields end })
stub("ui/uimanager", {
    show = function(_, widget) shown[#shown + 1] = widget end,
    close = function() end,
    scheduleIn = function() end,
    unschedule = function() end,
})
stub("ui/network/manager", { runWhenConnected = function(_, f) f() end })
stub("device", { model = "Kindle", setIgnoreInput = function() end })
stub("json", {})
stub("libs/libkoreader-lfs", {})
stub("logger", { warn = function() end, info = function() end, dbg = function() end })
stub("util", {})
stub("ffi/util", { template = function(text, ...)
    local args = { ... }
    return (text:gsub("%%(%d)", function(n) return tostring(args[tonumber(n)]) end))
end })
stub("gettext", function(text) return text end)

local function base(client) return client.service_url:gsub("/kosync$", "") end
stub("CWNGSyncClient", {
    new = function(_, o)
        function o:pair_start(_, _, callback)
            asked[#asked + 1] = "pair " .. base(self)
            if answering[base(self)] then
                callback(true, { device_code = "dc", user_code = "ABCD-1234", verify_url = base(self) .. "/pair" })
            else
                callback(false, nil, "HTTP 400")
            end
        end
        function o:authorize()
            asked[#asked + 1] = "sign in " .. base(self)
            if answering[base(self)] then return true end
            if refusing[base(self)] then return false, nil, nil end
            return false, nil, "HTTP 400"
        end
        return o
    end,
    statusOf = function(reason) return tonumber(tostring(reason):match("(%d%d%d)")) end,
    plainReason = function(reason) return tostring(reason) end,
})
G_reader_settings = { saveSetting = function() end } -- luacheck: ignore

local SetupFlow = require("cwng_setup_flow")

local function assertEqual(actual, expected, message)
    if actual ~= expected then
        error(string.format("%s\nexpected: %s\nactual: %s",
            message, tostring(expected), tostring(actual)), 2)
    end
end

local function newPlugin()
    local plugin = setmetatable({ settings = {}, device_id = "device", path = ".", settings_key = "cwngsync" },
        { __index = SetupFlow })
    function plugin:registerEvents() end
    function plugin:applyReaderDefaults() end
    function plugin:syncLibrary() end
    function plugin:login(_, callback) callback("reader", "secret") end
    return plugin
end

local function reset(servers)
    shown, dialogs, asked, answering, refusing = {}, {}, {}, {}, {}
    for _, server in ipairs(servers) do answering[server] = true end
end

local function press(dialog, label)
    for _, row in ipairs(dialog.buttons) do
        for _, button in ipairs(row) do
            if button.text == label then return button.callback() end
        end
    end
    error("no button " .. label)
end

local function pairingShown()
    for _, widget in ipairs(shown) do
        if widget.pairing_screen then return widget end
    end
end

local function testAnAddressTypedWithoutHttpsFindsAnHttpsOnlyServer()
    reset({ "https://10.0.30.36:8083" })
    local plugin = newPlugin()
    typed = "10.0.30.36:8083"
    plugin:startPairing()
    press(dialogs[#dialogs], "Continue")
    assertEqual(table.concat(asked, ", "), "pair http://10.0.30.36:8083, pair https://10.0.30.36:8083",
        "http is tried first, then https")
    assert(pairingShown(), "the code is shown")
    assertEqual(pairingShown().code, "ABCD-1234", "the https server's code")
    -- A browser adds http:// to an address typed without one, and this server
    -- refuses http: the address to type must say https.
    assertEqual(pairingShown().address, "https://10.0.30.36:8083/pair", "the address to open says https")
    pairingShown().on_cancel() -- one pairing at a time
end

local function testAPlainHttpServerIsAskedOnce()
    reset({ "http://192.168.1.20:8083" })
    typed = "192.168.1.20:8083"
    newPlugin():startPairing()
    press(dialogs[#dialogs], "Continue")
    assertEqual(table.concat(asked, ", "), "pair http://192.168.1.20:8083", "one request when http answers")
    assert(pairingShown(), "the code is shown")
    assertEqual(pairingShown().address, "192.168.1.20:8083/pair", "plain http needs no scheme to type")
    pairingShown().on_cancel() -- one pairing at a time
end

local function testAnAddressTypedWithASchemeIsTakenAtItsWord()
    reset({ "https://10.0.30.36:8083" })
    typed = "http://10.0.30.36:8083"
    newPlugin():startPairing()
    press(dialogs[#dialogs], "Continue")
    assertEqual(table.concat(asked, ", "), "pair http://10.0.30.36:8083", "no other scheme is tried")
    assertEqual(pairingShown(), nil, "no code")
end

local function testAFailedAddressIsOfferedAgainWithoutTheGuessedScheme()
    reset({})
    local plugin = newPlugin()
    typed = "10.0.30.36:8083"
    plugin:startPairing()
    press(dialogs[#dialogs], "Continue")
    assertEqual(#asked, 2, "both schemes are tried")
    assert(tostring(shown[#shown].text):find("10.0.30.36:8083", 1, true), "the failure names the address")
    -- Offered back as typed, the next try still tries both.
    plugin:startPairing()
    assertEqual(dialogs[#dialogs].input, "10.0.30.36:8083", "offered as it was typed")
    reset({})
    plugin.settings.server = "https://books.example.com"
    plugin:startPairing()
    assertEqual(dialogs[#dialogs].input, "https://books.example.com", "a scheme the reader chose is kept")
end

local function testSigningInFindsAnHttpsOnlyServerToo()
    reset({ "https://10.0.30.36:8083" })
    local plugin = newPlugin()
    typed = "10.0.30.36:8083"
    plugin:showConnectChoices()
    press(dialogs[#dialogs], "Sign in with username and password")
    press(dialogs[#dialogs], "Continue")
    assertEqual(table.concat(asked, ", "), "sign in http://10.0.30.36:8083, sign in https://10.0.30.36:8083",
        "http is tried first, then https")
    assertEqual(plugin.settings.server, "https://10.0.30.36:8083", "and the address that worked is kept")
end

local function testAPasswordRefusedOverHttpIsNotSentAgain()
    reset({})
    refusing["http://192.168.1.20:8083"] = true
    typed = "192.168.1.20:8083"
    newPlugin():showConnectChoices()
    press(dialogs[#dialogs], "Sign in with username and password")
    press(dialogs[#dialogs], "Continue")
    assertEqual(table.concat(asked, ", "), "sign in http://192.168.1.20:8083", "the server answered: once")
    assert(tostring(shown[#shown].text):find("not accepted", 1, true), "and the reader is told why")
end

testAnAddressTypedWithoutHttpsFindsAnHttpsOnlyServer()
testAPlainHttpServerIsAskedOnce()
testAnAddressTypedWithASchemeIsTakenAtItsWord()
testAFailedAddressIsOfferedAgainWithoutTheGuessedScheme()
testSigningInFindsAnHttpsOnlyServerToo()
testAPasswordRefusedOverHttpIsNotSentAgain()

print("cwng_setup_flow tests passed")

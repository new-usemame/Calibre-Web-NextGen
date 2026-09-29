-- Two Kindles set up by copying the koreader folder from one to the other
-- (#2351). The copy carries KOReader's device_id, so the server merged both
-- e-readers into one device. The plugin as KOReader loads it must give the
-- second Kindle its own ID and tell the reader why.

package.path = table.concat({ "../?.lua", "./?.lua", package.path }, ";")

local function assertEqual(actual, expected, message)
    if actual ~= expected then
        error(string.format("%s\nexpected: %s\nactual: %s",
            message, tostring(expected), tostring(actual)), 2)
    end
end

-- KOReader's G_reader_settings, backed by a plain table so it can be copied.
local function newSettings(data)
    local store = { data = data or {} }
    function store:readSetting(key) return self.data[key] end
    function store:saveSetting(key, value) self.data[key] = value end
    function store:hasNot(key) return self.data[key] == nil end
    return store
end

local function copyOf(settings)
    local data = {}
    for key, value in pairs(settings.data) do data[key] = value end
    return newSettings(data)
end

-- A stand-in for md5: stable, and never contains its input.
local function hash(text)
    local h = 5381
    for i = 1, #text do h = (h * 33 + text:byte(i)) % 4294967296 end
    return string.format("%08x", h)
end

local issued = 0
local function newUuid()
    issued = issued + 1
    return "uuid-" .. issued
end

-- Anything the plugin requires that this test does not care about.
local function stub()
    return setmetatable({}, {
        __index = function() return stub() end,
        __call = function() return stub() end,
    })
end

-- Loads main.lua the way KOReader's PluginLoader does, on an e-reader whose
-- files read as `files`, with `settings` as G_reader_settings. Returns the
-- plugin and every widget it shows.
local function loadPlugin(settings, device, files)
    local main_path = assert(package.searchpath("main", package.path), "cannot locate main.lua")
    local shown = {}
    local ticks = {}
    local real = {
        device_identity = true, json = false,
    }
    local env
    local function fakeRequire(name)
        if name == "device" then return device end
        if name == "random" then return { uuid = newUuid } end
        if name == "ffi/sha2" then return { md5 = hash } end
        if name == "ui/uimanager" then
            return {
                show = function(_, widget) shown[#shown + 1] = widget end,
                nextTick = function(_, fn) ticks[#ticks + 1] = fn end,
            }
        end
        if name == "ui/widget/infomessage" then
            return { new = function(_, fields) return fields end }
        end
        if name == "gettext" then return function(text) return text end end
        if name == "ui/widget/container/widgetcontainer" then
            return { extend = function(_, class) return class end }
        end
        if real[name] then
            local module = package.loaded[name]
            if module then return module end
            local path = assert(package.searchpath(name, package.path))
            module = assert(loadfile(path, "t", env))()
            package.loaded[name] = module
            return module
        end
        return stub()
    end
    env = setmetatable({
        require = fakeRequire,
        G_reader_settings = settings,
        io = { open = function(path)
            local line = files[path]
            if not line then return nil end
            return { read = function() return line end, close = function() end }
        end },
    }, { __index = _G })
    package.loaded.device_identity = nil
    local plugin = assert(loadfile(main_path, "t", env))()
    -- KOReader makes one instance for the file browser and another for each
    -- book it opens, all from the one loaded module.
    local function newInstance()
        local instance = setmetatable({ ui = { menu = stub(), document = true } }, { __index = plugin })
        -- Only the part of init that reads the ID and shows the notice matters here.
        for _, name in ipairs({ "registerToMainMenu", "installOpenHook", "installStatusHook",
                "installHomeButton", "onDispatcherRegisterActions", "registerEvents" }) do
            instance[name] = function() end
        end
        local ok, err = pcall(instance.init, instance)
        assert(ok, "init failed: " .. tostring(err))
        return instance
    end
    local file_browser = newInstance()
    local reader = newInstance()
    assertEqual(reader.device_id, file_browser.device_id, "every instance uses the same ID")
    for _, fn in ipairs(ticks) do fn() end
    return file_browser, shown
end

local KINDLE_A = { ["/proc/usid"] = "G000AA0000000001\n" }
local KINDLE_B = { ["/proc/usid"] = "G000AA0000000002" }
local kindle = { isKindle = function() return true end, isKobo = function() return false end, model = "KindlePaperWhite5" }

local function testACopiedKindleGetsItsOwnId()
    local first = newSettings()
    local a = loadPlugin(first, kindle, KINDLE_A)
    assert(a.device_id, "the first Kindle has an ID")

    -- The whole koreader folder is copied onto the second Kindle.
    local second = copyOf(first)
    local b, shown = loadPlugin(second, kindle, KINDLE_B)
    assert(b.device_id ~= a.device_id,
        "the second Kindle must not keep the first Kindle's ID: " .. tostring(b.device_id))
    assertEqual(second:readSetting("device_id"), b.device_id,
        "the new ID is saved, so KOReader's own sync uses it too")
    assertEqual(#shown, 1, "the reader is told once why the device changed")
    assert(tostring(shown[1].text):find("copied from another device", 1, true),
        "the notice says the settings came from another device")

    local again, shown_again = loadPlugin(second, kindle, KINDLE_B)
    assertEqual(again.device_id, b.device_id, "the second Kindle keeps its new ID after a restart")
    assertEqual(#shown_again, 0, "and is not told again")

    local a_again, shown_a = loadPlugin(first, kindle, KINDLE_A)
    assertEqual(a_again.device_id, a.device_id, "the first Kindle keeps its ID")
    assertEqual(#shown_a, 0, "the first Kindle shows nothing")
end

local function testTheSerialIsNotWrittenToSettings()
    local settings = newSettings()
    loadPlugin(settings, kindle, KINDLE_A)
    for key, value in pairs(settings.data) do
        assert(not tostring(value):find("G000AA", 1, true),
            "settings.reader.lua must not hold the serial (key " .. key .. ")")
    end
end

local function testAKoboIsToldApartByItsSerial()
    local kobo = { isKindle = function() return false end, isKobo = function() return true end, model = "Kobo_spaBW" }
    local first = newSettings()
    local a = loadPlugin(first, kobo, {
        ["/mnt/onboard/.kobo/version"] = "N4180A1111111,4.1.15,4.38.23038,4.1.15,4.1.15,00000000-0000-0000-0000-000000000393",
    })
    local second = copyOf(first)
    local b = loadPlugin(second, kobo, {
        ["/mnt/onboard/.kobo/version"] = "N4180A2222222,4.1.15,4.38.23038,4.1.15,4.1.15,00000000-0000-0000-0000-000000000393",
    })
    assert(b.device_id ~= a.device_id, "a Kobo set up from another Kobo's settings gets its own ID")

    -- A firmware update rewrites everything after the serial; the ID stays.
    local updated = loadPlugin(second, kobo, {
        ["/mnt/onboard/.kobo/version"] = "N4180A2222222,4.1.15,4.41.23145,4.1.15,4.1.15,00000000-0000-0000-0000-000000000393",
    })
    assertEqual(updated.device_id, b.device_id, "a firmware update is not a different device")
end

local function testWithoutASerialTheIdIsLeftAlone()
    local other = { isKindle = function() return false end, isKobo = function() return false end, model = "PocketBook" }
    local settings = newSettings({ device_id = "kept" })
    local plugin, shown = loadPlugin(settings, other, {})
    assertEqual(plugin.device_id, "kept", "an e-reader without a readable serial keeps its ID")
    assertEqual(#shown, 0, "and shows nothing")

    -- A blank read is not this Kindle's serial; recording it would make the
    -- next start, with the real serial, look like a different device.
    local empty = newSettings({ device_id = "kept" })
    local unreadable = loadPlugin(empty, kindle, { ["/proc/usid"] = "   " })
    assertEqual(unreadable.device_id, "kept", "a blank serial is not a serial")
    local later, later_shown = loadPlugin(empty, kindle, KINDLE_A)
    assertEqual(later.device_id, "kept", "reading the real serial later keeps the ID")
    assertEqual(#later_shown, 0, "and shows nothing")
end

-- Every Kindle already syncing when this plugin update arrives has an ID and
-- no hash yet. Almost all of them were never copied; they must stay the
-- device the server already knows.
local function testAnExistingKindleKeepsItsIdOnUpdate()
    local settings = newSettings({ device_id = "already-syncing" })
    local plugin, shown = loadPlugin(settings, kindle, KINDLE_A)
    assertEqual(plugin.device_id, "already-syncing", "updating the plugin must not re-key a Kindle")
    assertEqual(#shown, 0, "and must not tell it that it was copied")
end

local function testAFreshInstallStillGetsAnId()
    local settings = newSettings()
    local plugin = loadPlugin(settings, kindle, KINDLE_A)
    assert(type(plugin.device_id) == "string" and plugin.device_id ~= "",
        "a first start creates the ID, as before")
end

testACopiedKindleGetsItsOwnId()
testTheSerialIsNotWrittenToSettings()
testAKoboIsToldApartByItsSerial()
testWithoutASerialTheIdIsLeftAlone()
testAnExistingKindleKeepsItsIdOnUpdate()
testAFreshInstallStillGetsAnId()
print("device identity: all tests passed")

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

-- #2952: a Kindle's koreader folder restored onto a Tolino with a backup
-- plugin. KOReader runs there as its Android build, which exposes no serial
-- the plugin can read, so the Tolino kept the Kindle's ID and the server
-- showed both as "my Kindle". A settings file that arrives on a different
-- kind of e-reader is a copy, whether or not the new one has a serial.
local tolino = { isKindle = function() return false end, isKobo = function() return false end, model = "tolino_vision5" }

local function testAKindleSetupRestoredOnAnAndroidReaderGetsItsOwnId()
    local kindle_settings = newSettings()
    local k = loadPlugin(kindle_settings, kindle, KINDLE_A)

    local restored = copyOf(kindle_settings)
    local t, shown = loadPlugin(restored, tolino, {})
    assert(t.device_id ~= k.device_id,
        "the Tolino must not keep the Kindle's ID: " .. tostring(t.device_id))
    assertEqual(restored:readSetting("device_id"), t.device_id, "the new ID is saved")
    assertEqual(#shown, 1, "the reader is told once why the device changed")

    local again, shown_again = loadPlugin(restored, tolino, {})
    assertEqual(again.device_id, t.device_id, "the Tolino keeps its new ID after a restart")
    assertEqual(#shown_again, 0, "and is not told again")

    local k_again, shown_k = loadPlugin(kindle_settings, kindle, KINDLE_A)
    assertEqual(k_again.device_id, k.device_id, "the Kindle keeps its ID")
    assertEqual(#shown_k, 0, "the Kindle shows nothing")
end

-- The other way round: an Android reader's setup copied onto a Kindle.
local function testAnAndroidSetupCopiedOntoAKindleGetsItsOwnId()
    local tolino_settings = newSettings()
    local t = loadPlugin(tolino_settings, tolino, {})
    local copied = copyOf(tolino_settings)
    local k = loadPlugin(copied, kindle, KINDLE_A)
    assert(k.device_id ~= t.device_id, "the Kindle must not keep the Tolino's ID")
end

-- Android readers already syncing when this update arrives have an ID and no
-- recorded fingerprint, or have one for their own model. They stay the
-- device the server knows.
local function testAnAndroidReaderKeepsItsIdOnUpdate()
    local settings = newSettings({ device_id = "already-syncing" })
    local plugin, shown = loadPlugin(settings, tolino, {})
    assertEqual(plugin.device_id, "already-syncing", "updating the plugin must not re-key it")
    assertEqual(#shown, 0, "and must not tell it that it was copied")
    local again = loadPlugin(settings, tolino, {})
    assertEqual(again.device_id, "already-syncing", "nor on the next start")
end

-- A Kindle that cannot read its serial on one start is still that Kindle; it
-- must not be judged by its model name against a serial it recorded earlier.
local function testAKindleWithAnUnreadableSerialKeepsItsRecordedId()
    local settings = newSettings()
    local first = loadPlugin(settings, kindle, KINDLE_A)
    local blank, shown = loadPlugin(settings, kindle, {})
    assertEqual(blank.device_id, first.device_id, "an unreadable serial is not a different device")
    assertEqual(#shown, 0, "and shows nothing")
    local later = loadPlugin(settings, kindle, KINDLE_A)
    assertEqual(later.device_id, first.device_id, "the serial reads again: same device")
end

-- #2351: KOReader writes settings.reader.lua when it exits, not when a value
-- changes. A Kindle updated to a plugin with the fingerprint, then copied
-- before KOReader closed, gave the copy no fingerprint, so the second Kindle
-- kept the first one's ID and said nothing. What the plugin records about
-- this e-reader must be on disk as soon as it is recorded.
local function diskBacked(data)
    local settings = newSettings(data)
    settings.disk = {}
    function settings:flush()
        self.disk = {}
        for key, value in pairs(self.data) do self.disk[key] = value end
    end
    return settings
end

local function fileCopyOf(settings)
    local data = {}
    for key, value in pairs(settings.disk) do data[key] = value end
    return newSettings(data)
end

local function testACopyTakenBeforeKOReaderExitsCarriesTheFingerprint()
    -- A Kindle that was syncing before the fingerprint existed updates the plugin.
    local first = diskBacked({ device_id = "already-syncing" })
    first:flush()
    local a = loadPlugin(first, kindle, KINDLE_A)
    assertEqual(a.device_id, "already-syncing", "the updated Kindle keeps its ID")

    -- The file on disk is copied to a second Kindle while KOReader still runs.
    local second = fileCopyOf(first)
    local b = loadPlugin(second, kindle, KINDLE_B)
    assert(b.device_id ~= "already-syncing",
        "the copy must carry the fingerprint, so the second Kindle gets its own ID")
end

-- A platform that reports no model name says nothing about its hardware.
local function testAReaderWithNoModelNameKeepsItsId()
    local nameless = { isKindle = function() return false end, isKobo = function() return false end }
    local settings = newSettings({ device_id = "kept", cwngsync_hardware_fingerprint = "from-a-kindle" })
    local plugin, shown = loadPlugin(settings, nameless, {})
    assertEqual(plugin.device_id, "kept", "no model name: the ID is left alone")
    assertEqual(#shown, 0, "and nothing is shown")
end

testACopiedKindleGetsItsOwnId()
testTheSerialIsNotWrittenToSettings()
testAKoboIsToldApartByItsSerial()
testWithoutASerialTheIdIsLeftAlone()
testAnExistingKindleKeepsItsIdOnUpdate()
testAFreshInstallStillGetsAnId()
testAKindleSetupRestoredOnAnAndroidReaderGetsItsOwnId()
testAnAndroidSetupCopiedOntoAKindleGetsItsOwnId()
testAnAndroidReaderKeepsItsIdOnUpdate()
testAKindleWithAnUnreadableSerialKeepsItsRecordedId()
testACopyTakenBeforeKOReaderExitsCarriesTheFingerprint()
testAReaderWithNoModelNameKeepsItsId()
print("device identity: all tests passed")

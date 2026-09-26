-- Connecting from Tools ▸ CWNG library ▸ Connect this device.
--
-- The connection ends on the library home with a "Connected to ..." message.
-- Seen on a Kindle: the menu the connection was started from stayed on top of
-- both, still saying "Connect this device", so the reader could not tell that
-- it had worked. The production menu is loaded verbatim from main.lua into a
-- sandbox and its entry is tapped the way KOReader's TouchMenu does.

local function assertEqual(actual, expected, message)
    if actual ~= expected then
        error(string.format("%s\nexpected: %s\nactual: %s",
            message, tostring(expected), tostring(actual)), 2)
    end
end

local function loadAddToMainMenu(env)
    package.path = table.concat({ "../?.lua", "./?.lua", package.path }, ";")
    local main_path = assert(package.searchpath("main", package.path), "cannot locate main.lua")
    local file = assert(io.open(main_path, "r"))
    local source = file:read("*a")
    file:close()
    local start = assert(source:find("function CWNGSync:addToMainMenu(", 1, true), "addToMainMenu not found")
    local following = assert(source:find("\nfunction CWNGSync:", start + 1, true))
    local chunk = "local CWNGSync = {}\n" .. source:sub(start, following - 1) .. "\nreturn CWNGSync\n"
    return assert(load(chunk, "addToMainMenu", "t", env))()
end

local shown = {}
local env = setmetatable({
    _ = function(text) return text end,
    T = function(text, ...)
        local args = { ... }
        return (text:gsub("%%(%d)", function(n) return tostring(args[tonumber(n)]) end))
    end,
    hostOf = function(server) return server end,
    UIManager = { show = function(_, widget) shown[#shown + 1] = widget end },
    InfoMessage = { new = function(_, fields) return fields end },
}, { __index = _G })
local CWNGSync = loadAddToMainMenu(env)

local function newPlugin(configured)
    local events = {}
    local plugin = setmetatable({
        settings = { username = "kid", server = "https://books.example.com" },
        version = "test",
    }, { __index = CWNGSync })
    function plugin:isConfigured() return configured end
    function plugin:getAdvancedMenuItems() return {} end
    function plugin:showConnectChoices() events[#events + 1] = "connect choices" end
    local items = {}
    plugin:addToMainMenu(items)
    return items.cwng_progress_sync.sub_item_table[1], events
end

-- The TouchMenu passes itself to the callback, then closes itself unless the
-- entry says keep_menu_open.
local function tap(entry, events)
    local menu = { closeMenu = function() events[#events + 1] = "menu closed" end }
    entry.callback(menu)
    if not entry.keep_menu_open then menu.closeMenu() end
end

local function testConnectingLeavesTheMenuForTheLibrary()
    local entry, events = newPlugin(false)
    assertEqual(entry.text_func(), "Connect this device", "the entry a new device shows")
    tap(entry, events)
    assertEqual(table.concat(events, ", "), "menu closed, connect choices",
        "the menu must close before connecting, not stay up over the library")
end

local function testTheConnectedEntryExplainsAndStays()
    local entry, events = newPlugin(true)
    shown = {}
    tap(entry, events)
    assertEqual(#events, 0, "a connected device's entry neither closes the menu nor reconnects")
    assert(tostring(shown[1] and shown[1].text):find("Disconnect this device", 1, true),
        "it says how to change account")
end

testConnectingLeavesTheMenuForTheLibrary()
testTheConnectedEntryExplainsAndStays()
print("cwng_menu tests passed")

-- The library home's Reading tab against KOReader's own records for each
-- book (#2732): BookList's cached info, the sidecar, and the reading history.
-- KOReader's UI modules are inert stand-ins; the status logic is the shipped
-- cwng_home.lua and cwng_catalog.lua.
package.path = table.concat({ "../?.lua", "./?.lua", package.path }, ";")

local function assertEqual(actual, expected, message)
    if actual ~= expected then
        error(string.format("%s\nexpected: %s\nactual: %s",
            message, tostring(expected), tostring(actual)), 2)
    end
end

local inert
inert = setmetatable({}, {
    __index = function() return inert end,
    __call = function() return inert end,
})
local function class()
    local c = {}
    c.extend = function(base, o) o = o or {}; return setmetatable(o, { __index = base }) end
    return setmetatable(c, { __index = inert })
end

local ROOT = "/mnt/onboard/cwng-library"
local book_info, sidecars = {}, {}
local BookList = class()
BookList.getBookInfo = function(path) return book_info[path] or { been_opened = false } end
BookList.getBookStatus = function(path) -- as KOReader's BookList.getBookStatus
    local info = BookList.getBookInfo(path)
    return info.been_opened and info.status or "new"
end
local DocSettings = class()
local opens = {}
DocSettings.open = function(_, path)
    opens[path] = (opens[path] or 0) + 1
    local data = sidecars[path] or {}
    return {
        readSetting = function(_, key) return data[key] end,
        isTrue = function(_, key) return data[key] == true end,
    }
end
local ReadHistory = { hist = {} }

local stubs = {
    ["ui/widget/booklist"] = BookList,
    ["docsettings"] = DocSettings,
    ["readhistory"] = ReadHistory,
}
for _, name in ipairs({ "ui/widget/verticalgroup", "ui/widget/horizontalgroup", "ui/widget/menu",
        "ui/widget/button", "ui/widget/buttondialog", "ui/widget/titlebar", "ui/widget/linewidget",
        "ui/widget/inputdialog", "ui/widget/infomessage" }) do
    stubs[name] = class()
end
for name, module in pairs(stubs) do package.loaded[name] = module end
for _, name in ipairs({ "ui/bidi", "ffi/blitbuffer", "device", "document/documentregistry",
        "ui/geometry", "ui/size", "ui/uimanager", "apps/filemanager/filemanagerutil",
        "libs/libkoreader-lfs", "logger", "util", "ffi/util" }) do
    package.loaded[name] = package.loaded[name] or inert
end

-- Any other KOReader module the home screen pulls in is an inert stand-in.
table.insert(package.searchers or package.loaders, function(name)
    if name:match("^cwng_") then return nil end
    return function() return inert end
end)

local Home = require("cwng_home")

local function book(id, title, read_status)
    return { book_id = id, title = title, path = ROOT .. "/" .. title .. ".epub", read_status = read_status }
end

local function reading(entries)
    local home = { entries = function() return entries end,
                   plugin = { getLibraryRoot = function() return ROOT end } }
    local out = {}
    for i, e in ipairs(Home.readingNow(home)) do out[i] = e.title end
    return table.concat(out, "|")
end

local function testResetBookLeavesReadingAndAReadBookStays()
    local reset = book(1, "Reset", "unread")
    local read_here = book(2, "Read here", "unread")
    local no_footer = book(3, "No footer", "unread")
    local deleted = book(4, "Deleted", "unread")
    ReadHistory.hist = { { file = reset.path }, { file = read_here.path }, { file = no_footer.path },
                         { file = deleted.path } }
    -- KOReader's BookList says "reading" for all three opened books.
    book_info[reset.path] = { been_opened = true, status = "reading" }
    book_info[read_here.path] = { been_opened = true, status = "reading", percent_finished = 0.3 }
    book_info[no_footer.path] = { been_opened = true, status = "reading" }
    -- Reset left the sidecar without its summary; the others were opened and saved one.
    sidecars[reset.path] = { last_page = 12, docsettings_reset_done = true }
    sidecars[read_here.path] = { summary = { status = "reading" }, percent_finished = 0.3 }
    sidecars[no_footer.path] = { summary = { status = "reading" }, last_page = 42 }
    assertEqual(reading({ reset, read_here, no_footer, deleted }), "Read here|No footer",
        "a reset or deleted unread book leaves Reading; one read here stays, status bar or not")

    assertEqual(opens[read_here.path], 1, "one Reading build reads a sidecar once")

    reset.read_status = "reading"
    assertEqual(reading({ reset, read_here, no_footer, deleted }), "Reset|Read here|No footer",
        "a reset book the server says is being read stays")
end

testResetBookLeavesReadingAndAReadBookStays()
print("cwng_home_reading_test.lua: all tests passed")

-- One swipe in the library home turns one page (#2730).
-- Plugins such as Simple UI wrap a full-screen widget's handleEvent: they run
-- the widget's gesture handlers first and, unless that call reports the
-- gesture as handled, pass the same event on to the widget's normal handler,
-- which runs the gesture handlers again. KOReader's Menu:onSwipe returns
-- nothing, so a home that only delegates to it turned two pages per swipe.
-- KOReader's UI modules are inert stand-ins; Home is the shipped cwng_home.lua.
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

-- KOReader's Menu:onSwipe, as shipped: it turns the page and returns nothing.
local BookList = class()
function BookList:onSwipe(_, ges_ev)
    if ges_ev.direction == "west" then self:onNextPage()
    elseif ges_ev.direction == "east" then self:onPrevPage() end
end

local stubs = {
    ["ui/widget/booklist"] = BookList,
    ["ui/bidi"] = { flipDirectionIfMirroredUILayout = function(d) return d end },
}
for _, name in ipairs({ "ui/widget/verticalgroup", "ui/widget/horizontalgroup", "ui/widget/menu",
        "ui/widget/button", "ui/widget/buttondialog", "ui/widget/titlebar", "ui/widget/linewidget",
        "ui/widget/inputdialog", "ui/widget/infomessage" }) do
    stubs[name] = class()
end
for name, module in pairs(stubs) do package.loaded[name] = module end
for _, name in ipairs({ "ffi/blitbuffer", "device", "document/documentregistry", "docsettings",
        "readhistory", "ui/geometry", "ui/size", "ui/uimanager", "apps/filemanager/filemanagerutil",
        "libs/libkoreader-lfs", "logger", "util", "ffi/util" }) do
    package.loaded[name] = package.loaded[name] or inert
end
table.insert(package.searchers or package.loaders, function(name)
    if name:match("^cwng_") then return nil end
    return function() return inert end
end)

local Home = require("cwng_home")

local function newHome()
    local home = setmetatable({ page = 1, header = { getHeight = function() return 40 end } },
        { __index = Home })
    home.onNextPage = function(self) self.page = self.page + 1 end
    home.onPrevPage = function(self) self.page = self.page - 1 end
    return home
end

-- Simple UI's gesture-priority wrapper (infra/sui_core.lua): gesture handlers
-- first, then the inherited handleEvent, which dispatches the gesture again.
local function deliverThroughWrapper(home, ges)
    if home:onSwipe(nil, ges) then return true end
    return home:onSwipe(nil, ges)
end

local home = newHome()
deliverThroughWrapper(home, { direction = "west", pos = { y = 400 } })
assertEqual(home.page, 2, "one swipe left turns exactly one page forward")
deliverThroughWrapper(home, { direction = "east", pos = { y = 400 } })
assertEqual(home.page, 1, "one swipe right turns exactly one page back")

home = newHome()
assertEqual(home:onSwipe(nil, { direction = "west", pos = { y = 400 } }), true,
    "a page-turn swipe is reported as handled")

print("cwng_home_swipe_test: 3 passed")

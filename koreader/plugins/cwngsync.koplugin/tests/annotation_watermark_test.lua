-- A highlight drawn from the server and deleted on the device stays deleted.
--
-- CWNGSync:syncAnnotations and the watermark functions are loaded verbatim
-- from main.lua and run against the real native provider and SyncLogic, with
-- a fake server and a fake reader. The watermark (ids known to both sides) is
-- what turns "missing here" into "deleted here"; a highlight drawn from the
-- server has to be in it from the moment it is drawn, or deleting it before
-- the next push goes unnoticed and the next open draws it again.

package.path = table.concat({ "../?.lua", "./?.lua", package.path }, ";")
package.preload["json"] = function()
    return { encode = function(v) return tostring(v[1]) .. tostring(v[3]) end }
end
package.preload["ffi/sha2"] = function() return { md5 = function(s) return "md5(" .. s .. ")" end } end
package.preload["ui/event"] = function() return { new = function(_, n, a) return { name = n, args = a } end } end
package.preload["ui/uimanager"] = function() return { setDirty = function() end, show = function() end } end
package.loaded["kobo_sqlite_provider"] = { available = function() return false end }

local Native = require("koreader_annotations_provider")
local SyncLogic = require("sync_logic")

local function assertEqual(actual, expected, message)
    if actual ~= expected then
        error(string.format("%s\nexpected: %s\nactual: %s",
            message, tostring(expected), tostring(actual)), 2)
    end
end

local function mainFunctions(names)
    local file = assert(io.open(assert(package.searchpath("main", package.path)), "r"))
    local source = file:read("*a")
    file:close()
    local key = assert(source:match('local ANNOTATION_WATERMARK_KEY = "([^"]+)"'))
    local bodies = {}
    for _, name in ipairs(names) do
        local header = "function CWNGSync:" .. name .. "("
        local start = assert(source:find(header, 1, true), name .. " not found in main.lua")
        local following = source:find("\nfunction ", start + 1, true) or #source
        bodies[#bodies + 1] = source:sub(start, following - 1)
    end
    local chunk = table.concat({
        "local CWNGSync = {}",
        "local ANNOTATION_WATERMARK_KEY = " .. string.format("%q", key),
        "local SyncLogic, UIManager, InfoMessage = ...",
        "local T = function(text) return text end",
        "local _ = function(text) return text end",
        "local function promptLogin() end",
        "local function showSyncError() end",
        "local function showNoBookMessage() end",
        "local function ensureServerConfigured() return true end",
        table.concat(bodies, "\n"),
        "return CWNGSync",
    }, "\n")
    return assert(load(chunk, "main.lua functions", "t", _ENV))(
        SyncLogic, { show = function() end }, { new = function(_, fields) return fields end }), key
end

local START, END = "/body/DocFragment[3]/body/p[4]/text().0", "/body/DocFragment[3]/body/p[4]/text().24"
local WORDS = "One morning, when Gregor"

local function newReader()
    local ui = { settings = {}, rolling = {}, dialog = {} }
    ui.annotation = { annotations = {} }
    function ui.annotation:addItem(item)
        table.insert(self.annotations, item)
        return #self.annotations
    end
    ui.bookmark = { removeItemByIndex = function(_, index) table.remove(ui.annotation.annotations, index) end }
    ui.document = {
        isXPointerInDocument = function() return true end,
        getTextFromXPointers = function() return WORDS end,
    }
    ui.view = { highlight = {} }
    ui.doc_settings = {
        readSetting = function(_, k) return ui.settings[k] end,
        saveSetting = function(_, k, v) ui.settings[k] = v end,
    }
    function ui:handleEvent() end
    return ui
end

local function newHarness(push_succeeds, own_highlights)
    local CWNGSync, key = mainFunctions({
        "readAnnotationWatermark", "saveAnnotationWatermark", "addToAnnotationWatermark", "syncAnnotations",
    })
    local ui = newReader()
    for _, item in ipairs(own_highlights or {}) do table.insert(ui.annotation.annotations, item) end
    local server = { { annotation_id = "web-1", highlighted_text = WORDS, start_xpointer = START,
        end_xpointer = END, hidden = false, last_synced = "2026-09-23T23:00:00+00:00", color = "green" } }
    local pushes = {}
    local client = {
        pull_annotations = function(_, _, _, _, callback) callback(true, { annotations = server }) end,
        push_annotations = function(_, _, _, _, list, deleted, callback)
            pushes[#pushes + 1] = { list = list, deleted = deleted }
            callback(push_succeeds, {}, push_succeeds and nil or "offline")
        end,
    }
    local plugin = setmetatable({
        ui = ui,
        settings = { sync_annotations = true, username = "reader", password = "app", server = "http://books" },
        path = ".",
        hasCurrentDocument = function() return true end,
        getDocumentDigest = function() return "digest" end,
        getCurrentDocumentFile = function() return "/books/book.epub" end,
        refreshLibraryViews = function() end,
    }, { __index = CWNGSync })
    local real_require = require
    require = function(name)
        if name == "device_annotations" then
            return { getProvider = function(reader)
                Native.setContext(reader, "digest")
                return Native
            end }
        elseif name == "CWNGSyncClient" then
            return { new = function() return client end }
        end
        return real_require(name)
    end
    local function open()
        local ok, err = pcall(plugin.syncAnnotations, plugin, false)
        if not ok then
            require = real_require
            error(err, 2)
        end
    end
    local function done() require = real_require end
    return { ui = ui, open = open, done = done, pushes = pushes, key = key }
end

local function deleteOnDevice(ui, id)
    for index, item in ipairs(ui.annotation.annotations) do
        if item.cwng_id == id then
            ui.bookmark:removeItemByIndex(index)
            return
        end
    end
    error("no highlight " .. id .. " on the device")
end

local function testAServerHighlightDeletedHereBeforeAnyPushStaysDeleted()
    local h = newHarness(true)
    h.open()
    assertEqual(#h.ui.annotation.annotations, 1, "the server's highlight is drawn")
    deleteOnDevice(h.ui, "web-1")
    h.open()
    h.done()
    assertEqual(#h.ui.annotation.annotations, 0, "the highlight deleted here must not come back")
    local last = h.pushes[#h.pushes]
    assertEqual(last and last.deleted[1], "web-1", "the deletion is named to the server")
end

local function testADeletionSurvivesAPushThatFailed()
    local h = newHarness(false)
    h.open()
    deleteOnDevice(h.ui, "web-1")
    h.open() -- names the deletion, but the push fails
    h.open() -- still named, and still not drawn again
    h.done()
    assertEqual(#h.ui.annotation.annotations, 0, "the highlight deleted here must not come back")
    assertEqual(h.pushes[#h.pushes].deleted[1], "web-1", "the deletion stays pending until the server has it")
end

local function testItStaysDeletedWhenTheReaderHasHighlightsOfTheirOwn()
    -- Their own highlight is pushed on open, which rewrites the watermark.
    local own = { page = "/body/DocFragment[5]/body/p[2]/text().0", pos0 = "/body/DocFragment[5]/body/p[2]/text().0",
        pos1 = "/body/DocFragment[5]/body/p[2]/text().9", text = "Mine here", datetime = "2026-09-20 10:00:00",
        drawer = "lighten", color = "yellow" }
    local h = newHarness(true, { own })
    h.open()
    assertEqual(#h.ui.annotation.annotations, 2, "both highlights are on the device")
    deleteOnDevice(h.ui, "web-1")
    h.open()
    h.done()
    assertEqual(#h.ui.annotation.annotations, 1, "only the reader's own highlight remains")
    assertEqual(h.ui.annotation.annotations[1].text, "Mine here", "and it is theirs")
end

testAServerHighlightDeletedHereBeforeAnyPushStaysDeleted()
testADeletionSurvivesAPushThatFailed()
testItStaysDeletedWhenTheReaderHasHighlightsOfTheirOwn()

print("annotation_watermark tests passed")

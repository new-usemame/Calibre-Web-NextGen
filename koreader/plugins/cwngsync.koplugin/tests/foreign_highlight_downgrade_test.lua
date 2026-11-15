-- Independent review regression: persisted foreign proof survives an older server.
-- Harness adapted from the separately written fixed cleanup suite.
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

local passed = 0

local function assertEqual(actual, expected, message)
    if actual ~= expected then
        error(string.format("%s\nexpected: %s\nactual: %s",
            message, tostring(expected), tostring(actual)), 2)
    end
end

-- syncAnnotations plus each CWNGSync method it reaches through `self:` that
-- main.lua defines. Methods main.lua does not define (or the harness supplies
-- below) come from the fake plugin object.
local function mainFunctions(roots)
    local file = assert(io.open(assert(package.searchpath("main", package.path)), "r"))
    local source = file:read("*a")
    file:close()
    local key = assert(source:match('local ANNOTATION_WATERMARK_KEY = "([^"]+)"'))
    local supplied = {
        hasCurrentDocument = true, getDocumentDigest = true, getCurrentDocumentFile = true,
        refreshLibraryViews = true, recordOpenedAnnotations = true,
    }
    local bodies, seen, queue = {}, {}, {}
    for _, name in ipairs(roots) do queue[#queue + 1] = name end
    while #queue > 0 do
        local name = table.remove(queue, 1)
        if not seen[name] and not supplied[name] then
            seen[name] = true
            local header = "function CWNGSync:" .. name .. "("
            local start = source:find(header, 1, true)
            if start then
                local following = source:find("\nfunction ", start + 1, true) or #source
                local body = source:sub(start, following - 1)
                bodies[#bodies + 1] = body
                for called in body:gmatch("self:([%w_]+)%(") do queue[#queue + 1] = called end
            else
                assert(name ~= roots[1], name .. " not found in main.lua")
            end
        end
    end
    local chunk = table.concat({
        "local CWNGSync = {}",
        "local ANNOTATION_WATERMARK_KEY = " .. string.format("%q", key),
        "local SyncLogic, UIManager, InfoMessage = ...",
        "local T = function(text) return text end",
        "local Device = { model = 'Kindle' }",
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

local WORDS = "One morning, when Gregor"

local function xp(fragment, from, to)
    local base = "/body/DocFragment[" .. fragment .. "]/body/p[1]/text()"
    return base .. "." .. from, base .. "." .. to
end

-- A highlight this device received from the server at some point.
local function received(id, fragment)
    local pos0, pos1 = xp(fragment, 0, 24)
    return { page = pos0, pos0 = pos0, pos1 = pos1, text = WORDS, drawer = "lighten",
        color = "yellow", cwng_id = id, cwng_base = { color = "yellow", drawn_color = "yellow" } }
end

-- A highlight the reader made on this device.
local function made_here(fragment)
    local pos0, pos1 = xp(fragment, 0, 9)
    return { page = pos0, pos0 = pos0, pos1 = pos1, text = "Mine here",
        datetime = "2026-09-20 10:00:00", drawer = "lighten", color = "yellow" }
end

-- The id the native provider gives a highlight made here (the stubbed md5 of
-- the stubbed JSON of its identity: document digest and page).
local function native_id(item)
    return "koreader-md5(digest" .. item.page .. ")"
end

local function newHarness(opts)
    local CWNGSync, key = mainFunctions({ "syncAnnotations" })
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
    for _, item in ipairs(opts.local_items) do table.insert(ui.annotation.annotations, item) end
    -- Everything already on the device was pushed by an earlier sync.
    local watermark = {}
    for _, item in ipairs(opts.local_items) do
        watermark[#watermark + 1] = item.cwng_id or native_id(item)
    end
    ui.settings[key] = watermark

    local pushes, pulls = {}, 0
    local client = {
        pull_annotations = function(_, user, password, document, device, device_id, callback)
            assert(document == "digest" and device_id == "this-kindle", "pull names this book and device")
            pulls = pulls + 1
            local reply = opts.reply
            if type(reply) == "function" then reply = reply(pulls) end
            if reply == false then
                callback(false, nil)
            else
                callback(true, reply)
            end
        end,
        push_annotations = function(_, _, _, _, list, deleted, device, device_id, callback)
            pushes[#pushes + 1] = { list = list, deleted = deleted }
            callback(true, {}, nil)
        end,
    }
    local plugin = setmetatable({
        ui = ui,
        settings = { sync_annotations = true, username = "reader", password = "app", server = "http://192.0.2.10" },
        path = ".",
        device_id = "this-kindle",
        hasCurrentDocument = function() return true end,
        getDocumentDigest = function() return "digest" end,
        getCurrentDocumentFile = function() return "/books/book.epub" end,
        refreshLibraryViews = function() end,
        recordOpenedAnnotations = function() end,
    }, { __index = CWNGSync })
    local real_require = require
    local function open()
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
        local ok, err = pcall(plugin.syncAnnotations, plugin, false)
        require = real_require
        if not ok then error(err, 2) end
    end
    local function ids()
        local present = {}
        for _, item in ipairs(ui.annotation.annotations) do
            present[item.cwng_id or native_id(item)] = true
        end
        return present
    end
    return { ui = ui, open = open, pushes = pushes, ids = ids, key = key }
end

local function contains(list, id)
    for _, value in ipairs(list or {}) do
        local candidate = type(value) == "table" and value.annotation_id or value
        if candidate == id then return true end
    end
    return false
end

local function assertNeverSentBack(h, id)
    for index, push in ipairs(h.pushes) do
        assertEqual(contains(push.deleted, id), false,
            "push " .. index .. " must not name " .. id .. " as deleted here")
        assertEqual(contains(push.list, id), false,
            "push " .. index .. " must not send " .. id .. " back to the server")
    end
end

-- The book this device kept: one highlight a Kobo made (drawn here before
-- device-only highlights), one with no recorded origin, one this device made
-- and later received back with its server id, and one made here and never
-- given a server id.
local function keptBook()
    local mine = made_here(9)
    return {
        kobo = received("kobo-7", 3),
        legacy = received("legacy-1", 4),
        own = received("kindle-own-1", 5),
        mine = mine,
        all = function(self) return { self.kobo, self.legacy, self.own, self.mine } end,
    }
end

local function ownRow()
    local pos0, pos1 = xp(5, 0, 24)
    return { annotation_id = "kindle-own-1", highlighted_text = WORDS, start_xpointer = pos0,
        end_xpointer = pos1, hidden = false, last_synced = "2026-09-23T23:00:00+00:00", color = "yellow" }
end

local book = keptBook()
local h = newHarness({ local_items = book:all(), reply = function(pull)
    if pull == 1 then return { annotations={ownRow()}, foreign_annotation_ids={'kobo-7'} } end
    local older = ownRow()
    older.annotation_id='kobo-7'
    older.start_xpointer, older.end_xpointer=xp(3,0,24)
    return { annotations={ownRow(), older} }
end })
h.ui.settings.cwngsync_dismissed_annotation_ids={}
h.open()
h.open()
h.open()
assertNeverSentBack(h,'kobo-7')
print('PASS server downgrade cannot revive foreign upload')
print("foreign_highlight_downgrade tests passed (1)")

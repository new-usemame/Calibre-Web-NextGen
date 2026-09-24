-- Behavioural coverage for Runtime:syncLibrary's paging.
--
-- The production runtime is loaded with KOReader's modules stubbed and a fake
-- server whose answers arrive later, the way AsyncHTTP delivers them on a
-- device. What matters is which book list reaches applyLibraryManifest: a list
-- cut short would make every book past the cut look as if it had left the
-- library, and the plan removes those books from the device.

package.path = table.concat({
    "../?.lua",
    "./?.lua",
    package.path,
}, ";")

local function stub(name, value)
    package.loaded[name] = value
end

-- The reader's sidecars, by book path: { data = {...}, custom_cover = bool }.
local sidecars, sidecars_deleted = {}, {}
local reader = { instance = nil }

stub("ui/widget/booklist", { resetBookInfoCache = function() end })
stub("datastorage", { getSettingsDir = function() return "/tmp" end, getDataDir = function() return "/tmp" end })
stub("device", { model = "Kindle" })
stub("docsettings", {
    hasSidecarFile = function(_, path) return sidecars[path] ~= nil end,
    findCustomCoverFile = function(_, path) return sidecars[path] and sidecars[path].custom_cover or nil end,
    findCustomMetadataFile = function() return nil end,
    open = function(_, path)
        local data = {}
        for k, v in pairs((sidecars[path] or {}).data or {}) do data[k] = v end
        data.doc_path = path
        return { data = data }
    end,
    updateLocation = function(path, new_path)
        if new_path == nil then
            sidecars_deleted[path] = true
            sidecars[path] = nil
        else
            sidecars[new_path], sidecars[path] = sidecars[path], nil
        end
    end,
})
stub("apps/reader/readerui", reader)
stub("ui/widget/infomessage", { new = function(_, fields) return fields end })
stub("json", {})
stub("luasettings", {})
stub("ui/network/manager", { isConnected = function() return true end })
stub("ui/uimanager", { show = function() end })
stub("libs/libkoreader-lfs", { attributes = function(path, field)
    local f = io.open(path, "rb")
    if not f then return nil end
    local size = f:seek("end")
    f:close()
    local attributes = { mode = "file", size = size, modification = 0 }
    if field then return attributes[field] end
    return attributes
end })
stub("logger", { warn = function() end, info = function() end, dbg = function() end })
stub("util", { directoryExists = function() return true end, makePath = function() end })
stub("ffi/util", { template = function(text) return text end })
stub("gettext", function(text) return text end)

local Runtime = require("cwng_library_runtime")

local function assertEqual(actual, expected, message)
    if actual ~= expected then
        error(string.format("%s\nexpected: %s\nactual: %s",
            message, tostring(expected), tostring(actual)), 2)
    end
end

-- `answer(cursor)` is the server: the body for the page after `cursor`.
local function sync(answer)
    local queue, requests = {}, {}
    local outcome = { applied = nil }
    local client = {
        get_library = function(_, _, _, _, _, cursor, _, callback)
            requests[#requests + 1] = cursor or "first"
            queue[#queue + 1] = function() callback(true, answer(cursor)) end
        end,
    }
    local runtime = setmetatable({
        settings = { username = "reader", password = "secret" },
        device_id = "device",
    }, { __index = Runtime })
    function runtime:libraryEnabled() return true end
    function runtime:getLibraryRoot() return "/mnt/us/cwng-library" end
    function runtime:getLibraryState() return { books = {} } end
    function runtime:newSyncClient() return client end
    function runtime:accountOwner() return "reader@server" end
    function runtime:libraryProbe() return {} end
    function runtime:applyLibraryManifest(books, _, _, _, done)
        outcome.applied = books
        done(true)
    end
    runtime:syncLibrary({
        force = true,
        on_done = function(ok, summary) outcome.ok, outcome.summary = ok, summary end,
    })
    while #queue > 0 do table.remove(queue, 1)() end
    outcome.requests = requests
    return outcome
end

local function pagesOf(total_pages, per_page)
    return function(cursor)
        local page = (cursor or 0) + 1
        local books = {}
        for i = 1, per_page do
            books[i] = { book_id = (page - 1) * per_page + i }
        end
        return { books = books, next_cursor = page < total_pages and page or nil, revision = "r1" }
    end
end

-- Real files in a scratch folder, so os.remove and os.rename really run.
local folder = os.tmpname()
os.remove(folder)
assert(os.execute("mkdir -p '" .. folder .. "'"))
local placeholders = {}
Runtime.readPlaceholderId = function(path) return placeholders[path] end

local function put(name, placeholder_of)
    local path = folder .. "/" .. name
    local f = assert(io.open(path, "wb"))
    f:write(placeholder_of and "cover" or "the book")
    f:close()
    placeholders[path] = placeholder_of
    return path
end

local function exists(path)
    local f = io.open(path, "rb")
    if f then f:close() end
    return f ~= nil
end

local function newRuntime()
    local runtime = setmetatable({}, { __index = Runtime })
    function runtime:getDocumentDigest() return "md5:the book" end
    function runtime:fetchPlaceholder() error("a placeholder was fetched over the book") end
    function runtime:getLibraryState() return { books = {} } end
    return runtime
end

local function testRemovingACoverKeepsTheReadersNotesButNotAStaleStatus()
    local runtime = newRuntime()
    local status_only = put("Status Only [1].epub", 1)
    sidecars[status_only] = { data = { summary = { status = "complete" }, percent_finished = 1 } }
    local noted = put("Noted [2].epub", 2)
    sidecars[noted] = { data = { summary = { status = "reading" }, annotations = { { text = "mine" } },
        last_xpointer = "/body/DocFragment[3]" } }

    assertEqual(runtime:performLibraryAction(nil, { op = "remove_placeholder", book_id = 1, path = status_only }),
        true, "a cover leaving the library is removed")
    assertEqual(sidecars_deleted[status_only], true, "a status only a sync wrote goes with it")

    assertEqual(runtime:performLibraryAction(nil, { op = "remove_placeholder", book_id = 2, path = noted }),
        true, "a cover with notes is removed too")
    assertEqual(exists(noted), false, "the cover file is gone")
    assertEqual(sidecars_deleted[noted], nil, "the reader's notes and position stay")
end

local function testAStepDoesNothingToABookChangedSinceThePlan()
    local runtime = newRuntime()
    -- Opened since the plan.
    local open_cover = put("Open Cover [3].epub", 3)
    local open_book = put("Open Book [4].epub")
    reader.instance = { document = { file = open_cover } }
    assertEqual(runtime:performLibraryAction(nil, { op = "remove_placeholder", book_id = 3, path = open_cover }),
        false, "a cover open in the reader is not removed")
    reader.instance = { document = { file = open_book } }
    assertEqual(runtime:performLibraryAction(nil, { op = "remove_download", book_id = 4, path = open_book,
        checksum = "md5:the book" }), false, "a book open in the reader is not removed")
    local moved_to = folder .. "/Renamed [4].epub"
    assertEqual(runtime:performLibraryAction(nil, { op = "move_download", book_id = 4, from = open_book,
        path = moved_to }), false, "nor moved")
    assert(exists(open_cover) and exists(open_book) and not exists(moved_to), "every file is where it was")
    reader.instance = nil

    -- Downloaded over its cover since the plan: it is the book now.
    local became_book = put("Tapped [5].epub")
    assertEqual(runtime:performLibraryAction(nil, { op = "remove_placeholder", book_id = 5, path = became_book }),
        false, "a cover that became the book is not removed")
    assertEqual(runtime:performLibraryAction(nil, { op = "refresh_placeholder", book_id = 5, path = became_book,
        book = { book_id = 5 } }), false, "nor replaced by a new cover")
    assert(exists(became_book), "the downloaded book is still there")
end

local function testEveryPageOfABigLibraryReachesTheDevice()
    -- 250 pages is 50,000 books at the server's page size of 200.
    local outcome = sync(pagesOf(250, 2))
    assertEqual(outcome.ok, true, "a long library must sync")
    assertEqual(#outcome.applied, 500, "every book of every page must be applied")
    assertEqual(outcome.applied[500].book_id, 500, "the last page must be there")
end

local function testAListThatDoesNotFinishIsNotApplied()
    -- Past the runtime's guard of 5000 pages; bounded only so that a runtime
    -- without the guard fails here instead of looping forever.
    local outcome = sync(pagesOf(6000, 1))
    assertEqual(outcome.applied, nil, "a list cut short must never be applied")
    assertEqual(outcome.ok, false, "the sync must fail")
    assert(type(outcome.summary) == "string" and outcome.summary ~= "", "the failure must say why")
    -- The failed sync let go: the next one runs.
    local again = sync(pagesOf(1, 1))
    assertEqual(again.ok, true, "a later sync must run after a failed one")
end

local function testAServerRepeatingItsCursorStopsAtOnce()
    local outcome = sync(function()
        return { books = { { book_id = 1 } }, next_cursor = "same", revision = "r1" }
    end)
    assertEqual(outcome.applied, nil, "a list that does not advance must never be applied")
    assertEqual(outcome.ok, false, "the sync must fail")
    assertEqual(#outcome.requests, 2, "the repeat must stop the sync, not run it to the page limit")
end

testEveryPageOfABigLibraryReachesTheDevice()
testAListThatDoesNotFinishIsNotApplied()
testAServerRepeatingItsCursorStopsAtOnce()
testRemovingACoverKeepsTheReadersNotesButNotAStaleStatus()
testAStepDoesNothingToABookChangedSinceThePlan()
os.execute("rm -rf '" .. folder .. "'")

print("cwng_library_runtime tests passed")

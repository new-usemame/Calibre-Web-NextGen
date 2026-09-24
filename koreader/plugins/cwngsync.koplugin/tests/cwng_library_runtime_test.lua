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
-- KOReader's settings files: each flush keeps a copy of what it wrote, and a
-- restart reads only those copies.
local settings_files = {}
local function copy(value)
    if type(value) ~= "table" then return value end
    local out = {}
    for k, v in pairs(value) do out[k] = copy(v) end
    return out
end
stub("luasettings", { open = function(_, path)
    local file = settings_files[path] or { written = {}, flushes = 0 }
    settings_files[path] = file
    local data = copy(file.written)
    return {
        readSetting = function(_, key) return data[key] end,
        saveSetting = function(_, key, value) data[key] = value end,
        flush = function()
            file.flushes = file.flushes + 1
            file.written = copy(data)
        end,
    }
end })
local network = { up = true }
stub("ui/network/manager", { isConnected = function() return network.up end })
local ticks, shown = {}, {}
stub("ui/uimanager", {
    show = function(_, widget) shown[#shown + 1] = widget.text end,
    nextTick = function(_, f) ticks[#ticks + 1] = f end,
})
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
stub("ffi/util", { template = function(text, ...)
    local args = { ... }
    return (text:gsub("%%(%d)", function(n) return tostring(args[tonumber(n)]) end))
end })
stub("gettext", function(text) return text end)

local Runtime = require("cwng_library_runtime")

local function assertEqual(actual, expected, message)
    if actual ~= expected then
        error(string.format("%s\nexpected: %s\nactual: %s",
            message, tostring(expected), tostring(actual)), 2)
    end
end

-- `answer(cursor)` is the server: the body for the page after `cursor`.
-- With `real_apply`, the manifest is applied by the runtime itself, and
-- `fetch(book)` answers each cover download; a table `{ download = f }`
-- instead answers the runtime's own download as the client would,
-- `f(book_id, temp)`.
local function sync(answer, real_apply, fetch, root, broken)
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
    function runtime:getLibraryRoot() return root or "/mnt/us/cwng-library" end
    if real_apply ~= "persist" then
        function runtime:getLibraryState() return { books = {} } end
    end
    function runtime:newSyncClient() return client end
    function runtime:accountOwner() return "reader@server" end
    function runtime:libraryProbe() return {} end
    if real_apply then
        local state = { books = {} }
        outcome.fetched = 0
        if real_apply ~= "persist" then
            function runtime:getLibraryState() return state end
            function runtime:saveLibraryState() end
        end
        function runtime:refreshLibraryViews() if broken == "views" then error("views broke") end end
        function runtime:applyLibraryCollections() end
        function runtime:libraryProbe()
            return { attributes = function() return nil end, isOpen = function() return false end,
                digest = function() return nil end, placeholderId = function() return nil end }
        end
        if type(fetch) == "table" then
            function client:download_file(_, _, _, _, url_path, temp)
                outcome.fetched = outcome.fetched + 1
                return fetch.download(tonumber(url_path:match("/books/(%d+)/")), temp)
            end
        else
            function runtime:fetchPlaceholder(_, book)
                outcome.fetched = outcome.fetched + 1
                return fetch(book)
            end
        end
        outcome.state = state
        outcome.runtime = runtime
    else
        function runtime:applyLibraryManifest(books, _, _, _, done)
            if broken == "apply" then error("plan broke") end
            outcome.applied = books
            done(true)
        end
    end
    runtime:syncLibrary({
        force = true,
        interactive = real_apply,
        on_done = function(ok, summary) outcome.ok, outcome.summary = ok, summary end,
    })
    while #queue > 0 or #ticks > 0 do
        if #queue > 0 then table.remove(queue, 1)() else table.remove(ticks, 1)() end
    end
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

local function manyNewBooks(count)
    return function()
        local books = {}
        for id = 1, count do
            books[id] = { book_id = id, filename = "Book [" .. id .. "].epub", rev = "r1", read_status = "unread" }
        end
        return { books = books, revision = "r1" }
    end
end

local function testASyncStopsWhenTheServerStopsAnswering()
    local outcome = sync(manyNewBooks(2000), true, { download = function() return false, nil, nil, "timeout" end })
    assertEqual(outcome.fetched, 3, "three downloads fail in a row, then the sync stops")
    assertEqual(outcome.ok, false, "and says it did not finish")
    assertEqual(shown[#shown]:find("up to date", 1, true), nil, "it never says the library is up to date")
    -- A proxy in front of a server that is down answers for it.
    outcome = sync(manyNewBooks(2000), true, { download = function() return false, nil, nil, "HTTP 502", 502 end })
    assertEqual(outcome.fetched, 3, "a gateway error is the server not answering")
end

local function testBooksTheServerRefusesDoNotStopTheRest()
    -- A book with no format this e-reader opens is refused on every sync. The
    -- server is answering: the books after it must still arrive.
    local root = folder .. "/refused"
    assert(os.execute("mkdir -p '" .. root .. "'"))
    local outcome = sync(manyNewBooks(20), true, { download = function(book_id, temp)
        if book_id <= 5 then return false, nil, nil, "HTTP 404", 404 end
        local f = assert(io.open(temp, "wb"))
        f:write("cover")
        f:close()
        placeholders[temp] = book_id
        return true
    end }, root)
    assertEqual(outcome.fetched, 20, "every book is tried")
    assertEqual(outcome.ok, true, "the sync finishes")
    assertEqual(outcome.state.books["20"] ~= nil, true, "the books after the refused ones arrive")
    assert(shown[#shown]:find("except 5 books", 1, true), "and it says which did not: " .. tostring(shown[#shown]))
end

local function testASyncStopsWhenTheNetworkGoesAway()
    local outcome = sync(manyNewBooks(2000), true, function(book)
        if book.book_id == 10 then network.up = false end
        return true, { size = 1, mtime = 1 }
    end)
    network.up = true
    assertEqual(outcome.fetched, 10, "nothing is attempted after the network went away")
    assertEqual(outcome.ok, false, "and the sync says it did not finish")
    assertEqual(outcome.state.books["10"] ~= nil, true, "what did arrive is recorded")
end

local function testAFewFailedCoversDoNotStopTheRest()
    local outcome = sync(manyNewBooks(50), true, function(book)
        return book.book_id % 10 ~= 0, { size = 1, mtime = 1 }
    end)
    assertEqual(outcome.fetched, 50, "every cover is tried")
    assertEqual(outcome.ok, true, "the sync finishes")
    assert(shown[#shown]:find("except 5 books", 1, true), "and says which did not arrive: " .. tostring(shown[#shown]))
end

local function testBooksThatArrivedDuringTheSyncDoNotStopIt()
    -- Planned as new covers, but by the time their turn comes the books are
    -- there (sent or downloaded meanwhile): skipped, not failures.
    local root = folder .. "/arrived"
    assert(os.execute("mkdir -p '" .. root .. "'"))
    for id = 1, 3 do put("arrived/Book [" .. id .. "].epub") end
    local outcome = sync(manyNewBooks(10), true, function() return true, { size = 1, mtime = 1 } end, root)
    assertEqual(outcome.fetched, 7, "every other cover still arrives")
    assertEqual(outcome.ok, true, "and the sync finishes")
end

local function testTheInventoryKnowsCoversFromBooks()
    local cover = put("Cover [21].epub", 21)
    local book = put("Book [22].epub")
    local swapped = put("Swapped [23].epub")
    local untracked = put("Untracked.epub")
    local state = { books = {
        ["21"] = { kind = "placeholder", path = cover, size = 5 },       -- "cover" is 5 bytes
        ["22"] = { kind = "downloaded", path = book, size = 8 },
        ["23"] = { kind = "placeholder", path = swapped, size = 5 },     -- replaced: 8 bytes now
    } }
    local runtime = setmetatable({}, { __index = Runtime })
    function runtime:getLibraryState() return state end
    local isPlaceholder = runtime:libraryPlaceholderTest()
    for _, path in ipairs({ cover, book, swapped, untracked }) do
        assertEqual(isPlaceholder(path), runtime:isLibraryPlaceholder(path), "same answer as one by one for " .. path)
    end
    assertEqual(isPlaceholder(cover), true, "a cover is not a book on the device")
    assertEqual(isPlaceholder(swapped), false, "a cover replaced by a file is")
end

local function written(name)
    for path, file in pairs(settings_files) do
        if path:sub(-#name) == name then return file end
    end
end

local function testTheBookListIsWrittenOncePerSyncNotWithEveryRecord()
    local outcome = sync(manyNewBooks(60), "persist", function() return true, { size = 1, mtime = 1 } end)
    assertEqual(outcome.ok, true, "the sync finishes")
    for _ = 1, 5 do outcome.runtime:saveLibraryState() end -- five books opened
    local list, records = written("cwngsync_library_list.lua"), written("cwngsync_library.lua")
    assertEqual(list.flushes, 1, "the book list is written once")
    assertEqual(#list.written.manifest, 60, "with every book")
    assertEqual(records.written.state.manifest, nil, "the records are written without it")
    assertEqual(outcome.runtime:getLibraryState().manifest ~= nil, true, "and it stays in memory")

    -- KOReader restarts: a fresh runtime reads only what was written.
    package.loaded["cwng_library_runtime"] = nil
    local restarted = setmetatable({}, { __index = require("cwng_library_runtime") })
    local state = restarted:getLibraryState()
    assertEqual(#state.manifest, 60, "the book list comes back")
    assertEqual(state.books["60"] ~= nil, true, "with the records")

    -- A list from another revision than the records is not trusted.
    list.written.revision = "some other revision"
    package.loaded["cwng_library_runtime"] = nil
    restarted = setmetatable({}, { __index = require("cwng_library_runtime") })
    assertEqual(restarted:getLibraryState().manifest, nil, "a list that does not match the records is ignored")
    package.loaded["cwng_library_runtime"] = Runtime
end

local function testALibraryWhoseListWasNotSavedIsNotEmptied()
    -- KOReader's settings write fails without a word (a full disk), so the
    -- records can reach the disk while the book list does not. After a
    -- restart the server's "nothing changed" must not be applied to a list
    -- the device no longer has: that would remove every cover and download.
    for path in pairs(settings_files) do settings_files[path] = nil end
    local root = folder .. "/unsaved"
    assert(os.execute("mkdir -p '" .. root .. "'"))
    local cover, book = put("unsaved/Cover [1].epub", 1), put("unsaved/Book [2].epub")
    local list = {
        { book_id = 1, filename = "Cover [1].epub", rev = "a", read_status = "unread" },
        { book_id = 2, filename = "Book [2].epub", rev = "b", read_status = "unread", checksum = "md5:book" },
    }
    local asked = {}
    local client = { get_library = function(_, _, _, _, _, _, if_revision, callback)
        asked[#asked + 1] = if_revision or "everything"
        ticks[#ticks + 1] = function()
            callback(true, if_revision == "r1" and { unchanged = true, revision = "r1" }
                or { books = list, revision = "r1" })
        end
    end }
    local function start()
        package.loaded["cwng_library_runtime"] = nil
        local restarted = require("cwng_library_runtime")
        restarted.readPlaceholderId = Runtime.readPlaceholderId
        local runtime = setmetatable({ device_id = "device",
            settings = { server = "http://books", username = "reader", password = "secret" },
        }, { __index = restarted })
        function runtime:libraryEnabled() return true end
        function runtime:getLibraryRoot() return root end
        function runtime:newSyncClient() return client end
        function runtime:refreshLibraryViews() end
        function runtime:applyLibraryCollections() end
        function runtime:getDocumentDigest(path) return path == book and "md5:book" or nil end
        return runtime
    end
    local function run(runtime, opts)
        local outcome = {}
        opts.on_done = function(ok) outcome.ok = ok end
        runtime:syncLibrary(opts)
        while #ticks > 0 do table.remove(ticks, 1)() end
        return outcome
    end

    local runtime = start()
    local state = runtime:getLibraryState()
    state.owner = runtime:accountOwner()
    state.books["1"] = { kind = "placeholder", path = cover, size = 5, mtime = 0, rev = "a", status = "unread" }
    state.books["2"] = { kind = "downloaded", path = book, checksum = "md5:book", rev = "b", status = "unread" }
    assertEqual(run(runtime, { force = true }).ok, true, "the first sync finishes")
    written("cwngsync_library_list.lua").written = {} -- the list never reached the disk

    asked = {}
    local outcome = run(start(), {})
    assertEqual(outcome.ok, true, "the sync after the restart finishes")
    assertEqual(exists(cover), true, "the cover is still there")
    assertEqual(exists(book), true, "and so is the downloaded book")
    assertEqual(asked[1], "everything", "the device asks for the whole list")
    assertEqual(#(written("cwngsync_library_list.lua").written.manifest or {}), 2, "and saves it again")
    package.loaded["cwng_library_runtime"] = Runtime
end

local function testAnErrorInASyncDoesNotStopLaterSyncs()
    local outcome = sync(pagesOf(1, 1), false, nil, nil, "apply")
    assertEqual(outcome.ok, false, "an error while reading the list ends the sync")
    assertEqual(sync(pagesOf(1, 1)).ok, true, "and the next sync runs")

    outcome = sync(manyNewBooks(40), true, function() return true, { size = 1, mtime = 1 } end, nil, "views")
    assertEqual(outcome.ok, false, "an error in a step ends the sync")
    assertEqual(sync(pagesOf(1, 1)).ok, true, "and the next sync runs")
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

local function testNothingChangedIsNotAnAnswerToAWholeListRequest()
    local outcome = sync(function() return { unchanged = true, revision = "r1" } end)
    assertEqual(outcome.applied, nil, "an empty list must never be applied")
    assertEqual(outcome.ok, false, "the sync must fail")
end

local function testTheSameAccountTypedAnotherWayKeepsItsLibrary()
    -- Retyping the server with a slash at the end, or the name with a
    -- capital, is not a new account: its downloads must not be handed over.
    local function owner(server, username)
        return setmetatable({ settings = { server = server, username = username } },
            { __index = Runtime }):accountOwner()
    end
    local state = { owner = owner("http://books.example.org:8083", "reader"), books = { ["1"] = { path = "/b.epub" } } }
    local Library = require("cwng_library")
    local probe = { attributes = function() return { mode = "file" } end }
    assertEqual(Library.handover(state, owner("http://books.example.org:8083/", "Reader"), probe), nil,
        "the same account must keep its library")
    assertEqual(state.books["1"] ~= nil, true, "and its books")
    assertEqual(owner("http://books.example.org:8083", "other") ~= state.owner, true, "another reader is another account")
end

testEveryPageOfABigLibraryReachesTheDevice()
testAListThatDoesNotFinishIsNotApplied()
testTheSameAccountTypedAnotherWayKeepsItsLibrary()
testNothingChangedIsNotAnAnswerToAWholeListRequest()
testAServerRepeatingItsCursorStopsAtOnce()
testRemovingACoverKeepsTheReadersNotesButNotAStaleStatus()
testAStepDoesNothingToABookChangedSinceThePlan()
testASyncStopsWhenTheServerStopsAnswering()
testBooksTheServerRefusesDoNotStopTheRest()
testASyncStopsWhenTheNetworkGoesAway()
testAFewFailedCoversDoNotStopTheRest()
testBooksThatArrivedDuringTheSyncDoNotStopIt()
testTheInventoryKnowsCoversFromBooks()
testTheBookListIsWrittenOncePerSyncNotWithEveryRecord()
testALibraryWhoseListWasNotSavedIsNotEmptied()
testAnErrorInASyncDoesNotStopLaterSyncs()
os.execute("rm -rf '" .. folder .. "'")

print("cwng_library_runtime tests passed")

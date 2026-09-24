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

stub("ui/widget/booklist", {})
stub("datastorage", { getSettingsDir = function() return "/tmp" end, getDataDir = function() return "/tmp" end })
stub("device", { model = "Kindle" })
stub("docsettings", {})
stub("ui/widget/infomessage", { new = function(_, fields) return fields end })
stub("json", {})
stub("luasettings", {})
stub("ui/network/manager", { isConnected = function() return true end })
stub("ui/uimanager", { show = function() end })
stub("libs/libkoreader-lfs", { attributes = function() return nil end })
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

print("cwng_library_runtime tests passed")

package.path = table.concat({
    "./?.lua",
    "../?.lua",
    package.path,
}, ";")

local Library = require("cwng_library")

local function assertEqual(actual, expected, message)
    if actual ~= expected then
        error(string.format("%s\nexpected: %s\nactual: %s",
            message, tostring(expected), tostring(actual)), 2)
    end
end

-- A fake disk: path -> { bytes, mtime, placeholder_id }.
local function newDisk()
    local disk = { files = {}, open = {} }
    disk.probe = {
        attributes = function(path)
            local f = disk.files[path]
            if not f then return nil end
            return { size = #f.bytes, modification = f.mtime }
        end,
        digest = function(path)
            local f = disk.files[path]
            return f and ("md5:" .. f.bytes) or nil
        end,
        placeholderId = function(path)
            local f = disk.files[path]
            return f and f.placeholder_id or nil
        end,
        isOpen = function(path) return disk.open[path] == true end,
    }
    return disk
end

local ROOT = "/mnt/us/cwng-library"

local function book(id, fields)
    local b = {
        book_id = id,
        filename = "Book " .. id .. " [" .. id .. "].epub",
        rev = "r1",
        checksum = "md5:real-" .. id,
        read_status = "unread",
        progress = 0,
    }
    for k, v in pairs(fields or {}) do b[k] = v end
    return b
end

local function ops(actions)
    local out = {}
    for _, a in ipairs(actions) do out[#out + 1] = a.op .. ":" .. tostring(a.book_id) end
    table.sort(out)
    return table.concat(out, ",")
end

-- Perform the plan the way the runtime does, against the fake disk.
local function perform(disk, state, actions)
    for _, a in ipairs(actions) do
        local ok, info = true, nil
        if a.op == "create_placeholder" or a.op == "refresh_placeholder" then
            disk.files[a.path] = { bytes = "ph-" .. a.book_id .. "-" .. tostring(a.book.rev),
                mtime = 100, placeholder_id = a.book_id }
            info = { size = #disk.files[a.path].bytes, mtime = 100 }
        elseif a.op == "remove_placeholder" or a.op == "remove_download" then
            disk.files[a.path] = nil
        elseif a.op == "move_download" then
            disk.files[a.path] = disk.files[a.from]
            disk.files[a.from] = nil
        elseif a.op == "adopt_download" then
            local f = disk.files[a.path]
            info = { size = #f.bytes, mtime = f.mtime, checksum = "md5:" .. f.bytes }
        end
        Library.record(state, a, ok, info)
    end
end

local function syncTwice(disk, state, manifest)
    perform(disk, state, Library.plan(manifest, state, ROOT, disk.probe))
    return Library.plan(manifest, state, ROOT, disk.probe)
end

local function testEmptyDeviceGetsOnePlaceholderPerBookAndThenSettles()
    local disk, state = newDisk(), Library.newState()
    local manifest = { book(1), book(2), book(3) }
    local actions = Library.plan(manifest, state, ROOT, disk.probe)
    assertEqual(ops(actions), "create_placeholder:1,create_placeholder:2,create_placeholder:3",
        "every book in scope becomes a placeholder")
    assertEqual(actions[1].path, ROOT .. "/Book 1 [1].epub", "placeholder uses the server filename")
    local second = syncTwice(disk, state, manifest)
    assertEqual(#second, 0, "an unchanged manifest against an unchanged disk does nothing")
end

local function testMetadataRevisionRefreshesOnlyThatPlaceholder()
    local disk, state = newDisk(), Library.newState()
    syncTwice(disk, state, { book(1), book(2) })
    local actions = Library.plan({ book(1, { rev = "r2" }), book(2) }, state, ROOT, disk.probe)
    assertEqual(ops(actions), "refresh_placeholder:1", "only the edited book is refetched")
end

local function testReadStatusIsAppliedOnceAndProgressOnlyForPlaceholders()
    local disk, state = newDisk(), Library.newState()
    syncTwice(disk, state, { book(1) })
    local actions = Library.plan({ book(1, { progress = 0.4, read_status = "reading" }) },
        state, ROOT, disk.probe)
    assertEqual(ops(actions), "apply_status:1", "status/progress change on a placeholder")
    perform(disk, state, actions)
    assertEqual(#Library.plan({ book(1, { progress = 0.4, read_status = "reading" }) },
        state, ROOT, disk.probe), 0, "applied status is not re-applied")

    -- A downloaded book follows read status but not the percentage: its own
    -- sidecar position is the reader's, and the pull on open reconciles it.
    state.books["1"].kind = "downloaded"
    assertEqual(#Library.plan({ book(1, { progress = 0.9, read_status = "reading" }) },
        state, ROOT, disk.probe), 0, "progress alone does not touch a downloaded book")
    assertEqual(ops(Library.plan({ book(1, { progress = 0.9, read_status = "finished" }) },
        state, ROOT, disk.probe)), "apply_status:1", "finishing it does")
end

local function testSentBookLandingOnThePlaceholderIsAdoptedNotOverwritten()
    local disk, state = newDisk(), Library.newState()
    syncTwice(disk, state, { book(1) })
    local path = ROOT .. "/Book 1 [1].epub"
    disk.files[path] = { bytes = "real-1", mtime = 200 }
    local actions = Library.plan({ book(1) }, state, ROOT, disk.probe)
    assertEqual(ops(actions), "adopt_download:1", "the delivered book is recognised")
    perform(disk, state, actions)
    assertEqual(state.books["1"].kind, "downloaded", "and recorded as downloaded")
    assertEqual(state.books["1"].checksum, "md5:real-1", "with the checksum of the bytes on disk")
end

local function testUnknownFileOverThePlaceholderIsAConflictNotAnOverwrite()
    local disk, state = newDisk(), Library.newState()
    syncTwice(disk, state, { book(1) })
    local path = ROOT .. "/Book 1 [1].epub"
    disk.files[path] = { bytes = "something the reader made", mtime = 300 }
    local actions = Library.plan({ book(1) }, state, ROOT, disk.probe)
    assertEqual(ops(actions), "conflict:1", "a foreign file is never replaced")
end

local function testLeavingScopeRemovesPlaceholderButNeverASentBookThatReplacedIt()
    local disk, state = newDisk(), Library.newState()
    syncTwice(disk, state, { book(1), book(2) })
    -- Book 2's placeholder was replaced by a real copy the server does not
    -- recognise yet (state not updated -- e.g. the sync that would adopt it
    -- never ran). It must survive the book leaving scope.
    disk.files[ROOT .. "/Book 2 [2].epub"] = { bytes = "real-2", mtime = 500 }
    local actions = Library.plan({}, state, ROOT, disk.probe)
    assertEqual(ops(actions), "release:2,remove_placeholder:1",
        "the placeholder goes, the real file is released")
    perform(disk, state, actions)
    assertEqual(disk.files[ROOT .. "/Book 2 [2].epub"].bytes, "real-2", "the real book is still on disk")
    assertEqual(disk.files[ROOT .. "/Book 1 [1].epub"], nil, "the placeholder is gone")
end

local function testLeavingScopeRemovesOnlyUnmodifiedClosedDownloads()
    local disk, state = newDisk(), Library.newState()
    local manifest = { book(1), book(2), book(3), book(4) }
    syncTwice(disk, state, manifest)
    for id = 1, 4 do
        local path = ROOT .. "/Book " .. id .. " [" .. id .. "].epub"
        disk.files[path] = { bytes = "real-" .. id, mtime = 200 }
    end
    perform(disk, state, Library.plan(manifest, state, ROOT, disk.probe))
    disk.open[ROOT .. "/Book 2 [2].epub"] = true            -- being read
    disk.files[ROOT .. "/Book 3 [3].epub"].bytes = "edited" -- changed on device
    disk.files[ROOT .. "/Book 4 [4].epub"] = nil            -- already gone
    local actions = Library.plan({}, state, ROOT, disk.probe)
    assertEqual(ops(actions), "forget:4,release:3,remove_download:1",
        "unmodified closed book removed; open one waits; edited one released; missing one forgotten")
    perform(disk, state, actions)
    assertEqual(state.books["2"] ~= nil, true, "the open book stays managed for a later sync")
end

local function testReaderDeletingADownloadedBookPutsItBackInTheCloud()
    local disk, state = newDisk(), Library.newState()
    syncTwice(disk, state, { book(1) })
    local path = ROOT .. "/Book 1 [1].epub"
    disk.files[path] = { bytes = "real-1", mtime = 200 }
    perform(disk, state, Library.plan({ book(1) }, state, ROOT, disk.probe))
    disk.files[path] = nil
    assertEqual(ops(Library.plan({ book(1) }, state, ROOT, disk.probe)), "create_placeholder:1",
        "the cover comes back as a cloud book")
end

local function testLostStateIsRebuiltFromDiskWithoutClobbering()
    local disk = newDisk()
    disk.files[ROOT .. "/Book 1 [1].epub"] = { bytes = "ph", mtime = 1, placeholder_id = 1 }
    disk.files[ROOT .. "/Book 2 [2].epub"] = { bytes = "real-2", mtime = 1 }
    disk.files[ROOT .. "/Book 3 [3].epub"] = { bytes = "mine", mtime = 1 }
    local actions = Library.plan({ book(1), book(2), book(3) }, Library.newState(), ROOT, disk.probe)
    assertEqual(ops(actions), "adopt_download:2,conflict:3,refresh_placeholder:1",
        "our placeholder refreshed, the real copy adopted, a foreign file left alone")
end

local function testRenamedBookMovesItsFileAndKeepsItsPlace()
    local disk, state = newDisk(), Library.newState()
    syncTwice(disk, state, { book(1), book(2) })
    disk.files[ROOT .. "/Book 2 [2].epub"] = { bytes = "real-2", mtime = 200 }
    perform(disk, state, Library.plan({ book(1), book(2) }, state, ROOT, disk.probe))

    local renamed = { book(1, { filename = "New 1 [1].epub", rev = "r2" }),
                      book(2, { filename = "New 2 [2].epub", rev = "r2" }) }
    local actions = Library.plan(renamed, state, ROOT, disk.probe)
    assertEqual(ops(actions), "create_placeholder:1,move_download:2,remove_placeholder:1",
        "placeholder recreated under the new name; downloaded book moved")
    for _, a in ipairs(actions) do
        if a.op == "remove_placeholder" then
            assertEqual(a.to, ROOT .. "/New 1 [1].epub", "the old cover's notes are for the new name")
        end
    end
    perform(disk, state, actions)
    assertEqual(disk.files[ROOT .. "/New 2 [2].epub"].bytes, "real-2", "downloaded bytes moved intact")
    assertEqual(#Library.plan(renamed, state, ROOT, disk.probe), 0, "and it settles")

    disk.open[ROOT .. "/New 2 [2].epub"] = true
    assertEqual(#Library.plan({ book(1, { filename = "New 1 [1].epub", rev = "r2" }),
        book(2, { filename = "Third [2].epub", rev = "r3" }) }, state, ROOT, disk.probe), 0,
        "an open book is not moved from under the reader")
end

local function testHostileOrDuplicateEntriesNeverTouchTheDisk()
    local disk, state = newDisk(), Library.newState()
    local actions = Library.plan({
        { book_id = 7, filename = "../../koreader/settings.reader.lua" },
        { book_id = 8, filename = "sub/dir.epub" },
        { book_id = -1, filename = "x.epub" },
        { filename = "no-id.epub" },
        book(9),
        book(10, { filename = "Book 9 [9].epub" }),
    }, state, ROOT, disk.probe)
    assertEqual(ops(actions), "conflict:10,create_placeholder:9",
        "path-escaping and id-less entries are ignored; a duplicate name is refused")
end

local function testDefaultRootKeepsOffTheStockLibraries()
    assertEqual(Library.defaultRoot({ isKindle = true }), "/mnt/us/cwng-library", "Kindle")
    assertEqual(Library.defaultRoot({ isKobo = true }), "/mnt/onboard/.cwng-library", "Kobo")
    assertEqual(Library.defaultRoot({ home_dir = "/sdcard/Books/" }), "/sdcard/Books/CWNG Library", "other")
    assertEqual(Library.defaultRoot({}), nil, "no home, no guess")
end

local function testStatusMapping()
    assertEqual(Library.koreaderStatus("finished"), "complete", "finished")
    assertEqual(Library.koreaderStatus("unread"), nil, "unread clears")
    assertEqual(Library.serverStatus("complete"), "finished", "complete")
    assertEqual(Library.serverStatus("abandoned"), nil, "on hold has no server meaning")
end

local function testStateOfAnotherVersionStartsOver()
    assertEqual(next(Library.loadState({ version = 99, books = { ["1"] = {} } }).books), nil,
        "unknown version")
    assertEqual(next(Library.loadState("garbage").books), nil, "garbage")
end

local function testAnotherAccountStartsAFreshLibraryAndKeepsDownloads()
    local disk, state = newDisk(), Library.newState()
    assertEqual(Library.handover(state, "http://a|ann", disk.probe), nil, "a new library takes its owner")
    assertEqual(state.owner, "http://a|ann", "owner recorded")
    local manifest = { book(1), book(2), book(3) }
    syncTwice(disk, state, manifest)
    local downloaded = ROOT .. "/Book 1 [1].epub"
    disk.files[downloaded] = { bytes = "real-1", mtime = 200 }
    perform(disk, state, Library.plan(manifest, state, ROOT, disk.probe))
    disk.files[ROOT .. "/Book 3 [3].epub"] = nil -- deleted on the device
    state.revision = "rev-a"
    state.collections = { ["7"] = "Favorites" }
    assertEqual(Library.handover(state, "http://a|ann", disk.probe), nil, "same account: nothing to do")

    local actions = Library.handover(state, "http://b|bob", disk.probe)
    assertEqual(ops(actions), "forget:3,release:1,remove_placeholder:2", "covers go, the downloaded book stays")
    perform(disk, state, actions)
    assertEqual(disk.files[downloaded] ~= nil, true, "the downloaded book is still on the device")
    assertEqual(next(state.books), nil, "nothing of the old account is tracked")
    assertEqual(state.revision, nil, "the old server's revision is never offered to the new one")
    assertEqual(state.owner, "http://b|bob", "the library is the new account's")
    assertEqual(state.collections["7"], "Favorites", "old shelf collections stay recorded, so the next sync removes them")

    local other = book(1, { filename = "Other [1].epub", checksum = "md5:other" })
    assertEqual(ops(Library.plan({ other }, state, ROOT, disk.probe)), "create_placeholder:1",
        "id 1 on the new server is a different book: the old file is not renamed to it")
end

-- A book downloaded here, as the library records it after a download.
local function downloaded(disk, state, b)
    syncTwice(disk, state, { b })
    local path = ROOT .. "/" .. b.filename
    disk.files[path] = { bytes = "real-" .. b.book_id, mtime = 200 }
    perform(disk, state, Library.plan({ b }, state, ROOT, disk.probe))
    assertEqual(state.books[tostring(b.book_id)].kind, "downloaded", "set-up: the book is downloaded")
    return path
end

local function testAFileCopiedOverACoverIsNeverDeletedWhenTheServerHasNoChecksum()
    local disk, state = newDisk(), Library.newState()
    local b = book(1)
    b.checksum = nil
    syncTwice(disk, state, { b })
    local path = ROOT .. "/Book 1 [1].epub"
    disk.files[path] = { bytes = "the reader's own edition", mtime = 300 }
    perform(disk, state, Library.plan({ b }, state, ROOT, disk.probe))
    assertEqual(state.books["1"].kind, "downloaded", "it is listed as the book")
    local leaving = Library.plan({}, state, ROOT, disk.probe)
    assertEqual(ops(leaving), "release:1", "leaving the library lets go of it")
    perform(disk, state, leaving)
    assert(disk.files[path], "the reader's file is still there")
end

local function testADownloadDeletedHereThenRetitledComesBackAsACover()
    local disk, state = newDisk(), Library.newState()
    local old_path = downloaded(disk, state, book(1))
    disk.files[old_path] = nil
    local retitled = book(1, { filename = "New Title [1].epub" })
    local actions = Library.plan({ retitled }, state, ROOT, disk.probe)
    assertEqual(ops(actions), "create_placeholder:1,forget:1", "nothing to move: it comes back as a cover")
    perform(disk, state, actions)
    assertEqual(state.books["1"].path, ROOT .. "/New Title [1].epub", "under its new name")
    assertEqual(#Library.plan({ retitled }, state, ROOT, disk.probe), 0, "and the library settles")
end

local function testAKeptBookWhoseMoveWasNotSavedStaysTheReaders()
    -- With no checksum from the server, a file that replaced a cover is kept
    -- as the reader's. Retitled, it moves; when KOReader stops before the move
    -- is saved, its size and time (a rename keeps both) still identify it.
    local disk, state = newDisk(), Library.newState()
    local b = book(1)
    b.checksum = nil
    syncTwice(disk, state, { b })
    local old_path, new_path = ROOT .. "/Book 1 [1].epub", ROOT .. "/New Title [1].epub"
    disk.files[old_path] = { bytes = "the reader's own edition", mtime = 300 }
    perform(disk, state, Library.plan({ b }, state, ROOT, disk.probe))
    local retitled = book(1, { filename = "New Title [1].epub" })
    retitled.checksum = nil

    local moved = disk.files[old_path]
    disk.files[old_path] = nil
    disk.files[new_path] = { bytes = "someone else's edition!!", mtime = 301 }
    assertEqual(#moved.bytes, #disk.files[new_path].bytes, "set-up: the same size")
    local planned = ops(Library.plan({ retitled }, state, ROOT, disk.probe))
    assertEqual(planned:find("adopt_download", 1, true), nil, "a different file of the same size is not it")

    disk.files[new_path] = moved
    local actions = Library.plan({ retitled }, state, ROOT, disk.probe)
    assertEqual(ops(actions), "adopt_download:1", "the moved file is recognised")
    perform(disk, state, actions)
    assertEqual(state.books["1"].path, new_path, "recorded at its new name")
    assertEqual(state.books["1"].checksum, nil, "and still the reader's: no sync deletes it")
    assertEqual(#Library.plan({ retitled }, state, ROOT, disk.probe), 0, "and the library settles")
end

local function testAMoveWhoseRecordWasLostIsRecognised()
    local disk, state = newDisk(), Library.newState()
    local old_path = downloaded(disk, state, book(1))
    local new_path = ROOT .. "/New Title [1].epub"
    -- The move happened, but KOReader stopped before the state was saved.
    disk.files[new_path], disk.files[old_path] = disk.files[old_path], nil
    local retitled = book(1, { filename = "New Title [1].epub" })
    local actions = Library.plan({ retitled }, state, ROOT, disk.probe)
    assertEqual(ops(actions), "adopt_download:1", "the moved book is recognised by its bytes")
    assertEqual(actions[1].from, old_path, "with where its sidecar may still be")
    perform(disk, state, actions)
    assertEqual(state.books["1"].path, new_path, "recorded at its new name")
    assertEqual(#Library.plan({ retitled }, state, ROOT, disk.probe), 0, "and the library settles")
end

testEmptyDeviceGetsOnePlaceholderPerBookAndThenSettles()
testAnotherAccountStartsAFreshLibraryAndKeepsDownloads()
testMetadataRevisionRefreshesOnlyThatPlaceholder()
testReadStatusIsAppliedOnceAndProgressOnlyForPlaceholders()
testSentBookLandingOnThePlaceholderIsAdoptedNotOverwritten()
testUnknownFileOverThePlaceholderIsAConflictNotAnOverwrite()
testLeavingScopeRemovesPlaceholderButNeverASentBookThatReplacedIt()
testLeavingScopeRemovesOnlyUnmodifiedClosedDownloads()
testReaderDeletingADownloadedBookPutsItBackInTheCloud()
local function testABookSentOntoItsCoverIsTheBookAtOnce()
    local disk, state = newDisk(), Library.newState()
    local manifest = { book(1), book(2) }
    state.manifest = manifest
    syncTwice(disk, state, manifest)
    local path = ROOT .. "/Book 1 [1].epub"

    -- The website sends book 1; it lands on the name its cover had.
    disk.files[path] = { bytes = "real-1", mtime = 500 }
    local id = Library.noteDelivered(state, path,
        { size = 6, mtime = 500, checksum = "md5:real-1" }, "2026-09-23T23:22:18Z")
    assertEqual(id, 1, "the library knows which of its books arrived")
    assertEqual(state.books["1"].kind, "downloaded", "it is the book now, not a cover")
    assertEqual(state.books["1"].arrived, "2026-09-23T23:22:18Z", "and when it reached this device")
    assertEqual(#Library.plan(manifest, state, ROOT, disk.probe), 0, "so the next sync has nothing to do")

    -- Different bytes from the server's are the reader's file: the sync's
    -- conflict rule decides, not this.
    local other = ROOT .. "/Book 2 [2].epub"
    disk.files[other] = { bytes = "someone else's", mtime = 600 }
    assertEqual(Library.noteDelivered(state, other,
        { size = 14, mtime = 600, checksum = "md5:someone else's" }, "2026-09-23T23:30:00Z"), nil,
        "a file that is not the book is not recorded as it")
    assertEqual(state.books["2"].kind, "placeholder", "the record is left for the sync to judge")

    assertEqual(Library.noteDelivered(state, ROOT .. "/Not in the library [99].epub",
        { size = 3, mtime = 700, checksum = "md5:x" }, "2026-09-23T23:31:00Z"), nil,
        "a book outside the library is listed from the folder instead")
end

testLostStateIsRebuiltFromDiskWithoutClobbering()
testRenamedBookMovesItsFileAndKeepsItsPlace()
testHostileOrDuplicateEntriesNeverTouchTheDisk()
testDefaultRootKeepsOffTheStockLibraries()
testStatusMapping()
local function testShelvesBecomeCollectionsWithoutTakingTheReadersOwn()
    local books = {
        book(1, { shelves = { "s-fav", "s-read" } }),
        book(2, { shelves = { "s-fav" } }),
        book(3, { shelves = {} }),
    }
    local shelves = {
        { id = "s-fav", name = "Favorites" },
        { id = "s-read", name = "To Read" },
        { id = "s-empty", name = "Empty" },
    }
    local plan = Library.collectionPlan(books, shelves, ROOT, {}, { ["To Read"] = true })
    assertEqual(#plan.collections, 2, "empty shelves are not made into collections")
    assertEqual(plan.collections[1].name, "Favorites", "plain shelf name")
    assertEqual(#plan.collections[1].files, 2, "cloud and downloaded books alike")
    assertEqual(plan.collections[1].files[1], ROOT .. "/Book 1 [1].epub", "library paths")
    assertEqual(plan.collections[2].name, "To Read (CWNG)", "the reader's own collection is not taken over")
    local steady = Library.collectionPlan(books, shelves, ROOT, plan.managed,
        { Favorites = true, ["To Read"] = true, ["To Read (CWNG)"] = true })
    assertEqual(steady.collections[1].name, "Favorites", "a collection this device made keeps its name")
    assertEqual(steady.collections[2].name, "To Read (CWNG)", "suffixed one stays suffixed")
    assertEqual(#steady.remove, 0, "nothing to remove when nothing changed")
    local builtin = Library.collectionPlan(books, shelves, ROOT, {}, { favorites = true })
    assertEqual(builtin.collections[1].name, "Favorites (CWNG)",
        "KOReader's built-in favorites (shown as Favorites) is not doubled")
    local upgraded = Library.collectionPlan(books, shelves, ROOT, { ["s-fav"] = "Favorites" },
        { Favorites = true, favorites = true })
    assertEqual(upgraded.collections[1].name, "Favorites (CWNG)",
        "a device that already made 'Favorites' moves it off the built-in's name")
    assertEqual(upgraded.remove[1], "Favorites", "and drops the old one")

    -- Next sync: the shelf was renamed and one shelf emptied.
    local again = Library.collectionPlan(
        { book(1, { shelves = { "s-fav" } }) },
        { { id = "s-fav", name = "Loved" }, { id = "s-read", name = "To Read" } },
        ROOT, plan.managed, { ["To Read"] = true, ["Favorites"] = true, ["To Read (CWNG)"] = true })
    assertEqual(again.collections[1].name, "Loved", "renamed shelf renames its collection")
    assertEqual(table.concat(again.remove, "|"), "Favorites|To Read (CWNG)",
        "our old names are removed; the reader's 'To Read' is not")
end

testStateOfAnotherVersionStartsOver()
testShelvesBecomeCollectionsWithoutTakingTheReadersOwn()
testABookSentOntoItsCoverIsTheBookAtOnce()
testAFileCopiedOverACoverIsNeverDeletedWhenTheServerHasNoChecksum()
testADownloadDeletedHereThenRetitledComesBackAsACover()
testAMoveWhoseRecordWasLostIsRecognised()
testAKeptBookWhoseMoveWasNotSavedStaysTheReaders()
print("cwng_library_test.lua: all tests passed")

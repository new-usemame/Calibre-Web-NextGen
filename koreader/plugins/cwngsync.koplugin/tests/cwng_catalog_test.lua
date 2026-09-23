package.path = table.concat({
    "./?.lua",
    "../?.lua",
    package.path,
}, ";")

local Catalog = require("cwng_catalog")

local function assertEqual(actual, expected, message)
    if actual ~= expected then
        error(string.format("%s\nexpected: %s\nactual: %s",
            message, tostring(expected), tostring(actual)), 2)
    end
end

local function titles(list)
    local out = {}
    for i, e in ipairs(list) do out[i] = e.title end
    return table.concat(out, "|")
end

local ROOT = "/mnt/us/cwng-library"

local BOOKS = {
    { book_id = 1, title = "Guards! Guards!", authors = { "Terry Pratchett" }, series = "Discworld",
      series_index = 8, filename = "Guards [1].epub", added = "2026-01-05T00:00:00Z",
      read_status = "finished", shelves = { "s1" } },
    { book_id = 2, title = "The Colour of Magic", authors = { "Terry Pratchett" }, series = "Discworld",
      series_index = 1, filename = "Colour [2].epub", added = "2026-03-01T00:00:00Z",
      read_status = "reading", last_read = "2026-09-20T10:00:00Z", shelves = {} },
    { book_id = 3, title = "Good Omens", authors = { "Terry Pratchett", "Neil Gaiman" },
      filename = "Omens [3].epub", added = "2026-09-01T00:00:00Z", read_status = "unread",
      shelves = { "s1", "s2" } },
    { book_id = 4, title = "Coraline", authors = { "Neil Gaiman" }, filename = "Coraline [4].epub",
      added = "2026-02-01T00:00:00Z", read_status = "reading", last_read = "2026-09-22T09:00:00Z",
      shelves = {} },
    { book_id = 5, title = "Mort", authors = { "Terry Pratchett" }, series = "Discworld",
      series_index = 4, filename = "Mort [5].epub", added = "2026-04-01T00:00:00Z",
      read_status = "unread", shelves = { "s2" } },
    { book_id = 6, title = "A Broken Compass", filename = "Broken.epub" }, -- no authors, no dates
    { title = "No id", filename = "x.epub" },                   -- skipped
}
local SHELVES = { { id = "s1", name = "Favorites" }, { id = "s2", name = "Beach reads" } }
local KNOWN = { ["4"] = { kind = "downloaded", path = ROOT .. "/Coraline [4].epub" },
                ["5"] = { kind = "placeholder", path = ROOT .. "/Mort [5].epub" } }

local function entries() return Catalog.entries(BOOKS, KNOWN, ROOT) end

local function testEntriesKnowWhatIsOnTheDevice()
    local list = entries()
    assertEqual(#list, 6, "a book without an id is not listed")
    assertEqual(list[4].downloaded, true, "the downloaded book")
    assertEqual(list[5].downloaded, false, "a cover is not a download")
    assertEqual(list[1].path, ROOT .. "/Guards [1].epub", "path from the library folder")
    assertEqual(list[6].title, "A Broken Compass", "a book with little metadata is still listed")
    assertEqual(list[5].present, true, "a cover is on the device")
    assertEqual(list[1].present, false, "a book the sync has not added yet is not")
end

local function testRecentlyAddedNewestFirst()
    assertEqual(titles(Catalog.recentlyAdded(entries(), 3)), "Good Omens|Mort|The Colour of Magic",
        "newest three")
end

local function testContinueReadingPutsThisDeviceFirstAndDropsFinished()
    local history = { ROOT .. "/Guards [1].epub", ROOT .. "/Coraline [4].epub" }
    local list = Catalog.continueReading(entries(), history, 5, function(path)
        if path == ROOT .. "/Coraline [4].epub" then return "reading" end
    end)
    assertEqual(titles(list), "Coraline|The Colour of Magic",
        "opened here first; Guards is finished; Colour is being read elsewhere")

    local finished_here = Catalog.continueReading(entries(), history, 5, function(path)
        if path == ROOT .. "/Coraline [4].epub" then return "complete" end
    end)
    assertEqual(titles(finished_here), "The Colour of Magic", "finishing it here takes it off the row")
end

local function testGroupsAndTheirMembersInReadingOrder()
    local authors = Catalog.groups(entries(), "author")
    assertEqual(#authors, 2, "two authors")
    assertEqual(authors[1].name, "Neil Gaiman", "sorted by surname, shown as written")
    assertEqual(authors[1].sort_name, "Gaiman, Neil", "filed under the surname")
    assertEqual(authors[2].count, 4, "co-written books count for each author")
    local series = Catalog.groups(entries(), "series")
    assertEqual(#series, 1, "books without a series make no group")
    assertEqual(titles(Catalog.members(entries(), "series", series[1].key)),
        "The Colour of Magic|Mort|Guards! Guards!", "series in number order, not title order")
    assertEqual(titles(Catalog.members(entries(), "author", authors[2].key)),
        "The Colour of Magic|Mort|Guards! Guards!|Good Omens",
        "an author's series first in order, then the rest by title")
    local shelves = Catalog.groups(entries(), "shelf", SHELVES)
    assertEqual(shelves[1].name .. "=" .. shelves[1].count, "Beach reads=2", "shelf names from the manifest")
    assertEqual(titles(Catalog.members(entries(), "shelf", "s1")), "Good Omens|Guards! Guards!", "shelf by title")
end

local function testAuthorsAreFiledBySurname()
    assertEqual(Catalog.authorSortName("Terry Pratchett"), "Pratchett, Terry", "surname first")
    assertEqual(Catalog.authorSortName("Martin Luther King Jr."), "King, Martin Luther Jr.", "suffix stays")
    assertEqual(Catalog.authorSortName("Homer"), "Homer", "one name")
    assertEqual(Catalog.authorSortName("Pratchett, Terry"), "Pratchett, Terry", "already filed")
    local books = {
        { book_id = 1, title = "A", authors = { "Anna Zola" }, filename = "a.epub" },
        { book_id = 2, title = "B", authors = { "Zed Adams" }, filename = "b.epub" },
        { book_id = 3, title = "C", authors = { "Mary Shelley", "Percy Shelley" },
          author_sort = "Shelley, Mary Wollstonecraft & Shelley, Percy Bysshe", filename = "c.epub" },
    }
    local groups = Catalog.groups(Catalog.entries(books, {}, ROOT), "author")
    local names = {}
    for i, g in ipairs(groups) do names[i] = g.name end
    assertEqual(table.concat(names, "|"), "Zed Adams|Mary Shelley|Percy Shelley|Anna Zola",
        "by surname, not first name")
    assertEqual(groups[2].sort_name, "Shelley, Mary Wollstonecraft", "the server's author_sort wins")
end

local function testSearchFindsEveryWordAnywhereAndRanksTitles()
    assertEqual(titles(Catalog.search(entries(), "gaiman")), "Coraline|Good Omens", "by author")
    assertEqual(titles(Catalog.search(entries(), "discworld mort")), "Mort", "all words must match")
    assertEqual(titles(Catalog.search(entries(), "co")), "Coraline|A Broken Compass|The Colour of Magic",
        "title start before title middle")
    assertEqual(titles(Catalog.search(entries(), "ma")), "The Colour of Magic|Coraline|Good Omens",
        "a title match before an author match")
    assertEqual(#Catalog.search(entries(), "   "), 0, "nothing typed, nothing found")
    local accents = Catalog.search(entries(), "CORALINE", function(s) return (s or ""):lower() end)
    assertEqual(titles(accents), "Coraline", "matching goes through the fold")
end

local function testAccentsMatchTheirPlainLetters()
    local fold = Catalog.makeFold()
    assertEqual(fold("Brontë Æsop Ōtsuka Straße"), "bronte aesop otsuka strasse", "accents and ligatures fold")
    local books = { { book_id = 1, title = "Jane Eyre", authors = { "Charlotte Brontë" }, filename = "a.epub" },
                    { book_id = 2, title = "Émile", authors = { "Jean-Jacques Rousseau" }, filename = "b.epub" } }
    local list = Catalog.entries(books, {}, ROOT)
    assertEqual(titles(Catalog.search(list, "bronte", fold)), "Jane Eyre", "typed without the accent")
    assertEqual(titles(Catalog.search(list, "émile", fold)), "Émile", "typed with it")
    local calls = 0
    local memo = Catalog.memoize(function(s) calls = calls + 1 return s end)
    memo("x") memo("x") memo("y")
    assertEqual(calls, 2, "each text folded once")
end

local function testBooksSentOrCopiedToTheFolderAreListedToo()
    local title, author = Catalog.parseFilename("Jane Eyre_ An Autobiography - Charlotte Brontë [11].epub")
    assertEqual(title .. "|" .. author, "Jane Eyre_ An Autobiography|Charlotte Brontë", "server file name")
    assertEqual((Catalog.parseFilename("notes.pdf")), "notes", "any other name is the title")
    local files = {
        { name = "Guards [1].epub", path = ROOT .. "/Guards [1].epub", mtime = 100 },
        { name = "Emma - Jane Austen [77].epub", path = ROOT .. "/Emma - Jane Austen [77].epub", mtime = 1790000000 },
        { name = "x.epub", path = ROOT .. "/x.epub", mtime = 50 },
    }
    local tracked = { [ROOT .. "/Guards [1].epub"] = true }
    local list = Catalog.localEntries(files, tracked, function(path)
        if path == ROOT .. "/x.epub" then return { title = "Persuasion", authors = { "Jane Austen" } } end
    end)
    assertEqual(#list, 2, "a book the library tracks is not listed twice")
    assertEqual(list[1].title .. "/" .. list[1].authors[1], "Emma/Jane Austen", "from the file name")
    assertEqual(list[2].title, "Persuasion", "KOReader's own metadata wins")
    assertEqual(list[1].downloaded and list[1].present, true, "it is here and readable")
    assertEqual(list[1].added, "2026-09-21T14:13:20Z", "added when the file arrived")
    assertEqual(titles(Catalog.recentlyAdded(list)), "Emma|Persuasion", "a new arrival is first in Recent")
    assertEqual(list[1].book_id ~= list[2].book_id and list[1].book_id < 0, true, "ids never clash with the server's")
end

local function testDownloadedOnly()
    assertEqual(titles(Catalog.downloadedOnly(entries())), "Coraline", "only the real book")
end

testEntriesKnowWhatIsOnTheDevice()
testRecentlyAddedNewestFirst()
testContinueReadingPutsThisDeviceFirstAndDropsFinished()
testGroupsAndTheirMembersInReadingOrder()
testAuthorsAreFiledBySurname()
testSearchFindsEveryWordAnywhereAndRanksTitles()
testAccentsMatchTheirPlainLetters()
testBooksSentOrCopiedToTheFolderAreListedToo()
testDownloadedOnly()
print("cwng_catalog_test.lua: all tests passed")

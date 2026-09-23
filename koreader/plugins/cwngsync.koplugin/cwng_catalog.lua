--[[
The CWNG home's view of the library: every book the server lists for this
device, downloaded or not, grouped and searched without opening a single
file. Pure functions over the library manifest (see cwng_library.lua) so a
library of thousands of books browses instantly; cwng_home.lua draws it.

`fold` is the text normaliser for matching and sorting (lowercase, accents
removed); the runtime passes KOReader's Unicode-aware one, tests a plain
lowercase.
]]

local Catalog = {}

local function defaultFold(text)
    return (text or ""):lower()
end

-- Letters with accents match and sort with their plain letter, so "Brontë"
-- is found by typing "bronte". Latin-1 and Latin Extended-A, both cases.
local ACCENTS = {
    a = "àáâãäåāăąÀÁÂÃÄÅĀĂĄ", c = "çćĉċčÇĆĈĊČ", d = "ďđĎĐ", e = "èéêëēĕėęěÈÉÊËĒĔĖĘĚ",
    g = "ĝğġģĜĞĠĢ", h = "ĥħĤĦ", i = "ìíîïĩīĭįıÌÍÎÏĨĪĬĮİ", j = "ĵĴ", k = "ķĶ", l = "ĺļľŀłĹĻĽĿŁ",
    n = "ñńņňÑŃŅŇ", o = "òóôõöøōŏőÒÓÔÕÖØŌŎŐ", r = "ŕŗřŔŖŘ", s = "śŝşšŚŜŞŠ", t = "ţťŧŢŤŦ",
    u = "ùúûüũūŭůűųÙÚÛÜŨŪŬŮŰŲ", w = "ŵŴ", y = "ýÿŷÝŸŶ", z = "źżžŹŻŽ",
    ae = "æÆ", oe = "œŒ", ss = "ß",
}
local PLAIN = {}
for plain, accented in pairs(ACCENTS) do
    for char in accented:gmatch("[\xC3-\xC5][\x80-\xBF]") do PLAIN[char] = plain end
end

function Catalog.stripAccents(text)
    return (text:gsub("[\xC3-\xC5][\x80-\xBF]", PLAIN))
end

-- The fold used for matching and sorting: `lower` lowercases (KOReader's
-- Unicode-aware one on the device), then accents go.
function Catalog.makeFold(lower)
    lower = lower or string.lower
    return function(text)
        return Catalog.stripAccents(lower(text or ""))
    end
end

-- Sorting a large library folds the same names over and over; remember them.
function Catalog.memoize(fold)
    local cache = {}
    return function(text)
        text = text or ""
        local folded = cache[text]
        if folded == nil then
            folded = fold(text)
            cache[text] = folded
        end
        return folded
    end
end

-- An author's name as a library files it, surname first ("Pratchett,
-- Terry"), the way calibre derives author_sort when nobody set one.
local NAME_SUFFIXES = { jr = true, sr = true, ii = true, iii = true, iv = true, phd = true, md = true }
function Catalog.authorSortName(name)
    if type(name) ~= "string" or name:find(",", 1, true) then return name end
    local words = {}
    for word in name:gmatch("%S+") do words[#words + 1] = word end
    local last = #words
    while last > 1 and NAME_SUFFIXES[words[last]:lower():gsub("%.", "")] do last = last - 1 end
    if last <= 1 then return name end
    local rest = {}
    for i = 1, last - 1 do rest[#rest + 1] = words[i] end
    for i = last + 1, #words do rest[#rest + 1] = words[i] end
    return words[last] .. ", " .. table.concat(rest, " ")
end

-- The sort name for each of a book's authors: the server's author_sort
-- ("Pratchett, Terry & Gaiman, Neil") when it lines up with the authors,
-- else derived.
local function authorSorts(book, authors)
    local given = {}
    if type(book.author_sort) == "string" and book.author_sort ~= "" then
        for part in (book.author_sort .. " & "):gmatch("(.-) & ") do given[#given + 1] = part end
    end
    local sorts = {}
    for i, author in ipairs(authors) do
        sorts[i] = (#given == #authors and given[i] ~= "") and given[i] or Catalog.authorSortName(author)
    end
    return sorts
end

local function join(root, name)
    return (root:gsub("/+$", "")) .. "/" .. name
end

-- One entry per manifest book, with where it lives on this device and
-- whether the real book (not just its cover) is here.
function Catalog.entries(books, known_books, root)
    local out = {}
    for _, book in ipairs(books or {}) do
        if type(book) == "table" and type(book.book_id) == "number"
                and type(book.filename) == "string" and book.filename ~= "" then
            local known = known_books and known_books[tostring(book.book_id)]
            local authors = type(book.authors) == "table" and book.authors or {}
            out[#out + 1] = {
                book_id = book.book_id,
                title = type(book.title) == "string" and book.title ~= "" and book.title or book.filename,
                authors = authors,
                author_sorts = authorSorts(book, authors),
                series = type(book.series) == "string" and book.series ~= "" and book.series or nil,
                series_index = tonumber(book.series_index),
                shelves = type(book.shelves) == "table" and book.shelves or {},
                read_status = book.read_status,
                progress = tonumber(book.progress),
                added = type(book.added) == "string" and book.added or "",
                last_read = type(book.last_read) == "string" and book.last_read or "",
                path = (known and known.path) or join(root, book.filename),
                -- On the device as a cover or the real book (not yet, while
                -- a sync is still adding it).
                present = known ~= nil,
                downloaded = known ~= nil and known.kind == "downloaded",
            }
        end
    end
    return out
end

-- Title and author from a library file name, "Title - Author [12].epub"
-- (the server's naming for sent and downloaded books); anything else is all
-- title.
function Catalog.parseFilename(name)
    local stem = (name or ""):gsub("%.[%w]+$", ""):gsub("%s*%[%d+%]$", "")
    local title, author = stem:match("^(.-) %- ([^%-]+)$")
    if title and title ~= "" then return title, author end
    return stem, nil
end

-- Books in the library folder that the library does not track: sent from
-- the website, released when they left the chosen shelves, or copied over
-- USB. They are the reader's too, so the home lists them. `files` is
-- { {name, path, mtime} }; `tracked` is a set of paths the manifest owns;
-- `metadata(path)` returns KOReader's cached {title, authors} or nil.
function Catalog.localEntries(files, tracked, metadata)
    local out = {}
    for i, file in ipairs(files or {}) do
        if not tracked[file.path] then
            local meta = metadata and metadata(file.path)
            local title, author = Catalog.parseFilename(file.name)
            local authors = meta and meta.authors or (author and { author } or {})
            out[#out + 1] = {
                book_id = -i,
                title = meta and meta.title or title,
                authors = authors,
                author_sorts = authorSorts({}, authors),
                shelves = {},
                added = file.mtime and os.date("!%Y-%m-%dT%H:%M:%SZ", file.mtime) or "",
                last_read = "",
                path = file.path,
                present = true,
                downloaded = true,
                local_file = true,
            }
        end
    end
    return out
end

local function byTitle(fold)
    return function(a, b)
        local ta, tb = fold(a.title), fold(b.title)
        if ta ~= tb then return ta < tb end
        return a.book_id < b.book_id
    end
end

function Catalog.recentlyAdded(entries, limit)
    local list = {}
    for _, e in ipairs(entries) do list[#list + 1] = e end
    table.sort(list, function(a, b)
        if a.added ~= b.added then return a.added > b.added end
        return a.book_id > b.book_id
    end)
    if limit and #list > limit then
        for i = #list, limit + 1, -1 do list[i] = nil end
    end
    return list
end

-- Books being read: first those opened on this device, most recent first
-- (KOReader's history), then books being read elsewhere (another e-reader,
-- the web reader) by when they were last read. A finished book is not
-- "continue reading", wherever it was finished. local_status(path) gives
-- KOReader's own status for a book on this device, or nil.
function Catalog.continueReading(entries, history_paths, limit, local_status)
    local by_path = {}
    for _, e in ipairs(entries) do by_path[e.path] = e end
    local seen, list = {}, {}
    local function finished(e)
        local status = local_status and local_status(e.path)
        if status == "complete" then return true end
        if status == nil and e.read_status == "finished" then return true end
        return false
    end
    for _, path in ipairs(history_paths or {}) do
        local e = by_path[path]
        if e and not seen[e.book_id] and not finished(e) then
            seen[e.book_id] = true
            list[#list + 1] = e
        end
    end
    local elsewhere = {}
    for _, e in ipairs(entries) do
        if not seen[e.book_id] and e.read_status == "reading" and not finished(e) then
            elsewhere[#elsewhere + 1] = e
        end
    end
    table.sort(elsewhere, function(a, b)
        if a.last_read ~= b.last_read then return a.last_read > b.last_read end
        return a.book_id > b.book_id
    end)
    for _, e in ipairs(elsewhere) do list[#list + 1] = e end
    if limit and #list > limit then
        for i = #list, limit + 1, -1 do list[i] = nil end
    end
    return list
end

-- Groups for the Authors, Series and Shelves lists: { key, name, count,
-- sort_name }, sorted by sort_name: authors by surname ("Pratchett, Terry"),
-- the rest by name. `shelves` is the manifest's [{id, name}].
function Catalog.groups(entries, kind, shelves, fold)
    fold = fold or defaultFold
    local counts, names, sort_names = {}, {}, {}
    local function add(key, name, sort_name)
        if key == nil or name == nil or name == "" then return end
        counts[key] = (counts[key] or 0) + 1
        names[key] = names[key] or name
        sort_names[key] = sort_names[key] or sort_name or name
    end
    local shelf_names = {}
    for _, shelf in ipairs(shelves or {}) do
        if type(shelf) == "table" and shelf.id ~= nil then shelf_names[tostring(shelf.id)] = shelf.name end
    end
    for _, e in ipairs(entries) do
        if kind == "author" then
            for i, author in ipairs(e.authors) do add(fold(author), author, e.author_sorts[i]) end
        elseif kind == "series" then
            if e.series then add(fold(e.series), e.series) end
        elseif kind == "shelf" then
            for _, id in ipairs(e.shelves) do
                id = tostring(id)
                add(id, shelf_names[id])
            end
        end
    end
    local list = {}
    for key, count in pairs(counts) do
        list[#list + 1] = { key = key, name = names[key], sort_name = sort_names[key], count = count }
    end
    table.sort(list, function(a, b)
        local na, nb = fold(a.sort_name), fold(b.sort_name)
        if na ~= nb then return na < nb end
        return tostring(a.key) < tostring(b.key)
    end)
    return list
end

-- The books in one group, in the order a reader expects: a series by its
-- numbering, an author's books series by series then by title, a shelf by
-- title.
function Catalog.members(entries, kind, key, fold)
    fold = fold or defaultFold
    local list = {}
    for _, e in ipairs(entries) do
        local member = false
        if kind == "author" then
            for _, author in ipairs(e.authors) do
                if fold(author) == key then member = true break end
            end
        elseif kind == "series" then
            member = e.series ~= nil and fold(e.series) == key
        elseif kind == "shelf" then
            for _, id in ipairs(e.shelves) do
                if tostring(id) == key then member = true break end
            end
        end
        if member then list[#list + 1] = e end
    end
    local title_order = byTitle(fold)
    table.sort(list, function(a, b)
        if kind == "series" or kind == "author" then
            local sa, sb = a.series and fold(a.series), b.series and fold(b.series)
            if kind == "author" and sa ~= sb then
                if sa == nil then return false end
                if sb == nil then return true end
                return sa < sb
            end
            if sa ~= nil and sa == sb and a.series_index ~= b.series_index then
                return (a.series_index or math.huge) < (b.series_index or math.huge)
            end
        end
        return title_order(a, b)
    end)
    return list
end

-- Every word typed must appear in the title, an author or the series.
-- Title matches rank first (a title starting with the query first of all),
-- then author, then series matches.
function Catalog.search(entries, query, fold)
    fold = fold or defaultFold
    local words = {}
    for word in fold(query or ""):gmatch("%S+") do words[#words + 1] = word end
    if #words == 0 then return {} end
    local scored = {}
    for _, e in ipairs(entries) do
        local title = fold(e.title)
        local authors = {}
        for _, author in ipairs(e.authors) do authors[#authors + 1] = fold(author) end
        local author_text = table.concat(authors, " ")
        local series = e.series and fold(e.series) or ""
        local all_found = true
        for _, word in ipairs(words) do
            if not (title:find(word, 1, true) or author_text:find(word, 1, true)
                    or series:find(word, 1, true)) then
                all_found = false
                break
            end
        end
        if all_found then
            local phrase = table.concat(words, " ")
            local rank
            if title:sub(1, #phrase) == phrase then rank = 1
            elseif title:find(phrase, 1, true) then rank = 2
            elseif author_text:find(phrase, 1, true) then rank = 3
            elseif series:find(phrase, 1, true) then rank = 4
            else rank = 5 end
            scored[#scored + 1] = { entry = e, rank = rank, title = title }
        end
    end
    table.sort(scored, function(a, b)
        if a.rank ~= b.rank then return a.rank < b.rank end
        if a.title ~= b.title then return a.title < b.title end
        return a.entry.book_id < b.entry.book_id
    end)
    local out = {}
    for i, s in ipairs(scored) do out[i] = s.entry end
    return out
end

function Catalog.downloadedOnly(list)
    local out = {}
    for _, e in ipairs(list) do
        if e.downloaded then out[#out + 1] = e end
    end
    return out
end

return Catalog

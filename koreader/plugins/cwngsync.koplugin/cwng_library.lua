--[[
The CWNG library folder: one EPUB per book in the user's e-reader scope.

A book that is not on the device yet is a placeholder, a small EPUB the server
generates (cover, metadata, one page) so KOReader's own cover grid can show it.
Opening a placeholder downloads the real book over the same path. A book that
leaves the scope is removed again, but a downloaded one only while its bytes
are still exactly what the server sent and it is not open, so nothing a reader
changed on the device is ever deleted by a sync.

This module only decides. `plan` compares the server's manifest with what the
device recorded and what is on disk, and returns actions; the runtime in
main.lua performs them and reports each outcome back through `record`. Keeping
the decision pure is what lets the host-Lua suites exercise every branch
without a device.

State shape (persisted by the runtime):
    { version = 1, revision = <server revision>, books = {
        ["<book_id>"] = { kind = "placeholder"|"downloaded", path, rev,
                          size, mtime, checksum, status, progress } } }
]]

local Library = {}

Library.STATE_VERSION = 1

function Library.newState()
    return { version = Library.STATE_VERSION, books = {} }
end

-- Accept only a state this version wrote; anything else starts over. A stale
-- or corrupt state only costs re-deciding from disk, which `plan` does safely.
function Library.loadState(raw)
    if type(raw) ~= "table" or raw.version ~= Library.STATE_VERSION
            or type(raw.books) ~= "table" then
        return Library.newState()
    end
    return raw
end

local function join(root, name)
    return (root:gsub("/+$", "")) .. "/" .. name
end
Library.join = join

local function validName(name)
    return type(name) == "string" and name ~= ""
        and name ~= "." and name ~= ".."
        and not name:find("[/\\]")
        and not name:find("%z")
end

local function validBook(book)
    return type(book) == "table"
        and type(book.book_id) == "number" and book.book_id > 0
        and validName(book.filename)
end

-- Where the library lives when the user has not chosen a folder. Outside the
-- Kindle's `documents/` (the stock side indexes that and is left alone), and a
-- dot-folder on Kobo so Nickel does not list the placeholders as books.
function Library.defaultRoot(device)
    if device.isKindle then
        return "/mnt/us/cwng-library"
    elseif device.isKobo then
        return "/mnt/onboard/.cwng-library"
    end
    local base = device.home_dir
    if type(base) ~= "string" or base == "" then return nil end
    return join(base, "CWNG Library")
end

local function statusDiffers(known, book)
    if known.status ~= book.read_status then return true end
    if known.kind == "placeholder" and known.progress ~= book.progress then
        return true
    end
    return false
end

-- Is the file at `known.path` still the placeholder we wrote? Size and mtime
-- are the cheap answer; the marker inside the file is the authoritative one
-- when they disagree (a copy, a clock change). Anything else -- usually a book
-- sent to the device over the same name -- is a real file and must survive.
local function stillPlaceholder(known, id, probe)
    local attributes = probe.attributes(known.path)
    if not attributes then return nil end
    if attributes.size == known.size and attributes.modification == known.mtime then
        return true
    end
    return probe.placeholderId(known.path) == tonumber(id)
end

-- The library belongs to one account on one server (`owner`). Book ids and
-- revisions mean nothing to another, so connecting elsewhere starts afresh:
-- the old covers go, and downloaded books stay as the reader's own files, as
-- "Disconnect this device" promises. Returns the actions that clear the disk,
-- having already reset `state` in place, or nil when the owner is unchanged.
-- A state without an owner was written before owners were recorded; it is
-- taken to be the current account's.
function Library.handover(state, owner, probe)
    if state.owner == owner then return nil end
    if state.owner == nil then
        state.owner = owner
        return nil
    end
    local actions = {}
    for id, known in pairs(state.books) do
        local op = "release"
        if known.kind == "placeholder" then
            local placeholder = stillPlaceholder(known, id, probe)
            if placeholder then
                op = "remove_placeholder"
            elseif placeholder == nil then
                op = "forget"
            end
        elseif not probe.attributes(known.path) then
            op = "forget"
        end
        actions[#actions + 1] = { op = op, book_id = tonumber(id), path = known.path, leaving = true }
    end
    local collections = state.collections
    for key in pairs(state) do state[key] = nil end
    state.version = Library.STATE_VERSION
    state.books = {}
    state.owner = owner
    state.collections = collections
    return actions
end

-- probe:
--   attributes(path) -> { size, modification } | nil
--   digest(path)     -> KOReader partial MD5 | nil
--   placeholderId(path) -> book_id recorded inside a placeholder | nil
--   isOpen(path)     -> true while the reader has this file open
function Library.plan(manifest_books, state, root, probe)
    local actions = {}
    local wanted = {}
    local order = {}
    local claimed_paths = {}

    for _, book in ipairs(manifest_books or {}) do
        if validBook(book) then
            local id = tostring(book.book_id)
            if not wanted[id] then
                wanted[id] = book
                order[#order + 1] = id
            end
        end
    end

    local function add(op, id, fields)
        local action = fields or {}
        action.op = op
        action.book_id = tonumber(id)
        actions[#actions + 1] = action
        return action
    end

    for _, id in ipairs(order) do
        local book = wanted[id]
        local path = join(root, book.filename)
        local known = state.books[id]

        if claimed_paths[path] then
            -- Two books resolving to one filename would overwrite each other.
            -- The server names files with the book id, so this is a server
            -- defect, never something to resolve by clobbering.
            add("conflict", id, { path = path, book = book,
                reason = "another book in the library uses this file name" })
            goto continue
        end
        claimed_paths[path] = true

        -- A filename change (retitled book, new format) moves the entry.
        if known and known.path ~= path then
            if known.kind == "downloaded" then
                if probe.attributes(path) then
                    add("conflict", id, { path = path, book = book,
                        reason = "a different file already uses the new name" })
                    goto continue
                end
                if probe.isOpen(known.path) then
                    goto continue
                end
                add("move_download", id, { from = known.path, path = path, book = book })
                if statusDiffers(known, book) then
                    add("apply_status", id, { path = path, book = book })
                end
                goto continue
            end
            local placeholder = stillPlaceholder(known, id, probe)
            if placeholder then
                add("remove_placeholder", id, { path = known.path })
            elseif placeholder == false then
                add("release", id, { path = known.path })
            end
            known = nil
        end

        if not known then
            local attributes = probe.attributes(path)
            if not attributes then
                add("create_placeholder", id, { path = path, book = book })
            elseif probe.placeholderId(path) == book.book_id then
                -- Our own placeholder, but the record of it was lost.
                add("refresh_placeholder", id, { path = path, book = book })
            elseif book.checksum and probe.digest(path) == book.checksum then
                add("adopt_download", id, { path = path, book = book })
            else
                add("conflict", id, { path = path, book = book,
                    reason = "a different file already uses this name" })
            end
            goto continue
        end

        if known.kind == "placeholder" then
            local attributes = probe.attributes(path)
            if not attributes then
                add("create_placeholder", id, { path = path, book = book })
            elseif attributes.size ~= known.size or attributes.modification ~= known.mtime then
                -- Something replaced the placeholder: normally a book sent to
                -- this device, landing on the same name. Keep it only if it is
                -- the book; a checksum the server does not know is still the
                -- reader's file, so it is left alone rather than overwritten.
                if probe.placeholderId(path) == book.book_id then
                    add("refresh_placeholder", id, { path = path, book = book })
                elseif not book.checksum or probe.digest(path) == book.checksum then
                    add("adopt_download", id, { path = path, book = book })
                else
                    add("conflict", id, { path = path, book = book,
                        reason = "the placeholder was replaced by a different file" })
                end
            elseif known.rev ~= book.rev then
                add("refresh_placeholder", id, { path = path, book = book })
            elseif statusDiffers(known, book) then
                add("apply_status", id, { path = path, book = book })
            end
        else -- downloaded
            if not probe.attributes(path) then
                -- The reader deleted the book on the device: back to the cloud.
                add("create_placeholder", id, { path = path, book = book })
            elseif statusDiffers(known, book) then
                add("apply_status", id, { path = path, book = book })
            end
        end

        ::continue::
    end

    for id, known in pairs(state.books) do
        if not wanted[id] then
            if known.kind == "placeholder" then
                local placeholder = stillPlaceholder(known, id, probe)
                if placeholder then
                    add("remove_placeholder", id, { path = known.path, leaving = true })
                elseif placeholder == false then
                    add("release", id, { path = known.path })
                else
                    add("forget", id, { path = known.path })
                end
            elseif not probe.attributes(known.path) then
                add("forget", id, { path = known.path })
            elseif probe.isOpen(known.path) then
                -- Retried on a later sync, once the book is closed.
            elseif known.checksum and probe.digest(known.path) == known.checksum then
                add("remove_download", id, { path = known.path, checksum = known.checksum })
            else
                -- Changed on the device since we put it there. Stop managing
                -- it and leave the file where it is.
                add("release", id, { path = known.path })
            end
        end
    end

    return actions
end

-- Fold one performed action's outcome into the state. `info` carries what the
-- runtime observed after acting: { size, mtime, checksum } of the file now on
-- disk. A failed action leaves the state as it was, so the next sync decides
-- again from the same facts.
function Library.record(state, action, ok, info)
    if not ok then return state end
    local id = tostring(action.book_id)
    local book = action.book or {}
    info = info or {}
    local op = action.op

    if op == "create_placeholder" or op == "refresh_placeholder" then
        state.books[id] = {
            kind = "placeholder",
            path = action.path,
            rev = book.rev,
            size = info.size,
            mtime = info.mtime,
            status = book.read_status,
            progress = book.progress,
        }
    elseif op == "adopt_download" or op == "download" then
        state.books[id] = {
            kind = "downloaded",
            path = action.path,
            rev = book.rev,
            size = info.size,
            mtime = info.mtime,
            checksum = info.checksum or book.checksum,
            status = book.read_status,
        }
    elseif op == "move_download" then
        local known = state.books[id]
        if known then known.path = action.path end
    elseif op == "apply_status" then
        local known = state.books[id]
        if known then
            known.status = book.read_status
            if known.kind == "placeholder" then known.progress = book.progress end
        end
    elseif op == "remove_placeholder" then
        local known = state.books[id]
        if known and known.path == action.path then state.books[id] = nil end
    elseif op == "remove_download" or op == "forget" or op == "release" then
        state.books[id] = nil
    end
    return state
end

-- A book sent from the website landed at `path`. When that is where the
-- library keeps one of its books as a cover and the bytes are that book's, it
-- is the book now: recorded as downloaded at once, so the home drops the cloud
-- badge without waiting for the next sync (which would adopt it the same way).
-- `arrived` (ISO-8601 UTC) goes on the record, so Recent can show it first.
-- Returns the book id, or nil when no book of the library is at `path` or the
-- file is not that book, which is left to the sync's conflict rule.
function Library.noteDelivered(state, path, info, arrived)
    for id, known in pairs(state.books or {}) do
        if known.path == path then
            if known.kind == "placeholder" then
                local book
                for _, entry in ipairs(state.manifest or {}) do
                    if tostring(entry.book_id) == id then book = entry break end
                end
                if not book or (book.checksum and book.checksum ~= info.checksum) then return nil end
                Library.record(state, { op = "adopt_download", book_id = tonumber(id), path = path, book = book },
                    true, info)
            end
            state.books[id].arrived = arrived
            return tonumber(id)
        end
    end
    return nil
end

-- The book a path holds as a placeholder, according to the state. Used on the
-- open path, where reading the file's own marker would cost a zip open.
function Library.placeholderAt(state, path)
    for id, known in pairs(state.books) do
        if known.path == path then
            if known.kind == "placeholder" then return tonumber(id), known end
            return nil
        end
    end
    return nil
end

function Library.isManagedPlaceholder(state, path)
    return Library.placeholderAt(state, path) ~= nil
end

-- Shelves become KOReader collections holding every book on the shelf, cloud
-- or downloaded, under the shelf's own name. `managed` maps shelf id -> the
-- collection name this device created for it last time; a name already used
-- by a collection the reader made is never taken over (it gets a suffix).
-- Returns { collections = { {id, name, files} }, remove = {name...}, managed }.
function Library.collectionPlan(books, shelves, root, managed, existing_names)
    managed = managed or {}
    -- Compared without case: KOReader shows its built-in "favorites" as
    -- "Favorites", so a shelf of that name would look like a duplicate.
    local ours = {}
    for _, name in pairs(managed) do ours[name] = true end
    local existing = {}
    for name in pairs(existing_names or {}) do
        if not ours[name] then existing[name:lower()] = true end
    end
    local members = {}
    for _, book in ipairs(books or {}) do
        if validBook(book) and type(book.shelves) == "table" then
            for _, shelf_id in ipairs(book.shelves) do
                local key = tostring(shelf_id)
                members[key] = members[key] or {}
                table.insert(members[key], join(root, book.filename))
            end
        end
    end
    local plan = { collections = {}, remove = {}, managed = {} }
    local taken = {}
    for _, shelf in ipairs(shelves or {}) do
        local id = type(shelf) == "table" and shelf.id ~= nil and tostring(shelf.id) or nil
        if id and members[id] then
            local base = type(shelf.name) == "string" and shelf.name ~= "" and shelf.name or "Shelf"
            local name = base
            if existing[name:lower()] or taken[name:lower()] then
                name = base .. " (CWNG)"
            end
            taken[name:lower()] = true
            plan.managed[id] = name
            table.insert(plan.collections, { id = id, name = name, files = members[id] })
        end
    end
    for id, name in pairs(managed) do
        if plan.managed[id] ~= name and not taken[name:lower()] then
            table.insert(plan.remove, name)
        end
    end
    table.sort(plan.remove)
    return plan
end

-- Map CWNG's read status onto KOReader's book status. KOReader's own
-- "on hold" (abandoned) has no server equivalent and is never overwritten.
function Library.koreaderStatus(read_status)
    if read_status == "finished" then return "complete" end
    if read_status == "reading" then return "reading" end
    return nil
end

function Library.serverStatus(koreader_status)
    if koreader_status == "complete" then return "finished" end
    if koreader_status == "reading" then return "reading" end
    if koreader_status == nil or koreader_status == "new" then return "unread" end
    return nil
end

return Library

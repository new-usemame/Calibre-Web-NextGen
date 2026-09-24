--[[
Runtime half of the CWNG library folder. library.lua decides what the folder
must hold; this file talks to the server and the disk, and is mixed into the
CWNGSync plugin class by main.lua.

Two paths matter to the reader:
  * syncLibrary   - fetches the manifest and brings the folder in line with it,
                    one file per UI tick so the device stays responsive.
  * the open hook - tapping a placeholder downloads the real book over it and
                    opens that instead, from anywhere KOReader opens a book.
]]

local BookList = require("ui/widget/booklist")
local DataStorage = require("datastorage")
local Device = require("device")
local DocSettings = require("docsettings")
local InfoMessage = require("ui/widget/infomessage")
local Json = require("json")
local Library = require("cwng_library")
local Setup = require("cwng_setup")
local LuaSettings = require("luasettings")
local NetworkMgr = require("ui/network/manager")
local UIManager = require("ui/uimanager")
local lfs = require("libs/libkoreader-lfs")
local logger = require("logger")
local util = require("util")
local T = require("ffi/util").template
local _ = require("gettext")

local Runtime = {}

local PLACEHOLDER_MAX_BYTES = 1024 * 1024
-- Automatic syncs (network up, wake, file browser shown) are at most this
-- often. A manual "Sync now" and the first sync after setup ignore it.
local LIBRARY_SYNC_INTERVAL = 5 * 60
-- Only a guard against a server that never stops paging: a list cut short is
-- never applied, since every book past the cut would look as if it had left the
-- library. 5000 pages is a million books at the server's page size.
local MAX_MANIFEST_PAGES = 5000
local PLACEHOLDER_TIMEOUTS = { 5, 20 }
-- Steps that download from the server, and how many may fail in a row before
-- the sync stops rather than waiting out every remaining book's timeouts.
local DOWNLOADING_OPS = { create_placeholder = true, refresh_placeholder = true }
local STOP_AFTER_FAILED_DOWNLOADS = 3
local SAVE_EVERY_SECONDS = 30

-- Shared by the file browser's and the reader's plugin instances: there is one
-- library and one sync at a time, whichever instance started it.
local shared = {
    running = nil,
    last_sync = nil,
    state = nil,
    store = nil,
    plugin = nil,
}
Runtime._shared = shared

local function store()
    if not shared.store then
        shared.store = LuaSettings:open(DataStorage:getSettingsDir() .. "/cwngsync_library.lua")
    end
    return shared.store
end

-- The server's list of books is most of the state's size (thousands of
-- entries) and changes once per sync, while the per-book records change with
-- every step and every book opened. So it lives in a file of its own, written
-- only when a sync brings a new list.
local function manifestStore()
    if not shared.manifest_store then
        shared.manifest_store = LuaSettings:open(DataStorage:getSettingsDir() .. "/cwngsync_library_list.lua")
    end
    return shared.manifest_store
end

function Runtime:getLibraryState()
    if not shared.state then
        local state = Library.loadState(store():readSetting("state"))
        if state.manifest == nil then
            local list = manifestStore()
            if list:readSetting("revision") == state.revision then
                state.manifest = list:readSetting("manifest")
                state.shelves = list:readSetting("shelves")
                shared.saved_manifest = state.manifest
            end
        end
        shared.state = state
    end
    return shared.state
end

local function flush(file, what)
    local ok, err = pcall(file.flush, file)
    if not ok then logger.warn("CWNGSync: could not save library", what, err) end
end

function Runtime:saveLibraryState()
    local state = self:getLibraryState()
    local manifest, shelves = state.manifest, state.shelves
    if manifest ~= nil and manifest ~= shared.saved_manifest then
        local list = manifestStore()
        list:saveSetting("revision", state.revision)
        list:saveSetting("manifest", manifest)
        list:saveSetting("shelves", shelves)
        flush(list, "list")
        shared.saved_manifest = manifest
    end
    -- The records alone; the list is put back once they are written.
    state.manifest, state.shelves = nil, nil
    local file = store()
    file:saveSetting("state", state)
    flush(file, "state")
    state.manifest, state.shelves = manifest, shelves
end

function Runtime:isConfigured()
    local s = self.settings
    return type(s) == "table" and type(s.server) == "string" and s.server ~= ""
        and type(s.username) == "string" and s.username ~= ""
        and type(s.password) == "string" and s.password ~= ""
end

-- On for a device connected through setup (code, ready-made plugin or first
-- sign-in); an existing progress-sync install keeps its folders as they are
-- until the reader turns the library on.
function Runtime:libraryEnabled()
    return self:isConfigured() and self.settings.library_enabled == true
end

-- The folder is fixed the first time it is needed, so later changes to
-- KOReader's home folder (which setup points at this folder) cannot move it.
function Runtime:getLibraryRoot()
    local root = self.settings.library_root
    if type(root) == "string" and root ~= "" then return root end
    root = Library.defaultRoot({
        isKindle = Device:isKindle(),
        isKobo = Device:isKobo(),
        home_dir = G_reader_settings:readSetting("home_dir") or Device.home_dir,
    })
    self.settings.library_root = root
    return root
end

-- Whose library and queued reading this device holds: one account on one server.
function Runtime:accountOwner()
    return Setup.accountKey(self.settings.server, self.settings.username)
end

function Runtime:newSyncClient()
    local CWNGSyncClient = require("CWNGSyncClient")
    return CWNGSyncClient:new{
        service_url = self.settings.server .. "/kosync",
        service_spec = self.path .. "/api.json",
    }
end

-- The book id written inside a placeholder by the server, or nil for any
-- other file. Only small files are opened; a real book is never unzipped here.
function Runtime.readPlaceholderId(path)
    local attributes = lfs.attributes(path)
    if not attributes or attributes.mode ~= "file" or attributes.size > PLACEHOLDER_MAX_BYTES then
        return nil
    end
    local ok_require, Archiver = pcall(require, "ffi/archiver")
    if not ok_require then return nil end
    local arc = Archiver.Reader:new()
    if not arc:open(path) then return nil end
    -- The reader only knows an entry once it has walked past it, so a lookup
    -- by name before iterating finds nothing.
    local ok, content = pcall(function()
        for entry in arc:iterate() do
            if entry.path == "META-INF/cwng-placeholder.json" then
                return arc:extractToMemory(entry.path)
            end
        end
    end)
    pcall(arc.close, arc)
    if not ok or type(content) ~= "string" or content == "" then return nil end
    local ok_json, data = pcall(Json.decode, content)
    if ok_json and type(data) == "table" then return tonumber(data.book_id) end
    return nil
end

local function openDocumentPath()
    local ok, ReaderUI = pcall(require, "apps/reader/readerui")
    local instance = ok and ReaderUI.instance
    return instance and instance.document and instance.document.file or nil
end

function Runtime:libraryProbe()
    return {
        attributes = function(path)
            local a = lfs.attributes(path)
            if not a or a.mode ~= "file" then return nil end
            return { size = a.size, modification = a.modification }
        end,
        digest = function(path) return self:getDocumentDigest(path) end,
        placeholderId = Runtime.readPlaceholderId,
        isOpen = function(path) return openDocumentPath() == path end,
    }
end

-- True for a file this plugin put in the library as a not-yet-downloaded book.
-- Inventory and bulk progress pull skip these: the device does not hold the
-- book, only its cover.
function Runtime:isLibraryPlaceholder(path)
    local book_id, known = Library.placeholderAt(self:getLibraryState(), path)
    if not book_id then return false end
    local a = lfs.attributes(path)
    return a ~= nil and a.size == known.size
end

-- isLibraryPlaceholder for a walk over many files: the records are indexed
-- once, when the walk starts.
function Runtime:libraryPlaceholderTest()
    local index = Library.placeholderIndex(self:getLibraryState())
    return function(path)
        local known = index[path]
        if not known then return false end
        return lfs.attributes(path, "size") == known.size
    end
end

local function isoToTime(value)
    if type(value) ~= "string" then return nil end
    local y, mo, d, h, mi, s = value:match("^(%d%d%d%d)-(%d%d)-(%d%d)[T ](%d%d):(%d%d):(%d%d)")
    if not y then
        y, mo, d = value:match("^(%d%d%d%d)-(%d%d)-(%d%d)")
        h, mi, s = 12, 0, 0
    end
    if not y then return nil end
    return os.time({ year = tonumber(y), month = tonumber(mo), day = tonumber(d),
        hour = tonumber(h), min = tonumber(mi), sec = tonumber(s) })
end
Runtime._isoToTime = isoToTime

local function fileInfo(path)
    local a = lfs.attributes(path)
    if not a then return nil end
    return { size = a.size, mtime = a.modification }
end

-- Mirror the server's read status (and, for a cloud book, its progress) into
-- the book's KOReader sidecar, so the cover grid shows "finished" or a
-- progress bar. KOReader's own "on hold" is the reader's and is kept unless
-- the book was finished somewhere.
function Runtime:writeBookStatus(path, book, kind)
    if openDocumentPath() == path then return false end
    local ok, err = pcall(function()
        local doc_settings = DocSettings:open(path)
        local summary = doc_settings:readSetting("summary") or {}
        if not (summary.status == "abandoned" and book.read_status ~= "finished") then
            summary.status = Library.koreaderStatus(book.read_status)
        end
        doc_settings:saveSetting("summary", summary)
        if kind == "placeholder" then
            local progress = tonumber(book.progress)
            if progress and progress > 0 then
                doc_settings:saveSetting("percent_finished", progress)
            else
                doc_settings:delSetting("percent_finished")
            end
        end
        doc_settings:flush()
    end)
    if not ok then
        logger.warn("CWNGSync: could not write book status", path, err)
        return false
    end
    BookList.resetBookInfoCache(path)
    return true
end

function Runtime:fetchPlaceholder(client, book, path)
    local temp = path .. ".cwngsync.part"
    local ok, _length, _checksum, reason = client:download_file(
        self.settings.username, self.settings.password, Device.model, self.device_id,
        "/syncs/library/books/" .. book.book_id .. "/placeholder", temp, PLACEHOLDER_TIMEOUTS)
    if not ok then
        logger.warn("CWNGSync: placeholder download failed", book.book_id, reason)
        return false
    end
    local a = lfs.attributes(temp)
    if not a or a.size == 0 or a.size > PLACEHOLDER_MAX_BYTES
            or Runtime.readPlaceholderId(temp) ~= book.book_id then
        os.remove(temp)
        logger.warn("CWNGSync: server returned something that is not this book's placeholder", book.book_id)
        return false
    end
    -- The plan was made a moment ago; never let a placeholder land on a file
    -- that has since become something else (a book sent to this device).
    if lfs.attributes(path) and Runtime.readPlaceholderId(path) ~= book.book_id then
        os.remove(temp)
        return false
    end
    local renamed, rename_error = os.rename(temp, path)
    if not renamed then
        os.remove(temp)
        logger.warn("CWNGSync: could not place placeholder", path, rename_error)
        return false
    end
    -- "Last read" order: a never-opened cloud book sorts by when it joined the
    -- library, behind everything that has been read or sent.
    local when = isoToTime(book.added) or (os.time() - 365 * 24 * 3600)
    pcall(lfs.touch, path, when, when)
    BookList.resetBookInfoCache(path)
    if book.read_status ~= "unread" or (tonumber(book.progress) or 0) > 0 then
        self:writeBookStatus(path, book, "placeholder")
    end
    return true, fileInfo(path)
end

-- What a cover's sidecar holds when a sync is the only thing that wrote it:
-- writeBookStatus's status and progress, and the path KOReader adds itself.
local STATUS_ONLY_KEYS = { doc_path = true, summary = true, percent_finished = true }
local STATUS_ONLY_SUMMARY = { status = true, modified = true }

-- Whether removing a cover may take its sidecar with it. Only when the sidecar
-- holds nothing but the status a sync wrote (left behind, it would put a stale
-- "finished" on the book if it came back). A position, highlights, notes or a
-- custom cover are the reader's: they may be the book's from when it was
-- downloaded here, and they stay.
local function sidecarIsOnlyStatus(path)
    local ok, only = pcall(function()
        if not DocSettings:hasSidecarFile(path) then return false end
        if DocSettings:findCustomCoverFile(path) or DocSettings:findCustomMetadataFile(path) then return false end
        for key, value in pairs(DocSettings:open(path).data or {}) do
            if not STATUS_ONLY_KEYS[key] then return false end
            if key == "summary" then
                if type(value) ~= "table" then return false end
                for field in pairs(value) do
                    if not STATUS_ONLY_SUMMARY[field] then return false end
                end
            end
        end
        return true
    end)
    return ok and only == true
end

-- A step runs up to minutes after the plan that chose it, one per tick, and
-- the reader may have opened, downloaded or replaced the book in between. Each
-- step that touches a file checks again that it is still the file the plan
-- saw; if not, it does nothing and the next sync decides afresh.
local function stillAsPlanned(self, action)
    local op = action.op
    local open = openDocumentPath()
    if op == "create_placeholder" then
        return lfs.attributes(action.path, "mode") == nil
    elseif op == "refresh_placeholder" or op == "remove_placeholder" then
        return open ~= action.path and Runtime.readPlaceholderId(action.path) == action.book_id
    elseif op == "remove_download" then
        return open ~= action.path
            and (action.checksum == nil or self:getDocumentDigest(action.path) == action.checksum)
    elseif op == "move_download" then
        return open ~= action.from and lfs.attributes(action.path, "mode") == nil
    end
    return true
end

function Runtime:performLibraryAction(client, action)
    local op = action.op
    if not stillAsPlanned(self, action) then
        logger.info("CWNGSync: library step skipped, the book changed since the sync planned it:",
            op, action.book_id)
        return false, nil, "changed"
    end
    if op == "create_placeholder" or op == "refresh_placeholder" then
        return self:fetchPlaceholder(client, action.book, action.path)
    elseif op == "remove_placeholder" then
        local keep_sidecar = not sidecarIsOnlyStatus(action.path)
        local removed = os.remove(action.path)
        if removed then
            if not keep_sidecar then pcall(DocSettings.updateLocation, action.path) end
            BookList.resetBookInfoCache(action.path)
        end
        return removed ~= nil
    elseif op == "remove_download" then
        local removed = os.remove(action.path)
        if removed then
            -- The sidecar stays on purpose: position, highlights and notes
            -- come back if the book returns to this device.
            BookList.resetBookInfoCache(action.path)
            local ok_history, ReadHistory = pcall(require, "readhistory")
            if ok_history then pcall(ReadHistory.fileDeleted, ReadHistory, action.path) end
        end
        return removed ~= nil
    elseif op == "move_download" then
        local moved = os.rename(action.from, action.path)
        if not moved then return false end
        pcall(DocSettings.updateLocation, action.from, action.path)
        local ok_history, ReadHistory = pcall(require, "readhistory")
        if ok_history then pcall(ReadHistory.updateItem, ReadHistory, action.from, action.path) end
        local ok_collection, ReadCollection = pcall(require, "readcollection")
        if ok_collection then pcall(ReadCollection.updateItem, ReadCollection, action.from, action.path) end
        BookList.resetBookInfoCache(action.from)
        return true, fileInfo(action.path)
    elseif op == "adopt_download" then
        local info = fileInfo(action.path)
        if not info then return false end
        info.checksum = self:getDocumentDigest(action.path)
        if action.from and not DocSettings:hasSidecarFile(action.path) then
            -- A move whose record was lost may have lost its sidecar move too.
            pcall(DocSettings.updateLocation, action.from, action.path)
        end
        return true, info
    elseif op == "apply_status" then
        local known = self:getLibraryState().books[tostring(action.book_id)]
        return self:writeBookStatus(action.path, action.book, known and known.kind)
    elseif op == "conflict" then
        logger.warn("CWNGSync: library conflict for book", action.book_id, action.path, action.reason)
        return false
    elseif op == "release" or op == "forget" then
        return true
    end
    return false
end

-- The user's shelves as KOReader collections, cloud books included, so a
-- shelf chosen on the website is one tap away on the device.
function Runtime:applyLibraryCollections(books, shelves)
    local ok, ReadCollection = pcall(require, "readcollection")
    if not ok or type(ReadCollection.coll) ~= "table" then return end
    local state = self:getLibraryState()
    local existing = {}
    for name in pairs(ReadCollection.coll) do existing[name] = true end
    local plan = Library.collectionPlan(books, shelves, self:getLibraryRoot(),
        state.collections, existing)
    -- Only files that exist can be collected (a cover that failed to arrive
    -- is added on a later sync), and the signature covers exactly those, so a
    -- sync that finally delivers them is not mistaken for "nothing changed".
    local signature = {}
    for _, collection in ipairs(plan.collections) do
        local present = {}
        for _, file in ipairs(collection.files) do
            if lfs.attributes(file, "mode") == "file" then present[#present + 1] = file end
        end
        collection.files = present
        signature[#signature + 1] = collection.name .. "=" .. table.concat(present, "|")
    end
    signature = table.concat(signature, "\n")
    local in_place = signature == state.collections_signature and #plan.remove == 0
    for _, collection in ipairs(plan.collections) do
        local current = ReadCollection.coll[collection.name]
        local count = 0
        for _ in pairs(current or {}) do count = count + 1 end
        if not current or count ~= #collection.files then in_place = false end
    end
    if in_place then return end
    local updated = {}
    for _, name in ipairs(plan.remove) do
        if ReadCollection.coll[name] then
            ReadCollection:removeCollection(name)
            updated[name] = true
        end
    end
    for _, collection in ipairs(plan.collections) do
        if ReadCollection.coll[collection.name] then
            -- Same collection, fresh membership; its sort settings stay.
            ReadCollection.coll[collection.name] = {}
        else
            ReadCollection:addCollection(collection.name)
        end
        for _, file in ipairs(collection.files) do
            ReadCollection:addItem(file, collection.name)
        end
        updated[collection.name] = true
    end
    if next(updated) then
        local written, err = pcall(ReadCollection.write, ReadCollection, updated)
        if not written then
            logger.warn("CWNGSync: could not write shelf collections", err)
            return
        end
    end
    state.collections = plan.managed
    state.collections_signature = signature
end

-- The per-account "[CWNG xxxx]" collections the older inventory-based shelf
-- sync made are replaced by the library's own; drop them once.
function Runtime:retireSnapshotCollections()
    local snapshot_state = G_reader_settings:readSetting("cwngsync_collection_state")
    if type(snapshot_state) ~= "table" or next(snapshot_state) == nil then return end
    local ok, ReadCollection = pcall(require, "readcollection")
    if not ok or type(ReadCollection.coll) ~= "table" then return end
    local updated = {}
    for _, scope in pairs(snapshot_state) do
        for _, name in pairs(type(scope) == "table" and scope.names or {}) do
            if ReadCollection.coll[name] then
                ReadCollection:removeCollection(name)
                updated[name] = true
            end
        end
    end
    if next(updated) then pcall(ReadCollection.write, ReadCollection, updated) end
    G_reader_settings:delSetting("cwngsync_collection_state")
end

function Runtime:applyLibraryManifest(books, revision, token, opts, done, shelves)
    local state = self:getLibraryState()
    local root = self:getLibraryRoot()
    local actions = Library.plan(books, state, root, self:libraryProbe())
    local client = self:newSyncClient()
    local changed = {}
    local adding = 0
    for _, action in ipairs(actions) do
        if action.op == "create_placeholder" then adding = adding + 1 end
    end
    if adding >= 5 and opts.interactive ~= false then
        UIManager:show(InfoMessage:new{
            text = T(_("Adding %1 books to your library…"), adding),
            timeout = 3,
        })
    end

    local index = 0
    local succeeded, failed, failed_downloads_in_a_row = 0, 0, 0
    local last_saved = os.time()
    -- The list is recorded as applied either way: the next sync plans against
    -- it again, so whatever did not happen now happens then.
    local function finish(ok, summary)
        state.revision = revision
        state.manifest = books
        state.shelves = shelves
        local ok_collections, collections_error = pcall(self.applyLibraryCollections, self, books, shelves)
        if not ok_collections then
            logger.warn("CWNGSync: shelf collections failed", collections_error)
        end
        self:saveLibraryState()
        self:refreshLibraryViews(changed)
        done(ok, summary)
    end
    local step
    local function stepOnce()
        if shared.running ~= token then return end
        index = index + 1
        local action = actions[index]
        if not action then
            finish(true, { actions = #actions, succeeded = succeeded, failed = failed })
            return
        end
        local downloads = DOWNLOADING_OPS[action.op]
        if downloads and not NetworkMgr:isConnected() then
            logger.warn("CWNGSync: library sync stopped, the network went away after", index - 1, "steps")
            finish(false, _("the network went away; the rest comes on the next sync"))
            return
        end
        local ok_call, ok, info, why = pcall(self.performLibraryAction, self, client, action)
        ok = ok_call and ok
        if not ok_call then logger.warn("CWNGSync: library action failed", action.op, ok) end
        Library.record(state, action, ok, info)
        if ok then
            succeeded = succeeded + 1
            if action.path then changed[#changed + 1] = action.path end
            if action.from then changed[#changed + 1] = action.from end
        elseif action.op ~= "conflict" then
            failed = failed + 1
        end
        if downloads and why ~= "changed" then
            failed_downloads_in_a_row = ok and 0 or failed_downloads_in_a_row + 1
            if failed_downloads_in_a_row >= STOP_AFTER_FAILED_DOWNLOADS then
                -- Each attempt holds the screen for its timeouts: a server that
                -- stopped answering must not cost that for every book left.
                logger.warn("CWNGSync: library sync stopped after", failed_downloads_in_a_row,
                    "downloads failed in a row")
                finish(false, _("the server stopped answering; the rest comes on the next sync"))
                return
            end
        end
        -- Show covers as they arrive rather than all at the end. The records
        -- are saved at most every half minute meanwhile: a record lost to a
        -- crash only costs fetching that cover again.
        if #changed >= 18 then
            if os.time() - (last_saved or 0) >= SAVE_EVERY_SECONDS then
                self:saveLibraryState()
                last_saved = os.time()
            end
            self:refreshLibraryViews(changed)
            changed = {}
        end
        UIManager:nextTick(step)
    end
    -- As with the list's callback: an error in a step ends the sync rather
    -- than leaving it marked as running for good.
    step = function()
        local ran, err = pcall(stepOnce)
        if not ran then
            logger.warn("CWNGSync: library sync failed", err)
            done(false, _("something went wrong while updating the library"))
        end
    end
    step()
end

-- opts: interactive (show outcome, may bring Wi-Fi up), force (ignore the
-- interval and the revision shortcut), on_done(ok, summary).
function Runtime:syncLibrary(opts)
    opts = opts or {}
    if not self:libraryEnabled() then return end
    if shared.running then return end
    if not NetworkMgr:isConnected() then
        if opts.interactive then
            NetworkMgr:runWhenConnected(function() self:syncLibrary(opts) end)
        end
        return
    end
    local now = os.time()
    if not opts.force and shared.last_sync and now - shared.last_sync < LIBRARY_SYNC_INTERVAL then
        return
    end
    local root = self:getLibraryRoot()
    if not root then return end
    if not util.directoryExists(root) then util.makePath(root) end
    if not util.directoryExists(root) then
        logger.warn("CWNGSync: cannot create library folder", root)
        return
    end

    local token = {}
    shared.running = token
    local state = self:getLibraryState()
    local client = self:newSyncClient()
    local leaving = Library.handover(state, self:accountOwner(), self:libraryProbe())
    if leaving then
        -- Another account's covers go before this one's arrive; its downloaded
        -- books stay as the reader's own files.
        local cleared = {}
        for _, action in ipairs(leaving) do
            local ok_call, ok = pcall(self.performLibraryAction, self, client, action)
            Library.record(state, action, ok_call and ok)
            if ok_call and ok then cleared[#cleared + 1] = action.path end
        end
        logger.info("CWNGSync: library handed over to a new account;", #leaving, "books of the previous one cleared")
        self:saveLibraryState()
        self:refreshLibraryViews(cleared)
    end
    local books = {}
    local shelves = {}
    local pages = 0
    local cursors_seen = {}

    local function done(ok, summary)
        if shared.running ~= token then return end
        shared.running = nil
        if ok then shared.last_sync = os.time() end
        if opts.interactive then
            if ok and type(summary) == "table" and (summary.failed or 0) > 0 then
                UIManager:show(InfoMessage:new{
                    text = T(_("Your library is updated, except %1 books. They are tried again on the next sync."),
                        summary.failed),
                    timeout = 4,
                })
            elseif ok then
                UIManager:show(InfoMessage:new{ text = _("Your library is up to date."), timeout = 2 })
            else
                UIManager:show(InfoMessage:new{
                    text = T(_("Could not update your library: %1"), summary or _("no response from server")),
                    timeout = 5,
                })
            end
        end
        if opts.on_done then opts.on_done(ok, summary) end
    end

    local function fetch(cursor)
        pages = pages + 1
        local if_revision = (cursor == nil and not opts.force) and state.revision or nil
        local function onPage(ok, body, reason)
                if shared.running ~= token then return end
                if not ok or type(body) ~= "table" then
                    logger.warn("CWNGSync: library manifest failed", reason)
                    done(false, reason)
                    return
                end
                if body.unchanged then
                    -- Nothing changed on the server; still reconcile the disk
                    -- (a book deleted on the device comes back as a cover).
                    self:applyLibraryManifest(state.manifest or {}, state.revision, token, opts, done,
                        state.shelves)
                    return
                end
                if type(body.shelves) == "table" then shelves = body.shelves end
                for _, book in ipairs(body.books or {}) do books[#books + 1] = book end
                if body.next_cursor then
                    local cursor_key = tostring(body.next_cursor)
                    if cursors_seen[cursor_key] or pages >= MAX_MANIFEST_PAGES then
                        logger.warn("CWNGSync: library manifest did not finish after", pages, "pages")
                        done(false, _("the server's list of books did not finish"))
                        return
                    end
                    cursors_seen[cursor_key] = true
                    fetch(body.next_cursor)
                    return
                end
                self:applyLibraryManifest(books, body.revision, token, opts, done, shelves)
            end
        client:get_library(self.settings.username, self.settings.password, Device.model,
            self.device_id, cursor, if_revision,
            function(ok, body, reason)
                -- An error in here would end the request's coroutine without a
                -- word and leave this sync marked as running: the library would
                -- not sync again until KOReader restarted. It ends the sync.
                local ran, err = pcall(onPage, ok, body, reason)
                if not ran then
                    logger.warn("CWNGSync: library sync failed", err)
                    done(false, _("something went wrong while updating the library"))
                end
            end)
    end
    fetch(nil)
end

-- Download the real book over its placeholder. Synchronous: the reader tapped
-- this book and is waiting for it.
function Runtime:downloadLibraryBook(book_id, path, title)
    local client = self:newSyncClient()
    local temp = path .. ".cwngsync.part"
    local ok, length, checksum, reason = client:download_file(
        self.settings.username, self.settings.password, Device.model, self.device_id,
        "/syncs/library/books/" .. book_id .. "/file", temp)
    if not ok then return false, reason end
    local a = lfs.attributes(temp)
    if not a or (length and a.size ~= length) then
        os.remove(temp)
        return false, _("the download was incomplete")
    end
    local digest = self:getDocumentDigest(temp)
    if checksum and checksum ~= "" and digest ~= checksum then
        os.remove(temp)
        return false, _("the downloaded file was damaged")
    end
    local renamed, rename_error = os.rename(temp, path)
    if not renamed then
        os.remove(temp)
        return false, rename_error
    end
    pcall(lfs.touch, path)
    BookList.resetBookInfoCache(path)
    self:refreshLibraryViews({ path })

    local state = self:getLibraryState()
    local book = { book_id = book_id }
    for _, entry in ipairs(state.manifest or {}) do
        if entry.book_id == book_id then book = entry break end
    end
    local info = fileInfo(path) or {}
    info.checksum = digest
    Library.record(state, { op = "download", book_id = book_id, path = path, book = book }, true, info)
    self:saveLibraryState()
    logger.info("CWNGSync: downloaded library book", book_id, title)
    return true
end

-- A book sent from the website was installed at `installed.path`. See
-- Library.noteDelivered.
function Runtime:noteDelivered(installed)
    local state = self:getLibraryState()
    local info = fileInfo(installed.path) or {}
    info.checksum = installed.checksum
    if Library.noteDelivered(state, installed.path, info, os.date("!%Y-%m-%dT%H:%M:%SZ")) then
        self:saveLibraryState()
    end
end

local function manifestTitle(state, book_id, path)
    for _, entry in ipairs(state.manifest or {}) do
        if entry.book_id == book_id and type(entry.title) == "string" then return entry.title end
    end
    return (path:match("([^/]+)$") or path):gsub("%s*%[%d+%]%.%w+$", "")
end

-- Called in place of opening `file`. Returns true when `file` is a cloud book
-- and this function took over (it opens the real book itself once it is here).
function Runtime:openLibraryPlaceholder(file, open_real)
    if not self:isConfigured() then return false end
    local state = self:getLibraryState()
    local book_id, known = Library.placeholderAt(state, file)
    if not book_id then return false end
    local a = lfs.attributes(file)
    if not a or a.size ~= known.size then
        -- Already replaced by the real book (sent, or downloaded elsewhere).
        return false
    end
    local title = manifestTitle(state, book_id, file)
    NetworkMgr:runWhenConnected(function()
        local message = InfoMessage:new{ text = T(_("Downloading %1…"), title) }
        UIManager:show(message)
        UIManager:forceRePaint()
        local ok, reason = self:downloadLibraryBook(book_id, file, title)
        UIManager:close(message)
        if ok then
            open_real(file)
            -- With auto sync on, opening the book reports the inventory already.
            if not self.settings.auto_sync then
                UIManager:scheduleIn(5, function() self:reportInventory(false, false) end)
            end
        else
            UIManager:show(InfoMessage:new{
                text = T(_("Could not download %1.\n\n%2\n\nCheck that Wi-Fi is on and try again."),
                    title, require("CWNGSyncClient").plainReason(reason)),
            })
        end
    end)
    return true
end

-- Every way KOReader opens a book (file browser, history, collections, "open
-- last book") goes through ReaderUI:showReader, so this one hook covers them.
function Runtime:installOpenHook()
    shared.plugin = self
    local ok, ReaderUI = pcall(require, "apps/reader/readerui")
    if not ok or ReaderUI._cwngsync_original_showReader then return end
    local original = ReaderUI.showReader
    ReaderUI._cwngsync_original_showReader = original
    ReaderUI.showReader = function(reader_ui, file, ...)
        local n = select("#", ...)
        local args = { ... }
        local plugin = shared.plugin
        if plugin and type(file) == "string" then
            local handled_ok, handled = pcall(plugin.openLibraryPlaceholder, plugin, file,
                function(real_file) original(reader_ui, real_file, unpack(args, 1, n)) end)
            if handled_ok and handled then return end
            if not handled_ok then logger.warn("CWNGSync: open hook failed", handled) end
        end
        return original(reader_ui, file, unpack(args, 1, n))
    end
end

-- The library sorts most recent first by modification time; opening a book
-- is what makes it recent. (KOReader's own history only moves the access
-- time, which the device's storage also moves on every read.)
function Runtime:markOpened(file)
    if type(file) ~= "string" or not self:libraryEnabled() then return end
    local root = self:getLibraryRoot()
    if not root or file:sub(1, #root + 1) ~= root .. "/" then return end
    local now = os.time()
    pcall(lfs.touch, file, now, now)
    local known_id = nil
    for id, known in pairs(self:getLibraryState().books or {}) do
        if known.path == file then known_id = id break end
    end
    if known_id then
        -- Keep the record in step. Only a cover's time is ever compared with
        -- the file's, so this rides along with the next save instead of
        -- writing every record of the library each time a book is opened.
        local known = self:getLibraryState().books[known_id]
        if known.kind ~= "placeholder" then
            known.mtime = now
        end
    end
end

-- A placeholder opened some way the hook did not see (its record lost): close
-- it and fetch the real book, rather than showing the explanatory page.
function Runtime:rescueOpenedPlaceholder()
    local file = self.ui and self.ui.document and self.ui.document.file
    if not file or not self:isConfigured() then return false end
    local book_id = Runtime.readPlaceholderId(file)
    if not book_id then return false end
    local title = manifestTitle(self:getLibraryState(), book_id, file)
    local ui = self.ui
    UIManager:nextTick(function()
        ui:onClose()
        NetworkMgr:runWhenConnected(function()
            local message = InfoMessage:new{ text = T(_("Downloading %1…"), title) }
            UIManager:show(message)
            UIManager:forceRePaint()
            local ok, reason = self:downloadLibraryBook(book_id, file, title)
            UIManager:close(message)
            if ok then
                local ReaderUI = require("apps/reader/readerui")
                local original = ReaderUI._cwngsync_original_showReader or ReaderUI.showReader
                original(ReaderUI, file)
            else
                UIManager:show(InfoMessage:new{
                    text = T(_("Could not download %1.\n\n%2"), title, require("CWNGSyncClient").plainReason(reason)),
                })
            end
        end)
    end)
    return true
end

return Runtime

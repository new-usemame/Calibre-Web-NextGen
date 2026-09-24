--[[
Automatic sync around reading, without a "sync now" button and without ever
turning Wi-Fi on behind the reader's back. Mixed into the CWNGSync plugin
class by main.lua.

  * Leaving a book (closing it, the device going to sleep, a few page turns)
    captures its position, read status and highlights while it is still open
    and queues them on the device (cwng_pending.lua decides the queue rules).
  * Whenever the device is online (a connection comes up, it wakes, the
    library is shown) the queue is delivered, then the library, the open
    book's position and any books sent from the website are brought in.
  * Marking a book finished or reading from the library screen is queued the
    same way.
]]

local Device = require("device")
local DocSettings = require("docsettings")
local Json = require("json")
local Library = require("cwng_library")
local NetworkMgr = require("ui/network/manager")
local Pending = require("cwng_pending")
local SyncLogic = require("sync_logic")
local UIManager = require("ui/uimanager")
local logger = require("logger")

local AutoSync = {}

local PENDING_KEY = "cwngsync_pending"
local PUSHED_STATUS_KEY = "cwngsync_pushed_status"
-- Shared with main.lua's highlight sync: the ids this device last pushed.
local ANNOTATION_WATERMARK_KEY = "cwasync_pushed_annotation_ids"
-- After waking, the device takes a few seconds to rejoin Wi-Fi by itself.
local ONLINE_RETRY_DELAYS = { 3, 10, 30 }
local ONLINE_DEBOUNCE = 20

-- One delivery run at a time, whichever plugin instance (library or reader)
-- started it.
local shared = { flushing = false, last_online = nil, plugin = nil }

local function readerInstance()
    local ReaderUI = package.loaded["apps/reader/readerui"]
    return ReaderUI and ReaderUI.instance
end

local function decodeBody(body)
    if type(body) == "string" and body:find("^%s*{") then
        local ok, decoded = pcall(Json.decode, body)
        if ok then return decoded end
    end
    return body
end

-- A 4xx other than an auth failure will not change on retry (the server does
-- not know this book, or refused the value): give up on that part rather than
-- queue it forever. Anything else is transient.
-- A refusal that sending again will not change, so the queued change is let
-- go. Sign-in failures are not (a new password fixes them), and neither are
-- "too slow", "too early" or "too many requests": those ask to try later.
local RETRY_LATER = { [401] = true, [403] = true, [408] = true, [425] = true, [429] = true }
local function isFinalRefusal(reason)
    local code = tonumber(tostring(reason or ""):match("^HTTP (%d+)$"))
    return code ~= nil and code >= 400 and code < 500 and not RETRY_LATER[code]
end

function AutoSync:readPending()
    local queue = G_reader_settings:readSetting(PENDING_KEY)
    return type(queue) == "table" and queue or {}
end

function AutoSync:writePending(queue)
    if next(queue) == nil then
        G_reader_settings:delSetting(PENDING_KEY)
    else
        G_reader_settings:saveSetting(PENDING_KEY, queue)
    end
    pcall(G_reader_settings.flush, G_reader_settings)
end

-- Write one setting into a book's sidecar, through the reader when the book
-- is open there (its own flush would otherwise overwrite ours).
function AutoSync:saveBookSetting(file, key, value)
    if type(file) ~= "string" then return end
    local reader = readerInstance()
    if reader and reader.document and reader.document.file == file and reader.doc_settings then
        reader.doc_settings:saveSetting(key, value)
        return
    end
    local ok, err = pcall(function()
        local doc_settings = DocSettings:open(file)
        doc_settings:saveSetting(key, value)
        doc_settings:flush()
    end)
    if not ok then logger.warn("CWNGSync: could not update book settings", file, err) end
end

-- What the open book owes the server, read while it is still open.
-- explicit: the reader asked to send the position, moved or not.
function AutoSync:captureOpenBook(with_annotations, explicit)
    if not self:hasCurrentDocument() then return nil end
    local digest = self:getDocumentDigest()
    if not digest then return nil end
    local ok_progress, progress = pcall(self.getLastProgress, self)
    local ok_percent, percentage = pcall(self.getLastPercent, self)
    if not (ok_progress and ok_percent) or progress == nil then return nil end
    local doc_settings = self.ui.doc_settings
    local summary = doc_settings and doc_settings:readSetting("summary") or {}
    local entry = {
        document = digest,
        file = self:getCurrentDocumentFile(),
        progress = tostring(progress),
        percentage = percentage,
        captured_at = os.time(),
        status = Pending.statusToSend(summary.status,
            doc_settings and doc_settings:readSetting(PUSHED_STATUS_KEY)),
    }
    entry = Pending.trimUnmoved(entry, explicit or Pending.movedSince(self.opened_progress, progress))
    if not entry then return nil end
    if with_annotations and self.settings.sync_annotations then
        local DeviceAnnotations = require("device_annotations")
        local provider = DeviceAnnotations.getProvider(self.ui, digest)
        if provider and provider.push_all_local then
            local plan = SyncLogic.planLocalContribution(provider, nil, self:readAnnotationWatermark())
            -- Unreadable is not "no highlights": leave the last snapshot queued.
            if plan.known then
                entry.annotations = { list = plan.list, deletions = plan.deletions }
            end
        end
    end
    if entry.percentage == nil and entry.status == nil and entry.annotations == nil then return nil end
    return entry
end

-- Where the book stands now, as the point a later capture is compared with
-- to tell whether the reader moved. Called when the book is ready and after
-- a position from another device is applied.
function AutoSync:recordOpenedPosition()
    local ok, progress = pcall(self.getLastProgress, self)
    self.opened_progress = (ok and progress ~= nil) and tostring(progress) or nil
end

-- Queue what the open book owes and deliver it if the device is online.
-- on_done(ok, reason) reports the delivery (or "offline").
function AutoSync:queueOpenBook(with_annotations, on_done, explicit)
    if not self:isConfigured() then
        if on_done then on_done(false, "not connected") end
        return
    end
    local entry = self:captureOpenBook(with_annotations, explicit)
    if not entry then
        -- Nothing new to say is not a failure; still deliver what is queued.
        if not self:hasCurrentDocument() then
            if on_done then on_done(false, "no book") end
        elseif NetworkMgr:isConnected() then
            self:flushPending(on_done)
        elseif on_done then
            on_done(true)
        end
        return
    end
    local queue = self:readPending()
    entry.owner = self:accountOwner()
    Pending.record(queue, entry)
    self:writePending(queue)
    if NetworkMgr:isConnected() then
        self:flushPending(on_done)
    elseif on_done then
        on_done(false, "offline")
    end
end

-- The library screen's "Mark as finished / reading / on hold".
function AutoSync:noteStatusChange(doc_settings, summary)
    if not self:isConfigured() or not self.settings.auto_sync then return end
    if type(doc_settings) ~= "table" or type(summary) ~= "table" then return end
    local file = doc_settings:readSetting("doc_path")
    if type(file) ~= "string" then return end
    local reader = readerInstance()
    if reader and reader.document and reader.document.file == file then
        return -- leaving the book will carry it
    end
    local status = Pending.statusToSend(summary.status, doc_settings:readSetting(PUSHED_STATUS_KEY))
    if not status then return end
    local entry = { file = file, status = status, captured_at = os.time() }
    local book_id = Library.placeholderAt(self:getLibraryState(), file)
    if book_id and self:isLibraryPlaceholder(file) then
        -- A cloud book has no bytes to digest; the server knows it by id.
        entry.document = "book:" .. book_id
        entry.book_id = book_id
    else
        entry.document = self:getDocumentDigest(file)
        if not entry.document then return end
    end
    local queue = self:readPending()
    entry.owner = self:accountOwner()
    Pending.record(queue, entry)
    self:writePending(queue)
    if NetworkMgr:isConnected() then self:flushPending() end
end

function AutoSync:installStatusHook()
    shared.plugin = self
    local ok, filemanagerutil = pcall(require, "apps/filemanager/filemanagerutil")
    if not ok or filemanagerutil._cwngsync_original_saveSummary then return end
    local original = filemanagerutil.saveSummary
    filemanagerutil._cwngsync_original_saveSummary = original
    filemanagerutil.saveSummary = function(doc_settings_or_file, summary, ...)
        local result = original(doc_settings_or_file, summary, ...)
        local plugin = shared.plugin
        if plugin then
            local noted, err = pcall(plugin.noteStatusChange, plugin, result, summary)
            if not noted then logger.warn("CWNGSync: status hook failed", err) end
        end
        return result
    end
end

function AutoSync:bookIdFor(file)
    for id, known in pairs(self:getLibraryState().books or {}) do
        if known.path == file then return tonumber(id) end
    end
    return nil
end

-- Deliver one queued entry: position (unless another device has read on
-- since), then read status, then highlights. done(ok, reason).
function AutoSync:deliverPending(client, entry, done)
    local s = self.settings
    local steps = {}

    if entry.percentage ~= nil and entry.progress ~= nil then
        steps[#steps + 1] = function(continue)
            client:get_progress(s.username, s.password, entry.document, function(ok, body, reason)
                if not ok and reason ~= "HTTP 404" then return done(false, reason) end
                local remote = ok and decodeBody(body) or nil
                if not Pending.shouldSendProgress(entry, remote, self.device_id) then
                    logger.info("CWNGSync: newer position from another device; not sending the queued one")
                    return continue()
                end
                client:update_progress(s.username, s.password, entry.document, entry.progress,
                    entry.percentage, Device.model, self.device_id, function(sent, _body, send_reason)
                        if sent or isFinalRefusal(send_reason) then return continue() end
                        done(false, send_reason)
                    end)
            end)
        end
    end

    if entry.status then
        steps[#steps + 1] = function(continue)
            local book_id = entry.book_id or self:bookIdFor(entry.file)
            local document = entry.document
            if type(document) == "string" and document:match("^book:") then document = nil end
            client:update_read_status(s.username, s.password, Device.model, self.device_id,
                book_id, document, entry.status, function(sent, _body, reason)
                    if sent then
                        self:saveBookSetting(entry.file, PUSHED_STATUS_KEY, entry.status)
                        return continue()
                    end
                    if isFinalRefusal(reason) then return continue() end
                    done(false, reason)
                end)
        end
    end

    if entry.annotations and type(entry.annotations.list) == "table" then
        steps[#steps + 1] = function(continue)
            local list, deletions = entry.annotations.list, entry.annotations.deletions or {}
            if #list == 0 and #deletions == 0 then
                self:saveBookSetting(entry.file, ANNOTATION_WATERMARK_KEY, {})
                return continue()
            end
            client:push_annotations(s.username, s.password, entry.document, list, deletions,
                Device.model, self.device_id,
                function(sent, _body, reason)
                    if sent then
                        self:saveBookSetting(entry.file, ANNOTATION_WATERMARK_KEY,
                            SyncLogic.annotationIds(list))
                        return continue()
                    end
                    if isFinalRefusal(reason) then return continue() end
                    done(false, reason)
                end)
        end
    end

    local index = 0
    local function continue()
        index = index + 1
        local step = steps[index]
        if not step then return done(true) end
        step(continue)
    end
    continue()
end

-- Deliver everything queued, one request at a time. on_done(ok, reason).
function AutoSync:flushPending(on_done)
    local function finish(ok, reason)
        if on_done then on_done(ok, reason) end
    end
    if not self:isConfigured() then return finish(false, "not connected") end
    -- A run whose callbacks never came back (a request lost in a suspend)
    -- must not hold the queue forever.
    if shared.flushing and os.time() - (shared.flushing_since or 0) < 120 then return finish(true) end
    local queue = self:readPending()
    local dropped = Pending.dropOtherAccounts(queue, self:accountOwner())
    if dropped > 0 then
        logger.info("CWNGSync: dropped", dropped, "unsent captures from a previous account")
        self:writePending(queue)
    end
    local documents = {}
    for document, entry in pairs(queue) do
        if type(entry) == "table" then documents[#documents + 1] = document end
    end
    if #documents == 0 then return finish(true) end
    if not NetworkMgr:isConnected() then return finish(false, "offline") end

    shared.flushing = true
    shared.flushing_since = os.time()
    local client = self:newSyncClient()
    local index = 0
    local function nextEntry()
        index = index + 1
        local document = documents[index]
        if not document then
            shared.flushing = false
            return finish(true)
        end
        local entry = queue[document]
        local seq = entry.seq
        local delivered, err = pcall(self.deliverPending, self, client, entry, function(ok, reason)
            if not ok then
                shared.flushing = false
                logger.info("CWNGSync: queued sync kept for later:", reason)
                return finish(false, reason)
            end
            local current = self:readPending()
            if Pending.settle(current, document, seq) then self:writePending(current) end
            UIManager:nextTick(nextEntry)
        end)
        if not delivered then
            shared.flushing = false
            logger.warn("CWNGSync: queued sync failed", err)
            finish(false, tostring(err))
        end
    end
    nextEntry()
end

-- The device is online: deliver what is owed, then bring things in.
function AutoSync:onDeviceOnline(force)
    if not self:isConfigured() or not NetworkMgr:isConnected() then return end
    local now = os.time()
    if not force and shared.last_online and now - shared.last_online < ONLINE_DEBOUNCE then return end
    shared.last_online = now
    local auto = self.settings.auto_sync
    local library = self:libraryEnabled()
    if not auto and not library then return end
    self:flushPending(function()
        if auto and self:hasCurrentDocument() then
            self:getProgress(false, false)
        end
        if library then self:syncLibrary({}) end
        self:syncDeviceCapabilities(false, false)
        self:collectDeliveries(false, false)
    end)
end

-- After waking, look for the connection the device restores by itself; never
-- turn Wi-Fi on from here.
function AutoSync:onlineSoon(attempt)
    attempt = attempt or 1
    if self.online_retry_task then UIManager:unschedule(self.online_retry_task) end
    self.online_retry_task = function()
        self.online_retry_task = nil
        if NetworkMgr:isConnected() then
            self:onDeviceOnline()
        elseif attempt < #ONLINE_RETRY_DELAYS then
            self:onlineSoon(attempt + 1)
        end
    end
    UIManager:scheduleIn(ONLINE_RETRY_DELAYS[attempt], self.online_retry_task)
end

-- Manual "Sync now": everything, with the outcome on screen.
function AutoSync:syncEverythingNow()
    if not self:isConfigured() then
        self:showConnectChoices()
        return
    end
    NetworkMgr:runWhenConnected(function()
        local InfoMessage = require("ui/widget/infomessage")
        local T = require("ffi/util").template
        local _ = require("gettext")
        local function afterQueue(ok, reason)
            if not ok and reason ~= "no book" and reason ~= "not connected" then
                UIManager:show(InfoMessage:new{
                    text = T(_("Could not send your reading to CWNG: %1"), require("CWNGSyncClient").plainReason(reason)),
                    timeout = 5,
                })
            end
            if self:hasCurrentDocument() then
                self:getProgress(false, false)
                if self.settings.sync_annotations then self:syncAnnotations(false) end
            end
            self:syncDeviceCapabilities(false, false)
            self:collectDeliveries(false, false)
            if self:libraryEnabled() then
                self:syncLibrary({ force = true, interactive = true })
            else
                UIManager:show(InfoMessage:new{ text = _("Synced with CWNG."), timeout = 2 })
            end
        end
        if self:hasCurrentDocument() then
            self:queueOpenBook(true, afterQueue)
        else
            self:flushPending(afterQueue)
        end
    end)
end

return AutoSync

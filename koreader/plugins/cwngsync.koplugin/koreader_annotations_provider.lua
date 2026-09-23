-- KOReader-native phase-1 annotation provider.
--
-- Reads the active reader's public ReaderAnnotation collection.  It never
-- parses or writes .sdr files, so KOReader remains the sole owner regardless
-- of document-relative/central/hash metadata storage mode.

local Json = require("json")
local md5 = require("ffi/sha2").md5

local KoboProvider = require("kobo_sqlite_provider")
local Provider = { push_all_local = true, ui = nil, document_digest = nil }

function Provider.setContext(ui, document_digest)
    Provider.ui = ui
    Provider.document_digest = document_digest
end

function Provider.available()
    return Provider.ui ~= nil
        and Provider.ui.annotation ~= nil
        and type(Provider.ui.annotation.annotations) == "table"
end

local function stableId(annotation)
    -- A highlight this device received from the server keeps the server's id,
    -- so pushing it back updates that row rather than making a second one.
    if annotation.cwng_id then return annotation.cwng_id end
    local identity = {
        Provider.document_digest or "",
        annotation.datetime or "",
        annotation.page,
        annotation.pos0,
        annotation.pos1,
    }
    return "koreader-" .. md5(Json.encode(identity))
end

-- Colours KOReader draws, keyed by the names the server speaks: Kobo's five,
-- the web reader's red, and KOReader's own. KOReader has no pink and spells
-- grey "gray".
local KOREADER_COLOR = {
    yellow = "yellow", green = "green", blue = "blue", red = "red",
    grey = "gray", gray = "gray", pink = "purple",
    orange = "orange", olive = "olive", cyan = "cyan", purple = "purple",
}

local function koreaderColor(color)
    return type(color) == "string" and KOREADER_COLOR[color:lower()] or nil
end

local function stringOrNil(value)
    return type(value) == "string" and value or nil
end

-- A note with words in it; KOReader and the server both write an empty one.
local function noteOrNil(value)
    if type(value) ~= "string" or not value:find("%S") then return nil end
    return value
end

local function toPortable(annotation)
    if annotation.cwng_id then
        -- Received from the server. Send back only what the user changed
        -- here since the server's copy was last taken: the plugin snapshots
        -- highlights when a book closes and sends them on the next open,
        -- before it pulls, so sending the rest would put this older copy over
        -- an edit made elsewhere in the meantime. The anchor, origin and
        -- device of origin are always the server's.
        local base = type(annotation.cwng_base) == "table" and annotation.cwng_base or {}
        local portable = {
            annotation_id = annotation.cwng_id,
            highlighted_text = annotation.text,
            hidden = false,
        }
        if noteOrNil(annotation.note) ~= noteOrNil(base.note) then
            portable.note_text = noteOrNil(annotation.note) or ""
        end
        if annotation.color ~= base.drawn_color then
            portable.color = annotation.color
        end
        return portable
    end
    local rolling = type(annotation.page) == "string"
    return {
        annotation_id = stableId(annotation),
        highlighted_text = annotation.text,
        note_text = annotation.note,
        color = annotation.color,
        source = "koreader",
        position_type = rolling and "koreader_xpointer" or nil,
        start_xpointer = rolling and (annotation.pos0 or annotation.page) or nil,
        end_xpointer = rolling and annotation.pos1 or nil,
        chapter_progress = nil,
        hidden = false,
        device_origin_id = stableId(annotation),
    }
end

-- Returns nil, not {}, when the reader's annotation collection cannot be read.
--
-- The provider is chosen once, before the pull, and read again after it — a gap
-- of up to one HTTP round trip (ANNOTATION_TIMEOUTS, 15s), during which the user
-- can close the book and KOReader can tear `ui.annotation` down. Returning {}
-- there would say "the user deleted every highlight" and the caller would name
-- each one to the server, which obeys explicit deletes and never un-hides a
-- tombstone (#920). Unreadable is not empty.
function Provider.readAll(_volume_id)
    if not Provider.available() then return nil end
    local out = {}
    for _, annotation in ipairs(Provider.ui.annotation.annotations) do
        -- A page bookmark with neither selected text nor note is not a
        -- highlight/annotation and stays in KOReader only.
        if annotation.text or annotation.note then
            out[#out + 1] = toPortable(annotation)
        end
    end
    return out
end

-- The words a highlight covers, as a reader sees them: line breaks, runs of
-- spaces and soft hyphens are layout, not text.
local function readableText(text)
    if type(text) ~= "string" then return "" end
    text = text:gsub("\194\173", "")
    text = text:gsub("%s+", " ")
    text = text:gsub("^ ", "")
    text = text:gsub(" $", "")
    return text
end

-- Where a server highlight goes in the open book, or nil when this device
-- cannot place it faithfully. Its XPointers must resolve in this file, and the
-- words there must be the words the user highlighted: an anchor converted from
-- another reader, or taken from another edition, that lands a sentence off is
-- dropped rather than drawn over the wrong words.
local function placement(document, portable)
    local pos0, pos1 = portable.start_xpointer, portable.end_xpointer
    if type(pos0) ~= "string" or type(pos1) ~= "string" or pos0 == "" or pos1 == "" then
        return nil
    end
    if not (document:isXPointerInDocument(pos0) and document:isXPointerInDocument(pos1)) then
        return nil
    end
    local ok, text = pcall(document.getTextFromXPointers, document, pos0, pos1)
    if not ok or readableText(text) == "" or readableText(text) ~= readableText(portable.highlighted_text) then
        return nil
    end
    return pos0, pos1
end

-- Ids of server highlights the user deleted on this device. The server keeps
-- a web reader's highlight when a KOReader device names it deleted (a device
-- may only delete its own kind), so it goes on sending it; this is what stops
-- the next sync from putting it back.
local DISMISSED_KEY = "cwngsync_dismissed_annotation_ids"

local function dismissedSet(ui, deletions)
    local settings = ui.doc_settings
    local stored = settings and settings.readSetting and settings:readSetting(DISMISSED_KEY) or {}
    local set, grew = {}, false
    for _, id in ipairs(stored) do set[id] = true end
    for _, id in ipairs(deletions or {}) do
        if not set[id] then
            set[id] = true
            stored[#stored + 1] = id
            grew = true
        end
    end
    if grew and settings.saveSetting then settings:saveSetting(DISMISSED_KEY, stored) end
    return set
end

-- Edits to a highlight received from the server, merged three ways against
-- the server's copy when it was last taken (`base`). An edit made here wins
-- and is pushed up; an edit made on the server lands here only when this
-- device has not changed that field since. Each returns the event KOReader
-- fires for the same edit, or nil when nothing changed.
local function mergeNote(item, base, remote)
    remote = noteOrNil(remote)
    local had = noteOrNil(item.note)
    if remote == had then
        base.note = remote
        return nil
    end
    if had ~= noteOrNil(base.note) then return nil end
    item.note = remote
    base.note = remote
    if had and remote then return { item } end
    if remote then return { item, nb_highlights_added = -1, nb_notes_added = 1 } end
    return { item, nb_highlights_added = 1, nb_notes_added = -1 }
end

local function mergeColor(item, base, remote)
    local drawn = koreaderColor(remote)
    -- A colour this reader cannot name leaves the one it has.
    if not drawn then return nil end
    if drawn == item.color then
        base.color, base.drawn_color = remote, drawn
        return nil
    end
    if item.color ~= base.drawn_color then return nil end
    item.color = drawn
    base.color, base.drawn_color = remote, drawn
    return { item }
end

local function applyNative(portables, deletions)
    local ui = Provider.ui
    local document = ui.document
    -- XPointers exist only in reflowable documents; a PDF's highlights are
    -- page boxes, which the server does not carry.
    if not (ui.rolling and document and document.getTextFromXPointers) then return 0 end
    local Event = require("ui/event")
    local UIManager = require("ui/uimanager")

    local dismissed = dismissedSet(ui, deletions)
    local annotations = ui.annotation.annotations
    local by_id, by_range = {}, {}
    for _, item in ipairs(annotations) do
        by_id[stableId(item)] = item
        if item.pos0 and item.pos1 then by_range[tostring(item.pos0) .. "|" .. tostring(item.pos1)] = true end
    end

    local changed = 0
    for _, portable in ipairs(portables or {}) do
        local id = portable.annotation_id
        local item = id and by_id[id]
        if item and portable.hidden == true then
            -- Only a highlight this device received from the server. One the
            -- user made here is deleted here, by them: tombstones written
            -- before #920 are not proof that anyone deleted it.
            if item.cwng_id then
                for index, candidate in ipairs(annotations) do
                    if candidate == item then
                        ui.bookmark:removeItemByIndex(index)
                        break
                    end
                end
                by_id[id] = nil
                changed = changed + 1
            end
        elseif item and item.cwng_id and type(item.cwng_base) == "table" then
            local note_event = mergeNote(item, item.cwng_base, portable.note_text)
            local color_event = mergeColor(item, item.cwng_base, portable.color)
            if note_event or color_event then
                ui:handleEvent(Event:new("AnnotationsModified", note_event or color_event))
                changed = changed + 1
            end
        elseif not item and id and portable.hidden ~= true and not dismissed[id] then
            local pos0, pos1 = placement(document, portable)
            if pos0 and not by_range[pos0 .. "|" .. pos1] then
                local highlight = ui.view and ui.view.highlight or {}
                local note = noteOrNil(portable.note_text)
                local chapter
                if ui.toc and ui.toc.getTocTitleByPage then
                    local ok, title = pcall(ui.toc.getTocTitleByPage, ui.toc, pos0)
                    chapter = ok and title or nil
                end
                local new_item = {
                    page = pos0,
                    pos0 = pos0,
                    pos1 = pos1,
                    text = portable.highlighted_text,
                    note = note,
                    drawer = highlight.saved_drawer or "lighten",
                    color = koreaderColor(portable.color) or highlight.saved_color or "yellow",
                    chapter = chapter,
                    cwng_id = id,
                }
                new_item.cwng_base = { note = note, color = stringOrNil(portable.color), drawn_color = new_item.color }
                local index = ui.annotation:addItem(new_item)
                -- The same event KOReader fires for a highlight the user makes,
                -- so its counts, footer and statistics stay right.
                ui:handleEvent(Event:new("AnnotationsModified", {
                    new_item,
                    nb_highlights_added = not note and 1 or nil,
                    nb_notes_added = note and 1 or nil,
                    index_modified = index,
                }))
                by_id[id] = new_item
                by_range[pos0 .. "|" .. pos1] = true
                changed = changed + 1
            end
        end
    end
    if changed > 0 then UIManager:setDirty(ui.dialog, "ui") end
    return changed
end

-- `deletions` are the ids the user deleted on this device since the last
-- push, as SyncLogic.planLocalContribution named them.
function Provider.applyToDevice(portables, volume_id, deletions)
    -- A CW-synced kepub on a Kobo carries a VolumeID; its highlights also
    -- belong in KoboReader.sqlite, so stock Nickel shows them (the shipped
    -- Kobo bridge).
    if volume_id and KoboProvider.available() then
        return KoboProvider.applyToDevice(portables, volume_id)
    end
    if not Provider.available() then return 0 end
    return applyNative(portables, deletions)
end

return Provider

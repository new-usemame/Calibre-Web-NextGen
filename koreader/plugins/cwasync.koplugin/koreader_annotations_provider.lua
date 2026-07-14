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
    local identity = {
        Provider.document_digest or "",
        annotation.datetime or "",
        annotation.page,
        annotation.pos0,
        annotation.pos1,
    }
    return "koreader-" .. md5(Json.encode(identity))
end

local function toPortable(annotation)
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

function Provider.readAll(_volume_id)
    if not Provider.available() then return {} end
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

function Provider.applyToDevice(portables, volume_id)
    -- Preserve the already-shipped Kobo/Nickel bridge when KOReader happens
    -- to run on a Kobo. Other devices remain phase-1 push-only.
    if KoboProvider.available() then
        return KoboProvider.applyToDevice(portables, volume_id)
    end
    return 0
end

return Provider

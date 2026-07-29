package.path = table.concat({
    "./?.lua",
    "../?.lua",
    package.path,
}, ";")

-- Pure field-mapping helpers of the KoboReader.sqlite provider. The actual
-- sqlite I/O is verified on-device (it needs KOReader's sqlite FFI); these
-- helpers carry the risky get-it-right logic (color codes, the dotted-selector
-- escaping Kobo uses, the Bookmark row shape) and must be requirable + testable
-- without that FFI.
local KP = require("kobo_sqlite_provider")

local function assertEqual(actual, expected, message)
    if actual ~= expected then
        error(string.format("%s\nexpected: %s\nactual: %s", message, tostring(expected), tostring(actual)), 2)
    end
end

local function testColorMapping()
    assertEqual(KP.colorNameToKoboInt("yellow"), 0, "yellow -> 0")
    assertEqual(KP.colorNameToKoboInt("red"), 1, "red -> 1")
    assertEqual(KP.colorNameToKoboInt("green"), 2, "green -> 2")
    assertEqual(KP.colorNameToKoboInt("blue"), 3, "blue -> 3")
    assertEqual(KP.colorNameToKoboInt("chartreuse"), 0, "unknown -> 0 (yellow)")
    assertEqual(KP.colorNameToKoboInt(nil), 0, "nil -> 0")
    assertEqual(KP.koboIntToColorName(0), "yellow", "0 -> yellow")
    assertEqual(KP.koboIntToColorName(3), "blue", "3 -> blue")
    assertEqual(KP.koboIntToColorName(99), "yellow", "out-of-range -> yellow")
end

local function testSelectorEscaping()
    -- Kobo stores StartContainerPath as a CSS selector with backslash-escaped
    -- dots: span#kobo\.4\.1
    assertEqual(KP.escapeKoboSpanSelector("kobo.4.1"), "span#kobo\\.4\\.1", "dots escaped + span# prefix")
    assertEqual(KP.extractKoboSpanId("span#kobo\\.4\\.1"), "kobo.4.1", "round-trips back to the bare id")
    assertEqual(KP.extractKoboSpanId("span#kobo.0.15"), "kobo.0.15", "tolerates unescaped form too")
end

local function testBuildBookmarkRow()
    local portable = {
        annotation_id = "cwn-web-abc", highlighted_text = "the passage",
        note_text = "my note", color = "green",
        content_id = "bk-uuid!!OEBPS/c1.xhtml",
        start_kobospan = "kobo.4.1", start_offset = 3,
        end_kobospan = "kobo.4.2", end_offset = 17,
        context_string = "...around the passage...",
    }
    local row = KP.buildBookmarkRow(portable, "bk-uuid")
    assertEqual(row.BookmarkID, "cwn-web-abc", "BookmarkID = annotation_id")
    assertEqual(row.VolumeID, "bk-uuid", "VolumeID = volume id")
    assertEqual(row.ContentID, "bk-uuid!!OEBPS/c1.xhtml", "ContentID passthrough")
    assertEqual(row.StartContainerPath, "span#kobo\\.4\\.1", "start selector escaped")
    assertEqual(row.StartContainerChildIndex, -99, "start child index sentinel")
    assertEqual(row.StartOffset, 3, "start offset")
    assertEqual(row.EndContainerPath, "span#kobo\\.4\\.2", "end selector escaped")
    -- EndContainerChildIndex is NOT NULL with no default in the real Kobo
    -- Bookmark schema — must be supplied or the INSERT is rejected on-device.
    assertEqual(row.EndContainerChildIndex, -99, "end child index sentinel")
    assertEqual(row.EndOffset, 17, "end offset")
    assertEqual(row.Text, "the passage", "Text = highlighted_text")
    assertEqual(row.Annotation, "my note", "Annotation = note_text")
    assertEqual(row.Color, 2, "Color = green int")
    assertEqual(row.Type, "highlight", "Type = highlight")
end

local function testBookmarkRowToPortable()
    local row = {
        BookmarkID = "dev-1", VolumeID = "bk-uuid",
        ContentID = "bk-uuid!!OEBPS/c1.xhtml",
        StartContainerPath = "span#kobo\\.4\\.1", StartOffset = 0,
        EndContainerPath = "span#kobo\\.4\\.2", EndOffset = 9,
        Text = "passage", Annotation = "note", Color = 1,
        ContextString = "ctx", ChapterProgress = 0.42,
    }
    local p = KP.bookmarkRowToPortable(row)
    assertEqual(p.annotation_id, "dev-1", "annotation_id = BookmarkID")
    assertEqual(p.color, "red", "Color 1 -> red")
    assertEqual(p.start_kobospan, "kobo.4.1", "start span extracted")
    assertEqual(p.end_kobospan, "kobo.4.2", "end span extracted")
    assertEqual(p.start_offset, 0, "start offset")
    assertEqual(p.end_offset, 9, "end offset")
    assertEqual(p.highlighted_text, "passage", "text")
    assertEqual(p.note_text, "note", "note")
    assertEqual(p.content_id, "bk-uuid!!OEBPS/c1.xhtml", "content_id")
    assertEqual(p.chapter_progress, 0.42, "chapter progress carried")
end

-- The read contract both providers owe their caller: nil when the device could
-- not be read, {} only when it genuinely holds no highlights. The caller turns
-- a list into "the user deleted everything they are missing", so answering an
-- impossible read with {} is what makes a transient failure permanent (#920).
--
-- Both failure paths are reachable from a plain Lua host, which is the point:
-- off-device is exactly where the DB is absent and the reader does not exist,
-- so the unreadable case is the one this harness can execute for real.
local function testUnreadableProvidersReportNil()
    -- The native provider imports two KOReader modules at load time, and both
    -- are only reached from the id/portable helpers, not from the read contract
    -- under test. Stubbing them at the loader keeps `available()` and
    -- `readAll()` themselves real rather than re-implemented here.
    package.preload["json"] = function()
        return { encode = function(v) return tostring(v) end }
    end
    package.preload["ffi/sha2"] = function()
        return { md5 = function(s) return "md5-" .. tostring(s) end }
    end

    -- KoboReader.sqlite: `open_db` requires KOReader's lua-ljsqlite3, which is
    -- not here, so this exercises the genuine "the database would not open"
    -- exit rather than a stub of it.
    assertEqual(KP.readAll("vol-1"), nil,
        "a KoboReader.sqlite that will not open has read nothing, not an empty device")

    -- The KOReader-native provider with no reader attached -- the state the
    -- plugin lands in when the user closes the book while the pull is in flight.
    local Native = require("koreader_annotations_provider")
    Native.setContext(nil, nil)
    assertEqual(Native.available(), false, "no reader means the provider cannot read")
    assertEqual(Native.readAll(), nil,
        "a torn-down reader has read nothing, not an empty device")

    -- And with a reader holding no highlights, the same provider says {} --
    -- a real answer, which the caller may act on.
    Native.setContext({ annotation = { annotations = {} } }, "digest")
    local live = Native.readAll()
    assertEqual(type(live), "table", "an attached reader returns a real list")
    assertEqual(#live, 0, "which is empty when the book has no highlights")
    Native.setContext(nil, nil)
end

testColorMapping()
testSelectorEscaping()
testBuildBookmarkRow()
testBookmarkRowToPortable()
testUnreadableProvidersReportNil()

print("device_annotations (kobo_sqlite_provider) tests passed")

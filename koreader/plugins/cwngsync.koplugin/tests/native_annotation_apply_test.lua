package.path = table.concat({
    "./?.lua",
    "../?.lua",
    package.path,
}, ";")

-- Server highlights written into KOReader's own annotations, on any device
-- KOReader runs on (a Kindle has no KoboReader.sqlite to write them to).
--
-- The reader is a fake shaped like KOReader 2026.07's, as read on the Kindle:
-- ReaderAnnotation:addItem, ReaderBookmark:removeItemByIndex, CreDocument's
-- isXPointerInDocument / getTextFromXPointers, and the AnnotationsModified
-- event KOReader fires for its own highlights. The provider under test is real.

package.preload["json"] = function()
    return { encode = function(v)
        local parts = {}
        for i = 1, 5 do parts[i] = tostring(v[i]) end
        return table.concat(parts, "|")
    end }
end
package.preload["ffi/sha2"] = function()
    return { md5 = function(s) return "md5(" .. s .. ")" end }
end
package.preload["ui/event"] = function()
    return { new = function(_, name, args) return { name = name, args = args } end }
end
local dirty = 0
package.preload["ui/uimanager"] = function()
    return { setDirty = function() dirty = dirty + 1 end }
end

local Native = require("koreader_annotations_provider")

local function assertEqual(actual, expected, message)
    if actual ~= expected then
        error(string.format("%s\nexpected: %s\nactual: %s", message, tostring(expected), tostring(actual)), 2)
    end
end

-- What the book holds at each anchor, as crengine would report it.
local P1_START = "/body/DocFragment[3]/body/p[4]/text().0"
local P1_END = "/body/DocFragment[3]/body/p[4]/text().24"
local P2_START = "/body/DocFragment[5]/body/p[2]/text().10"
local P2_END = "/body/DocFragment[5]/body/p[2]/text().41"
local TEXT = {
    [P1_START .. "|" .. P1_END] = "One morning, when Gregor",
    [P2_START .. "|" .. P2_END] = "a horrible\n  vermin.\194\173",
}

local function fakeReader(opts)
    opts = opts or {}
    local ui = { events = {}, dialog = {}, settings = {} }
    ui.rolling = not opts.paged and {} or nil
    ui.annotation = { annotations = {} }
    function ui.annotation:addItem(item)
        item.datetime = item.datetime or "2026-09-23 23:10:00"
        item.pageno = 7
        table.insert(self.annotations, item)
        return #self.annotations
    end
    ui.bookmark = {}
    function ui.bookmark:removeItemByIndex(index)
        local item = table.remove(ui.annotation.annotations, index)
        ui.events[#ui.events + 1] = { name = "AnnotationsModified", args = { item, index_modified = -index } }
    end
    ui.document = {}
    function ui.document:isXPointerInDocument(xp)
        return xp == P1_START or xp == P1_END or xp == P2_START or xp == P2_END
    end
    function ui.document:getTextFromXPointers(pos0, pos1)
        return TEXT[pos0 .. "|" .. pos1]
    end
    ui.view = { highlight = { saved_drawer = "lighten", saved_color = "gray" } }
    ui.toc = { getTocTitleByPage = function() return "Chapter I" end }
    ui.doc_settings = {}
    function ui.doc_settings:readSetting(key) return ui.settings[key] end
    function ui.doc_settings:saveSetting(key, value) ui.settings[key] = value end
    function ui:handleEvent(event) self.events[#self.events + 1] = event end
    Native.setContext(ui, "digest-221")
    return ui
end

local function webHighlight(fields)
    local h = {
        annotation_id = "web-1",
        highlighted_text = "One morning, when Gregor",
        color = "yellow",
        source = "webreader",
        position_type = "cfi",
        start_xpointer = P1_START,
        end_xpointer = P1_END,
        hidden = false,
        last_synced = "2026-09-23T23:00:00+00:00",
    }
    for k, v in pairs(fields or {}) do h[k] = v end
    return h
end

local function testAWebHighlightIsDrawnWhereItsWordsAre()
    local ui = fakeReader()
    local applied = Native.applyToDevice({ webHighlight() }, nil, {})
    assertEqual(applied, 1, "one highlight added")
    local item = ui.annotation.annotations[1]
    assertEqual(item.page, P1_START, "a rolling highlight is paged by its start")
    assertEqual(item.pos0, P1_START, "starts at the server's start")
    assertEqual(item.pos1, P1_END, "ends at the server's end")
    assertEqual(item.text, "One morning, when Gregor", "keeps the words")
    assertEqual(item.drawer, "lighten", "drawn the way this reader draws its own")
    assertEqual(item.color, "yellow", "in the colour it was made in")
    assertEqual(item.chapter, "Chapter I", "filed under its chapter")
    assertEqual(ui.events[1].name, "AnnotationsModified", "KOReader is told, as for its own highlights")
    assertEqual(ui.events[1].args[1], item, "about this highlight")
    assertEqual(ui.events[1].args.nb_highlights_added, 1, "counted as a highlight")
    assert(dirty > 0, "the page is redrawn")

    -- The next sync pulls the same set again.
    assertEqual(Native.applyToDevice({ webHighlight() }, nil, {}), 0, "a second pull adds nothing")
    assertEqual(#ui.annotation.annotations, 1, "and draws it once")

    -- And the device reports it back under the server's own id, without an
    -- anchor or origin of its own, so the push updates that row in place.
    local read = Native.readAll()
    assertEqual(#read, 1, "read back once")
    assertEqual(read[1].annotation_id, "web-1", "under the server's id")
    assertEqual(read[1].start_xpointer, nil, "the server keeps its own anchor")
    assertEqual(read[1].position_type, nil, "and its own position type")
    assertEqual(read[1].source, nil, "and its own origin")
    assertEqual(read[1].device_origin_id, nil, "and its own device of origin")
end

local function testANoteComesWithItsHighlight()
    local ui = fakeReader()
    Native.applyToDevice({ webHighlight({ note_text = "Kafka's opening" }) }, nil, {})
    local item = ui.annotation.annotations[1]
    assertEqual(item.note, "Kafka's opening", "the note arrives")
    assertEqual(ui.events[1].args.nb_notes_added, 1, "and KOReader counts it as a note, as it does its own")
    assertEqual(ui.events[1].args.nb_highlights_added, nil, "not as a bare highlight")
end

local function testWordsAreComparedAsTheyRead()
    local ui = fakeReader()
    local applied = Native.applyToDevice({ webHighlight({
        annotation_id = "web-2",
        start_xpointer = P2_START,
        end_xpointer = P2_END,
        highlighted_text = "a horrible vermin.",
    }) }, nil, {})
    assertEqual(applied, 1, "line breaks, runs of spaces and soft hyphens are not differences")
    assertEqual(#ui.annotation.annotations, 1, "so the highlight is drawn")
end

local function testAnAnchorThatDoesNotFitIsNeverDrawn()
    local ui = fakeReader()
    local cfi_only = webHighlight({ annotation_id = "web-cfi-only" })
    cfi_only.start_xpointer, cfi_only.end_xpointer = nil, nil
    local applied = Native.applyToDevice({
        -- A conversion that landed on the right paragraph but the wrong words.
        webHighlight({ annotation_id = "web-wrong", highlighted_text = "He lay on his armour-like back" }),
        -- An anchor from another edition of the book.
        webHighlight({ annotation_id = "web-elsewhere", start_xpointer = "/body/DocFragment[40]/body/p[1]/text().0" }),
        -- A web highlight the server could not convert yet.
        cfi_only,
    }, nil, {})
    assertEqual(applied, 0, "nothing is placed")
    assertEqual(#ui.annotation.annotations, 0, "rather than over the wrong words")
end

local function testAPassageHighlightedInBothPlacesIsDrawnOnce()
    local ui = fakeReader()
    table.insert(ui.annotation.annotations, { page = P1_START, pos0 = P1_START, pos1 = P1_END,
        text = "One morning, when Gregor", datetime = "2026-09-20 10:00:00" })
    assertEqual(Native.applyToDevice({ webHighlight() }, nil, {}), 0,
        "the web's highlight of words already highlighted here adds nothing")
    assertEqual(#ui.annotation.annotations, 1, "the passage is drawn once, not twice over itself")
end

local function testAPageBasedDocumentIsLeftAlone()
    local ui = fakeReader({ paged = true })
    assertEqual(Native.applyToDevice({ webHighlight() }, nil, {}), 0, "a PDF has no XPointers to place by")
    assertEqual(#ui.annotation.annotations, 0, "so nothing is added")
end

local function testAServerDeletionRemovesOnlyWhatTheServerGave()
    local ui = fakeReader()
    Native.applyToDevice({ webHighlight() }, nil, {})
    -- A highlight the user made on this device, which the server has marked
    -- deleted. Tombstones from before #920 exist on real servers, so a hidden
    -- row is not proof the user deleted a highlight this device authored.
    local own = { page = P2_START, pos0 = P2_START, pos1 = P2_END, text = "a horrible vermin.",
                  datetime = "2026-09-20 10:00:00" }
    table.insert(ui.annotation.annotations, own)
    local own_id = Native.readAll()[2].annotation_id

    local removed = Native.applyToDevice({
        webHighlight({ hidden = true }),
        { annotation_id = own_id, hidden = true, highlighted_text = "a horrible vermin." },
    }, nil, {})
    assertEqual(removed, 1, "one removal")
    assertEqual(#ui.annotation.annotations, 1, "the web highlight deleted on the web is gone")
    assertEqual(ui.annotation.annotations[1], own, "the device's own highlight stays")
end

local function testAHighlightDeletedHereStaysDeleted()
    local ui = fakeReader()
    Native.applyToDevice({ webHighlight() }, nil, {})
    -- The user deletes it on the Kindle. The server refuses to delete a web
    -- reader's row on a KOReader device's word, so it keeps sending it.
    table.remove(ui.annotation.annotations, 1)
    assertEqual(Native.applyToDevice({ webHighlight() }, nil, { "web-1" }), 0,
        "the sync that carries the deletion does not bring it back")
    assertEqual(Native.applyToDevice({ webHighlight() }, nil, {}), 0, "nor does any later one")
    assertEqual(#ui.annotation.annotations, 0, "it stays deleted on this device")
end

local function testNoteEditsMeetInTheMiddle()
    local ui = fakeReader()
    Native.applyToDevice({ webHighlight({ note_text = "first" }) }, nil, {})
    local item = ui.annotation.annotations[1]

    -- Edited on the web, untouched here: the web's words arrive.
    assertEqual(Native.applyToDevice({ webHighlight({ note_text = "edited on the web" }) }, nil, {}), 1,
        "the web's edit is applied")
    assertEqual(item.note, "edited on the web", "to the note here")
    assertEqual(Native.readAll()[1].note_text, nil,
        "an unchanged note is not sent back, so this copy never overwrites a newer edit made elsewhere")

    -- Edited here since: the server still has the older words, and they do not
    -- overwrite the newer ones. The push carries these up.
    item.note = "edited on the Kindle"
    Native.applyToDevice({ webHighlight({ note_text = "edited on the web" }) }, nil, {})
    assertEqual(item.note, "edited on the Kindle", "this device's newer edit is kept")
    assertEqual(Native.readAll()[1].note_text, "edited on the Kindle", "and is what goes up")

    -- Deleting the note here is an edit too, and must reach the server as one.
    item.note = nil
    assertEqual(Native.readAll()[1].note_text, "", "a note removed here goes up as removed")

    -- A note removed on the web leaves this device when it was untouched here.
    local ui2 = fakeReader()
    Native.applyToDevice({ webHighlight({ note_text = "first" }) }, nil, {})
    Native.applyToDevice({ webHighlight({ note_text = "" }) }, nil, {})
    assertEqual(ui2.annotation.annotations[1].note, nil, "the web's removal lands here")
    assertEqual(ui2.events[#ui2.events].args.nb_notes_added, -1, "and KOReader counts a note fewer")
    assertEqual(ui2.events[#ui2.events].args.nb_highlights_added, 1, "and a bare highlight more")
end

local function testColoursSurviveTheRoundTrip()
    local ui = fakeReader()
    Native.applyToDevice({ webHighlight({ color = "pink" }) }, nil, {})
    local item = ui.annotation.annotations[1]
    assertEqual(item.color, "purple", "KOReader has no pink; the nearest it draws")
    assertEqual(Native.readAll()[1].color, nil, "the stand-in is never sent, so the server keeps pink")

    item.color = "green"
    assertEqual(Native.readAll()[1].color, "green", "a colour chosen here goes up as chosen")
    Native.applyToDevice({ webHighlight({ color = "red" }) }, nil, {})
    assertEqual(item.color, "green", "and a different colour from the server does not undo it")

    fakeReader()
    Native.applyToDevice({ webHighlight({ color = "grey" }) }, nil, {})
    local grey = Native.ui.annotation.annotations[1]
    assertEqual(grey.color, "gray", "KOReader spells it gray")
    Native.applyToDevice({ webHighlight({ color = "blue" }) }, nil, {})
    assertEqual(grey.color, "blue", "a colour changed on the web lands here")
    Native.applyToDevice({ webHighlight({ color = "#123456" }) }, nil, {})
    assertEqual(grey.color, "blue", "a colour this reader cannot name leaves the one it has")
end

testAWebHighlightIsDrawnWhereItsWordsAre()
testANoteComesWithItsHighlight()
testWordsAreComparedAsTheyRead()
testAnAnchorThatDoesNotFitIsNeverDrawn()
testAPassageHighlightedInBothPlacesIsDrawnOnce()
testAPageBasedDocumentIsLeftAlone()
testAServerDeletionRemovesOnlyWhatTheServerGave()
testAHighlightDeletedHereStaysDeleted()
testNoteEditsMeetInTheMiddle()
testColoursSurviveTheRoundTrip()

print("native_annotation_apply tests passed")

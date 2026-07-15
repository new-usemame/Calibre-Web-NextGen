package.path = table.concat({
    "./?.lua",
    "../?.lua",
    package.path,
}, ";")

local SyncLogic = require("sync_logic")

local function assertEqual(actual, expected, message)
    if actual ~= expected then
        error(string.format("%s\nexpected: %s\nactual: %s", message, tostring(expected), tostring(actual)), 2)
    end
end

local function testIsRemoteProgressFromThisDevice()
    assertEqual(SyncLogic.isRemoteProgressFromThisDevice({ device = "Foo", device_id = "abc" }, "Foo", "abc"), true,
        "same device payload should match")
    assertEqual(SyncLogic.isRemoteProgressFromThisDevice({ device = "Foo", device_id = "xyz" }, "Foo", "abc"), false,
        "different device_id should not match")
    assertEqual(SyncLogic.isRemoteProgressFromThisDevice({ device = "Bar", device_id = "abc" }, "Foo", "abc"), false,
        "different device model should not match")
    assertEqual(SyncLogic.isRemoteProgressFromThisDevice(nil, "Foo", "abc"), false,
        "non-table payload should not match")
end

local function testDidBookProgressChange()
    local previous = {
        percent_finished = 0.5,
        last_page = 12,
        last_xpointer = nil,
        status = "reading",
    }
    assertEqual(SyncLogic.didBookProgressChange(previous, {
        percent_finished = 0.5,
        last_page = 12,
        last_xpointer = nil,
        status = "reading",
    }), false, "identical state should not count as changed")
    assertEqual(SyncLogic.didBookProgressChange(previous, {
        percent_finished = 1,
        last_page = 12,
        last_xpointer = nil,
        status = "complete",
    }), true, "percent/status changes should count as changed")
    assertEqual(SyncLogic.didBookProgressChange(previous, {
        percent_finished = 0.5,
        last_page = nil,
        last_xpointer = "/body/1/4",
        status = "reading",
    }), true, "switching from page to xpointer should count as changed")
end

local function assertTrue(cond, message)
    if not cond then error(message, 2) end
end

local function findById(list, id)
    for _, a in ipairs(list) do
        if a.annotation_id == id then return a end
    end
    return nil
end

-- Phase 2: annotation merge (last-synced-wins; position immutable).
local function testMergeAnnotation()
    local older = { annotation_id = "x", color = "yellow", note_text = "old",
                    hidden = false, start_kobospan = "kobo.1.1", last_synced = "2026-05-01T00:00:00Z" }
    local newer = { annotation_id = "x", color = "red", note_text = "new",
                    hidden = false, start_kobospan = "kobo.1.1", last_synced = "2026-05-02T00:00:00Z" }
    local m = SyncLogic.mergeAnnotation(older, newer)
    assertEqual(m.color, "red", "newer color wins")
    assertEqual(m.note_text, "new", "newer note wins")
    -- order-independent: newer is still the winner when args are swapped
    local m2 = SyncLogic.mergeAnnotation(newer, older)
    assertEqual(m2.color, "red", "newer wins regardless of arg order")
    -- a newer delete wins (delete honored)
    local del = { annotation_id = "x", hidden = true, last_synced = "2026-05-03T00:00:00Z" }
    assertEqual(SyncLogic.mergeAnnotation(newer, del).hidden, true, "newer delete wins")
    -- position preserved even if the newer payload omits it
    assertEqual(SyncLogic.mergeAnnotation(older, del).start_kobospan, "kobo.1.1", "position immutable / preserved")
end

-- Phase 2: diff (which annotations flow to the device vs the server).
local function testDiffAnnotations()
    local localList = {
        { annotation_id = "both-local-newer", color = "red",    last_synced = "2026-05-02T00:00:00Z" },
        { annotation_id = "both-equal",       color = "yellow", last_synced = "2026-05-01T00:00:00Z" },
        { annotation_id = "local-only",       color = "green",  last_synced = "2026-05-01T00:00:00Z" },
        { annotation_id = "both-remote-newer",color = "yellow", last_synced = "2026-05-01T00:00:00Z" },
    }
    local remoteList = {
        { annotation_id = "both-local-newer", color = "yellow", last_synced = "2026-05-01T00:00:00Z" },
        { annotation_id = "both-equal",       color = "yellow", last_synced = "2026-05-01T00:00:00Z" },
        { annotation_id = "remote-only",      color = "blue",   last_synced = "2026-05-01T00:00:00Z" },
        { annotation_id = "both-remote-newer",color = "blue",   last_synced = "2026-05-09T00:00:00Z" },
    }

    local d = SyncLogic.diffAnnotations(localList, remoteList)

    -- apply_to_device: remote-only + both-remote-newer
    assertTrue(findById(d.apply_to_device, "remote-only") ~= nil, "remote-only applies to device")
    assertTrue(findById(d.apply_to_device, "both-remote-newer") ~= nil, "remote-newer applies to device")
    assertTrue(findById(d.apply_to_device, "both-equal") == nil, "converged row not re-applied (no echo)")
    assertTrue(findById(d.apply_to_device, "local-only") == nil, "local-only never applies to device")

    -- send_to_server: local-only + both-local-newer
    assertTrue(findById(d.send_to_server, "local-only") ~= nil, "local-only pushes to server")
    assertTrue(findById(d.send_to_server, "both-local-newer") ~= nil, "local-newer pushes to server")
    assertTrue(findById(d.send_to_server, "both-equal") == nil, "converged row not re-pushed (no echo)")
end

-- #920: the device names its deletions, because the server cannot infer them.
local function testComputeDeletions()
    local function ids(list)
        local out = {}
        for _, v in ipairs(list) do out[#out + 1] = v end
        table.sort(out)
        return table.concat(out, ",")
    end

    -- The #905 case: pushed two, user deleted one.
    assertEqual(
        ids(SyncLogic.computeDeletions({ "a", "b" }, { { annotation_id = "a" } })),
        "b", "an id in the watermark but not live was deleted by the user")

    -- The #905 edge the reap existed for: the last highlight is gone.
    assertEqual(
        ids(SyncLogic.computeDeletions({ "a" }, {})),
        "a", "deleting the last highlight is still a deletion")

    -- The #920 case: a second device has an empty local set, but it never
    -- pushed anything, so it has deleted nothing and must say so.
    assertEqual(
        ids(SyncLogic.computeDeletions({}, {})),
        "", "an empty watermark yields no deletions, whatever the server holds")

    -- Nothing changed.
    assertEqual(
        ids(SyncLogic.computeDeletions({ "a" }, { { annotation_id = "a" } })),
        "", "a live id is not a deletion")

    -- A highlight created since the last push is not a deletion.
    assertEqual(
        ids(SyncLogic.computeDeletions({}, { { annotation_id = "new" } })),
        "", "a new local highlight is not a deletion")

    -- Fails safe when the watermark is missing entirely (fresh install).
    assertEqual(ids(SyncLogic.computeDeletions(nil, nil)), "",
        "a missing watermark deletes nothing")
end

local function testAnnotationIds()
    local sorted = SyncLogic.annotationIds({
        { annotation_id = "b" }, { annotation_id = "a" }, { no_id = true },
    })
    assertEqual(table.concat(sorted, ","), "a,b",
        "ids are sorted and entries without an id are skipped")
    assertEqual(#SyncLogic.annotationIds(nil), 0, "nil list yields no ids")
end

testIsRemoteProgressFromThisDevice()
testDidBookProgressChange()
testMergeAnnotation()
testDiffAnnotations()
testComputeDeletions()
testAnnotationIds()

print("sync_logic tests passed")

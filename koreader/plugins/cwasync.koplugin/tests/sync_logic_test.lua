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

-- #991: the reporter's device kept sending a digest for bytes it no longer had,
-- so every re-download over OPDS registered one more digest it would never
-- compute and the server answered "No book found for checksum" forever.
local function testResolveDocumentDigest()
    local function returns(value)
        return function() return value end
    end

    -- The regression itself: the sidecar is stale because the file at that path
    -- was replaced. The digest of the bytes we actually hold has to win.
    assertEqual(
        SyncLogic.resolveDocumentDigest(returns("fresh"), returns("stale")),
        "fresh", "the digest computed from the file wins over a cached one")

    -- Unchanged file: recomputing agrees with the cache, so nothing moves.
    assertEqual(
        SyncLogic.resolveDocumentDigest(returns("same"), returns("same")),
        "same", "an accurate cache is indistinguishable from a recompute")

    -- File missing/unreadable (detached SD card, deleted book): a stale digest
    -- still beats no digest, since progress stored under it can round-trip.
    assertEqual(
        SyncLogic.resolveDocumentDigest(returns(nil), returns("cached")),
        "cached", "the cache is used when the file cannot be hashed")

    -- Hashing that throws must not take the sync down with it.
    assertEqual(
        SyncLogic.resolveDocumentDigest(function() error("unreadable") end, returns("cached")),
        "cached", "a compute that errors falls back to the cache")
    assertEqual(
        SyncLogic.resolveDocumentDigest(returns("fresh"), function() error("no sidecar") end),
        "fresh", "a cache read that errors does not discard a good digest")

    -- Empty strings are absent values, not digests -- KOReader leaves "" in the
    -- sidecar for documents it has not hashed yet.
    assertEqual(
        SyncLogic.resolveDocumentDigest(returns(""), returns("cached")),
        "cached", "an empty computed digest is treated as absent")
    assertEqual(
        SyncLogic.resolveDocumentDigest(returns(""), returns("")),
        nil, "two empty digests resolve to nothing")

    -- Nothing available at all, and non-callable sources.
    assertEqual(SyncLogic.resolveDocumentDigest(returns(nil), returns(nil)), nil,
        "no digest anywhere resolves to nil")
    assertEqual(SyncLogic.resolveDocumentDigest(nil, nil), nil,
        "missing sources resolve to nil rather than erroring")

    -- A non-string return (a table from a bad DocSettings read) is not a digest.
    assertEqual(SyncLogic.resolveDocumentDigest(returns({}), returns("cached")),
        "cached", "a non-string computed value is treated as absent")
end

-- The device half of #920. `computeDeletions` is only sound when the list it is
-- handed was genuinely read off the device, and the thing that decided that used
-- to be `push_all_local` — a constant, true for the KOReader provider on every
-- call, including the ones where the read returned nothing because the reader
-- had gone away. These pin that an unreadable device reports `known = false`,
-- which is what stops its whole watermark from being named for deletion.
local function testResolveLocalSet()
    local function providerReturning(value, extra)
        local p = { readAll = function() return value end }
        for k, v in pairs(extra or {}) do p[k] = v end
        return p
    end

    -- A read that succeeded and found nothing. This IS authoritative: the user
    -- deleted their last highlight (#905) and the deletion must still sync.
    local list, known = SyncLogic.resolveLocalSet(
        providerReturning({}, { push_all_local = true }))
    assertEqual(known, true, "a successful read of an empty device is known")
    assertEqual(#list, 0, "and yields an empty list")
    assertEqual(#SyncLogic.computeDeletions({ "a" }, list), 1,
        "so deleting the last highlight still reaps (#905)")

    -- A read that could not happen. Same empty table on the wire, opposite
    -- meaning: the device is not saying anything about its highlights.
    list, known = SyncLogic.resolveLocalSet(
        providerReturning(nil, { push_all_local = true }))
    assertEqual(known, false, "a provider that could not read is not known")
    assertEqual(#list, 0, "and still yields a safe empty list to diff against")

    -- The regression this exists to stop: unknown must never become deletions.
    assertEqual(known and #SyncLogic.computeDeletions({ "a", "b" }, list) or 0, 0,
        "an unreadable device deletes nothing, however full its watermark (#920)")

    -- A provider that raises is the least trustworthy answer, not an empty one.
    list, known = SyncLogic.resolveLocalSet({
        push_all_local = true,
        readAll = function() error("reader torn down") end,
    })
    assertEqual(known, false, "a provider that throws is unreadable, not empty")
    assertEqual(#list, 0, "and still returns a list callers can diff")

    -- A non-list return (a provider bug, or a stub) is not a set either.
    assertEqual(select(2, SyncLogic.resolveLocalSet(
        providerReturning("nope", { push_all_local = true }))), false,
        "a non-table read result is not a set")

    -- The Kobo provider addresses rows by VolumeID. Without one it cannot read
    -- the device, so it must not be trusted to have read an empty one.
    assertEqual(select(2, SyncLogic.resolveLocalSet(providerReturning({}))), false,
        "a volume-addressed provider with no volume_id has read nothing")
    assertEqual(select(2, SyncLogic.resolveLocalSet(providerReturning({}), "vol-1")), true,
        "the same provider with a volume_id has genuinely read")

    -- Absent/malformed providers fail closed rather than erroring.
    assertEqual(select(2, SyncLogic.resolveLocalSet(nil)), false,
        "a missing provider is unreadable")
    assertEqual(select(2, SyncLogic.resolveLocalSet({})), false,
        "a provider with no readAll is unreadable")
end

-- The whole decision, provider read through to "what do we tell the server to
-- delete". This is the test that would have caught #920's device half: the
-- resolver and computeDeletions were each individually correct, and the bug was
-- in how the call site joined them. Anything that re-derives authority from a
-- capability flag has to break one of these.
local function testPlanLocalContribution()
    local function providerReturning(value, extra)
        local p = { readAll = function() return value end }
        for k, v in pairs(extra or {}) do p[k] = v end
        return p
    end
    local WATERMARK = { "a", "b" }

    -- Unreadable device, full watermark. The catastrophic case: every id in the
    -- watermark is a highlight the server still holds and will tombstone.
    local plan = SyncLogic.planLocalContribution(
        providerReturning(nil, { push_all_local = true }), nil, WATERMARK)
    assertEqual(plan.known, false, "an unreadable device is not known")
    assertEqual(#plan.deletions, 0,
        "and names NOTHING for deletion, however full its watermark (#920)")
    assertEqual(plan.may_save_watermark, false,
        "and must not overwrite the watermark with its placeholder")
    assertEqual(#plan.list, 0, "and still offers a list safe to diff against")

    -- Same, via a provider that raises rather than returning nil.
    plan = SyncLogic.planLocalContribution({
        push_all_local = true,
        readAll = function() error("reader torn down mid-sync") end,
    }, nil, WATERMARK)
    assertEqual(plan.known, false, "a provider that throws is not known")
    assertEqual(#plan.deletions, 0, "and names nothing for deletion")
    assertEqual(plan.may_save_watermark, false, "and cannot set the watermark")

    -- The #905 case that must survive: the user really did delete everything.
    plan = SyncLogic.planLocalContribution(
        providerReturning({}, { push_all_local = true }), nil, WATERMARK)
    assertEqual(plan.known, true, "a successful read of an empty device IS known")
    assertEqual(#plan.deletions, 2,
        "so clearing every highlight still syncs as a deletion (#905)")
    assertEqual(plan.may_save_watermark, true, "and may set the watermark")

    -- A partial delete: one of the two survives.
    plan = SyncLogic.planLocalContribution(
        providerReturning({ { annotation_id = "a" } }, { push_all_local = true }),
        nil, WATERMARK)
    assertEqual(#plan.deletions, 1, "only the removed highlight is deleted")
    assertEqual(plan.deletions[1], "b", "and it is the right one")

    -- Kobo: addressable only with a volume_id, so without one it has read
    -- nothing and must not be allowed to delete.
    plan = SyncLogic.planLocalContribution(providerReturning({}), nil, WATERMARK)
    assertEqual(plan.known, false, "no volume_id means the device was not read")
    assertEqual(#plan.deletions, 0, "so it names nothing for deletion")
    plan = SyncLogic.planLocalContribution(providerReturning({}), "vol-1", WATERMARK)
    assertEqual(plan.known, true, "with a volume_id the same read is genuine")
    assertEqual(#plan.deletions, 2, "and an emptied Kobo still syncs its deletions")

    -- A fresh install has no watermark, so it has deleted nothing either way.
    plan = SyncLogic.planLocalContribution(
        providerReturning({}, { push_all_local = true }), nil, nil)
    assertEqual(#plan.deletions, 0, "an empty watermark yields no deletions")
end

testIsRemoteProgressFromThisDevice()
testDidBookProgressChange()
testMergeAnnotation()
testDiffAnnotations()
testComputeDeletions()
testAnnotationIds()
testResolveDocumentDigest()
testResolveLocalSet()
testPlanLocalContribution()

print("sync_logic tests passed")

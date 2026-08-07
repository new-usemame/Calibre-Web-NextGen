local SyncLogic = {}

-- Mirror of PERCENTAGE_ONLY_LOCATOR in
-- cps/progress_syncing/protocols/kosync.py. The two must stay byte-identical;
-- tests/unit/test_cwasync_plugin_wire_contract.py pins the correspondence so
-- neither side can be edited alone.
SyncLogic.PERCENTAGE_ONLY_LOCATOR = "cwng:percentage"

-- A wire percentage is KOReader's decimal fraction, so the only meaningful
-- values are 0..1 inclusive. Returns nil for anything else.
--
-- This is the trust boundary for a server response, and the value is on its way
-- to GotoPercent: `tonumber` alone would let -0.25 through as GotoPercent(-25)
-- and 4 through as GotoPercent(400), seeking outside the document. The rows are
-- not all written by this release -- KOSyncProgress predates it and accepts
-- writes from other producers -- so the client cannot assume every stored
-- percentage is one the current server would have accepted.
--
-- NaN is excluded by `p ~= p`, which is the only way to test for it in Lua, and
-- would otherwise pass every comparison below without ever being in range.
function SyncLogic.validWirePercentage(value)
    local p = tonumber(value)
    if p == nil or p ~= p then
        return nil
    end
    if p < 0 or p > 1 then
        return nil
    end
    return p
end

function SyncLogic.isRemoteProgressFromThisDevice(body, device_model, device_id)
    return type(body) == "table"
        and body.device == device_model
        and body.device_id == device_id
end

-- The digest the server matches a sync against is a partial MD5 of the book's
-- *bytes* — the server computes the same 12 x 1 KiB sample in
-- cps/progress_syncing/checksums/koreader.py. KOReader caches that value in the
-- per-document sidecar as `partial_md5_checksum`, and the sidecar is keyed by
-- file PATH, so it outlives the file at that path being replaced.
--
-- Trusting the cache therefore breaks sync permanently as soon as the server's
-- copy of a book changes and the reader re-downloads over the old file: the
-- device keeps reporting the digest of bytes it no longer holds, while the
-- server only ever registers the digest of the bytes it actually served. The
-- two can never meet, and re-downloading makes it worse rather than better --
-- every download registers one more digest the device will never compute. That
-- is #991, where the reporter re-downloaded over OPDS and kept getting
-- "No book found for checksum".
--
-- So the bytes on disk are the authority. The cache is kept only as a fallback
-- for when recomputing is impossible (file missing, unreadable, detached SD
-- card): there a stale digest still beats no digest, because progress already
-- stored under it can still round-trip.
--
-- Recomputing is not free: it is one open plus 12 seek/read pairs of 1 KiB per
-- call, and bulk library pull does this once per book. That cost has not been
-- measured on e-reader storage. It is accepted rather than optimised because
-- there is no sound way to decide the cache is fresh -- KOReader rewrites the
-- sidecar on ordinary progress saves without recomputing the digest, so neither
-- the sidecar's mtime nor its presence tells you whether the file underneath it
-- changed. Note the path this replaced was not free either: it opened and
-- parsed each book's sidecar, and in bulk pull every book is followed by an
-- HTTP round trip that dominates either cost.
function SyncLogic.resolveDocumentDigest(computeFromFile, readCachedDigest)
    local function call(source)
        if type(source) ~= "function" then
            return nil
        end
        local ok, digest = pcall(source)
        if ok and type(digest) == "string" and digest ~= "" then
            return digest
        end
        return nil
    end

    return call(computeFromFile) or call(readCachedDigest)
end

function SyncLogic.didBookProgressChange(previous, new_values)
    return previous.percent_finished ~= new_values.percent_finished
        or previous.last_page ~= new_values.last_page
        or previous.last_xpointer ~= new_values.last_xpointer
        or previous.status ~= new_values.status
end

-- Decide what a pulled progress body actually tells us about where to go.
--
-- Two encodings arrive on this endpoint. The original one is a locator: an
-- engine-private crengine xpointer, or a page number for a paged document,
-- which the reader seeks to exactly. The second (#1366) is a percentage with no
-- locator at all, written by producers that know how far through the book the
-- user is but cannot express it as a position this engine would understand --
-- the web reader, whose positions are EPUB CFIs, and a Kobo. The server marks
-- those `position_kind = "percentage"` and sends `progress` as null.
--
-- Returning the decision as data keeps it testable, and keeps the two pull
-- paths (single document, bulk library) from drifting: both ask this.
--
-- `kind` is one of:
--   "locator"    -- seek to `progress` exactly (existing behaviour)
--   "percentage" -- seek to `percentage` of the book; approximate by nature
--   "none"       -- nothing usable; the caller reports "no progress"
--
-- A body with neither a locator nor a percentage is "none" rather than an
-- error. So is a percentage-only body from a server that did not label it,
-- which cannot happen against a server that understands `position_kinds` but
-- costs nothing to be strict about: guessing "percentage" from a missing
-- `progress` would mean any truncated response silently moved the reader.
--
-- `percentage` is normalised to 0-100 here. The wire format is KOReader's
-- decimal fraction (0.4567 = 45.67%), while GotoPercent takes whole percent,
-- and getting that conversion wrong lands the reader at the front of the book
-- rather than half way -- a mistake with no visible symptom other than being
-- in the wrong place.
function SyncLogic.resolveRemotePosition(body)
    if type(body) ~= "table" then
        return { kind = "none" }
    end

    local percentage = SyncLogic.validWirePercentage(body.percentage)

    -- The sentinel is never a locator, whatever else the body says.
    --
    -- Checking the value and not just the `position_kind` label matters because
    -- the two arrive independently. `position_kind` is a field the server adds;
    -- `progress` is the column's stored contents. If the label is absent --
    -- stripped by a proxy, dropped by a truncated response, omitted by a future
    -- caller of get_progress_record(), which defaults to including these rows --
    -- then a sentinel that is only recognised via the label is a non-empty
    -- string, which is to say a locator. It would reach GotoXPointer and be
    -- written to last_xpointer, which is exactly the unrecoverable position
    -- this encoding exists to avoid. Recognising the value itself makes that
    -- shape unreachable rather than merely unlikely.
    local is_sentinel = body.progress == SyncLogic.PERCENTAGE_ONLY_LOCATOR

    -- A locator must be a non-empty string (an xpointer) or a number (a page).
    -- The empty string is rejected explicitly because it is the one value that
    -- looks present to every nil-check and is meaningless to the engine: it
    -- would reach GotoXPointer and be stored as the document's last_xpointer.
    local has_locator = not is_sentinel
        and ((type(body.progress) == "string" and body.progress ~= "")
            or type(body.progress) == "number")

    if has_locator and body.position_kind ~= "percentage" then
        return {
            kind = "locator",
            progress = body.progress,
            percentage = percentage,
        }
    end

    -- An unlabelled sentinel still carries a usable percentage; honour it
    -- rather than discarding a real position, but only ever as a percentage.
    if (body.position_kind == "percentage" or is_sentinel) and percentage then
        return {
            kind = "percentage",
            percentage = percentage,
            -- 0-100, the unit GotoPercent expects.
            percent_whole = percentage * 100,
        }
    end

    return { kind = "none" }
end

-- Phase 2: annotation sync. Annotations are portable tables (see the server's
-- cps/services/annotation_portable.py) keyed by `annotation_id`. Conflict rule:
-- last-`last_synced`-wins per field; position is immutable; a delete (hidden)
-- wins when it is the latest action. ISO-8601 `last_synced` strings sort
-- lexicographically in timestamp order, so plain string comparison works.

function SyncLogic.mergeAnnotation(a, b)
    local newer, older
    if (b.last_synced or "") >= (a.last_synced or "") then
        newer, older = b, a
    else
        newer, older = a, b
    end
    local out = {}
    for k, v in pairs(older) do out[k] = v end
    for k, v in pairs(newer) do
        if v ~= nil then out[k] = v end
    end
    -- Position is immutable: a delete/partial payload may omit the anchor;
    -- keep whatever the records established at creation time.
    if out.start_kobospan == nil then out.start_kobospan = older.start_kobospan end
    if out.end_kobospan == nil then out.end_kobospan = older.end_kobospan end
    if out.content_id == nil then out.content_id = older.content_id end
    return out
end

-- Given the device's local annotations and the server's pulled annotations,
-- return { apply_to_device = {...}, send_to_server = {...} }:
--   * remote-only            -> apply_to_device
--   * local-only             -> send_to_server
--   * in both, remote newer  -> apply_to_device (merged)
--   * in both, local newer   -> send_to_server (merged)
--   * in both, equal         -> converged, emitted to neither (no feedback loop)
-- The annotation_ids in a portable list, sorted so the stored watermark is
-- stable across syncs.
function SyncLogic.annotationIds(list)
    local ids = {}
    for _, a in ipairs(list or {}) do
        if a.annotation_id then table.insert(ids, a.annotation_id) end
    end
    table.sort(ids)
    return ids
end

-- Which annotations the user deleted on this device, given `watermark` (the ids
-- we last pushed) and `localList` (what is live now).
--
-- KOReader deletes a highlight outright, leaving no tombstone, so a deletion
-- exists only as this difference. It has to be computed HERE rather than
-- inferred by the server from an omission: a device whose user deleted every
-- highlight and a device that never had them push the identical empty set, and
-- the server has no way to tell those apart — it guessed, and destroyed a
-- second device's highlights permanently (#920). Only the device knows what it
-- used to have.
--
-- Callers must only trust this when the local set was genuinely read; an empty
-- `localList` that stands for "could not read" would delete everything.
function SyncLogic.computeDeletions(watermark, localList)
    local live = {}
    for _, a in ipairs(localList or {}) do
        if a.annotation_id then live[a.annotation_id] = true end
    end
    local deleted = {}
    for _, id in ipairs(watermark or {}) do
        if not live[id] then table.insert(deleted, id) end
    end
    return deleted
end

-- Read the device's live annotation set, and say whether it is actually known.
--
-- This is the guard `computeDeletions` above demands, and it lives here rather
-- than at the call site because the two must agree: the caller passes the list
-- to both, and only this function can tell "the user has no highlights" from
-- "this device could not be asked". Deriving that from a provider's static
-- capability flag instead — `push_all_local`, which is a constant — makes every
-- failed read indistinguishable from a full delete, which is #920 from the
-- device's side of the wire.
--
-- Returns `list, known`. `known` is false when the set could not be read, and
-- `list` is then an empty table so callers can still diff safely; they must not
-- derive deletions from it. A provider that throws is treated as unreadable, not
-- as empty — a raise is the least trustworthy answer of all.
function SyncLogic.resolveLocalSet(provider, volume_id)
    if type(provider) ~= "table" or type(provider.readAll) ~= "function" then
        return {}, false
    end
    -- The Kobo provider addresses rows by VolumeID, so without one it cannot
    -- read the device at all. `push_all_local` providers enumerate the open
    -- document instead and need no volume.
    if not (provider.push_all_local or volume_id) then
        return {}, false
    end
    local ok, list = pcall(provider.readAll, volume_id)
    if not ok or type(list) ~= "table" then
        return {}, false
    end
    return list, true
end

-- Everything the push needs to know about this device's own set, decided here
-- rather than at the call site.
--
-- The call site is a callback inside a 1600-line file that no test can reach,
-- so any authority logic written there is unexecutable by construction — and
-- the bug this replaces lived there for exactly that reason. Returning the
-- decision as data means the whole path, provider read through to "which ids do
-- we tell the server to delete", runs in the test suite.
--
--   list      -- the device's live annotations, {} when they could not be read
--   known     -- whether that list is a genuine read (see resolveLocalSet)
--   deletions -- ids to name to the server; ALWAYS empty on an unknown read
--   may_save_watermark -- whether `list` is fit to become the new watermark;
--                         saving a placeholder would make the next sync believe
--                         the device had nothing to begin with
function SyncLogic.planLocalContribution(provider, volume_id, watermark)
    local list, known = SyncLogic.resolveLocalSet(provider, volume_id)
    return {
        list = list,
        known = known,
        deletions = known and SyncLogic.computeDeletions(watermark, list) or {},
        may_save_watermark = known,
    }
end

function SyncLogic.diffAnnotations(localList, remoteList)
    local function byId(list)
        local m = {}
        for _, a in ipairs(list or {}) do
            if a.annotation_id then m[a.annotation_id] = a end
        end
        return m
    end
    local L = byId(localList)
    local R = byId(remoteList)
    local apply_to_device, send_to_server = {}, {}
    for id, r in pairs(R) do
        local l = L[id]
        if not l then
            table.insert(apply_to_device, r)
        else
            local rt = r.last_synced or ""
            local lt = l.last_synced or ""
            if rt > lt then
                table.insert(apply_to_device, SyncLogic.mergeAnnotation(l, r))
            elseif lt > rt then
                table.insert(send_to_server, SyncLogic.mergeAnnotation(r, l))
            end
        end
    end
    for id, l in pairs(L) do
        if not R[id] then
            table.insert(send_to_server, l)
        end
    end
    return { apply_to_device = apply_to_device, send_to_server = send_to_server }
end

return SyncLogic

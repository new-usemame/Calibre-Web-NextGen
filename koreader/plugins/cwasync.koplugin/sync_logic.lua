local SyncLogic = {}

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

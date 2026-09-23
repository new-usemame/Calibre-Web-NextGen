--[[
What leaving a book owes the server: its position, its read status and its
highlights, captured while the book is still open and kept on the device until
the server has them. Closing a book or putting the device to sleep never waits
for Wi-Fi; the next connection delivers whatever is waiting.

The queue is keyed by document digest, so the latest capture of a book
replaces an older one. `seq` tells a delivery that finished late whether it
delivered the entry that is still queued or one that has since been replaced.
]]

local Library = require("cwng_library")

local Pending = {}

-- Record a capture. Returns the sequence number to settle it with.
function Pending.record(queue, entry)
    local previous = queue[entry.document]
    entry.seq = ((previous and previous.seq) or 0) + 1
    -- A status or highlight change that has not reached the server yet must
    -- survive a later capture that carries no change of its own, from the
    -- same account.
    if previous and previous.owner == entry.owner then
        if entry.percentage == nil then
            entry.progress = previous.progress
            entry.percentage = previous.percentage
            entry.captured_at = previous.captured_at
        end
        if entry.status == nil then entry.status = previous.status end
        if entry.annotations == nil then entry.annotations = previous.annotations end
    end
    queue[entry.document] = entry
    return entry.seq
end

-- Remove what was captured under another account (another user or server):
-- its book ids and positions belong there. A capture from before owners were
-- recorded is taken to be the current account's. Returns how many were dropped.
function Pending.dropOtherAccounts(queue, owner)
    local dropped = 0
    for document, entry in pairs(queue) do
        if entry.owner ~= nil and entry.owner ~= owner then
            queue[document] = nil
            dropped = dropped + 1
        end
    end
    return dropped
end

-- Drop the entry once the server has everything it carried, unless a newer
-- capture of the same book replaced it in the meantime.
function Pending.settle(queue, document, seq)
    local entry = queue[document]
    if entry and entry.seq == seq then
        queue[document] = nil
        return true
    end
    return false
end

-- Trim a capture to what the reader actually did. A book opened and closed
-- without a page turned says nothing about the position (sending "page 1"
-- would pull every other device back to the start), and "reading" is the tag
-- KOReader puts on any book it opens. Returns nil when nothing is left.
-- Whether the reader moved in the book since it opened (or since a position
-- from another device was applied): the position now against the one then.
-- Page events are no guide, KOReader sends them while a book loads and when
-- a pulled position is applied. With no starting point recorded, assume they
-- did: losing reading is worse than resending it.
function Pending.movedSince(baseline, progress)
    if baseline == nil then return true end
    return tostring(progress) ~= baseline
end

function Pending.trimUnmoved(entry, moved)
    if moved then return entry end
    entry.progress, entry.percentage = nil, nil
    if entry.status == "reading" then entry.status = nil end
    if entry.status == nil and entry.annotations == nil then return nil end
    return entry
end

-- Whether a queued position should still be sent, given what the server holds
-- now. A position another device recorded after this one was captured is
-- newer reading, and sending the old one late would overwrite it.
function Pending.shouldSendProgress(entry, remote, device_id)
    if type(remote) ~= "table" or remote.percentage == nil then return true end
    if remote.device_id ~= nil and remote.device_id == device_id then return true end
    local remote_time = tonumber(remote.timestamp)
    local captured = tonumber(entry.captured_at)
    if remote_time and captured and remote_time > captured then return false end
    return true
end

-- The CWNG read status to send for a KOReader status, or nil when there is
-- nothing to say. "Unread" is only sent to undo a status this device sent
-- before; a book that is merely open says nothing new.
function Pending.statusToSend(koreader_status, already_sent)
    local status = Library.serverStatus(koreader_status)
    if status == nil or status == already_sent then return nil end
    if status == "unread" and already_sent == nil then return nil end
    return status
end

return Pending

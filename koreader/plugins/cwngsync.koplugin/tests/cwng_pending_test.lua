package.path = table.concat({
    "./?.lua",
    "../?.lua",
    package.path,
}, ";")

local Pending = require("cwng_pending")

local function assertEqual(actual, expected, message)
    if actual ~= expected then
        error(string.format("%s\nexpected: %s\nactual: %s",
            message, tostring(expected), tostring(actual)), 2)
    end
end

local function testLatestCaptureWinsAndALateDeliveryCannotDropIt()
    local queue = {}
    local first = Pending.record(queue, { document = "d1", percentage = 0.2, captured_at = 100 })
    local second = Pending.record(queue, { document = "d1", percentage = 0.3, captured_at = 200 })
    assertEqual(queue.d1.percentage, 0.3, "the later position is the one queued")
    assertEqual(Pending.settle(queue, "d1", first), false,
        "delivering the older capture does not clear the newer one")
    assertEqual(queue.d1 ~= nil, true, "still queued")
    assertEqual(Pending.settle(queue, "d1", second), true, "delivering the queued capture clears it")
    assertEqual(queue.d1, nil, "and it is gone")
end

local function testUnsentStatusAndHighlightsSurviveALaterPositionOnlyCapture()
    local queue = {}
    Pending.record(queue, { document = "d1", percentage = 1.0, status = "finished",
        annotations = { list = { { annotation_id = "a" } }, deletions = {} } })
    Pending.record(queue, { document = "d1", percentage = 1.0 })
    assertEqual(queue.d1.status, "finished", "status still owed")
    assertEqual(#queue.d1.annotations.list, 1, "highlights still owed")
    Pending.record(queue, { document = "d1", percentage = 1.0,
        annotations = { list = {}, deletions = { "a" } } })
    assertEqual(queue.d1.annotations.deletions[1], "a", "a newer highlight snapshot replaces the old")
end

local function testAQueuedPositionNeverOverwritesNewerReadingElsewhere()
    local entry = { document = "d1", percentage = 0.4, captured_at = 1000 }
    assertEqual(Pending.shouldSendProgress(entry, nil, "me"), true, "nothing on the server")
    assertEqual(Pending.shouldSendProgress(entry, {}, "me"), true, "no position on the server")
    assertEqual(Pending.shouldSendProgress(entry,
        { percentage = 0.6, timestamp = 1500, device_id = "kobo" }, "me"), false,
        "another device read on after this was captured")
    assertEqual(Pending.shouldSendProgress(entry,
        { percentage = 0.1, timestamp = 900, device_id = "kobo" }, "me"), true,
        "the server's position is older than this one")
    assertEqual(Pending.shouldSendProgress(entry,
        { percentage = 0.9, timestamp = 1500, device_id = "me" }, "me"), true,
        "this device's own later push does not block it")
end

local function testStatusIsSentOnlyWhenItSaysSomethingNew()
    assertEqual(Pending.statusToSend("complete", nil), "finished", "finishing a book")
    assertEqual(Pending.statusToSend("complete", "finished"), nil, "already told the server")
    assertEqual(Pending.statusToSend("reading", nil), "reading", "started")
    assertEqual(Pending.statusToSend("abandoned", nil), nil, "on hold is KOReader's own")
    assertEqual(Pending.statusToSend(nil, nil), nil, "a book never marked says nothing")
    assertEqual(Pending.statusToSend("new", "finished"), "unread", "resetting a finished book")
end

local function testOpeningABookWithoutReadingSendsNoPosition()
    assertEqual(Pending.trimUnmoved({ document = "d1", percentage = 0, progress = "p1",
        status = "reading" }, false), nil, "opened and closed: nothing to say")
    local finished = Pending.trimUnmoved({ document = "d1", percentage = 0.1, progress = "p",
        status = "finished" }, false)
    assertEqual(finished.percentage, nil, "no position from an unmoved book")
    assertEqual(finished.status, "finished", "but marking it finished still counts")
    local highlighted = Pending.trimUnmoved({ document = "d1", percentage = 0.1,
        annotations = { list = {}, deletions = { "x" } } }, false)
    assertEqual(highlighted.annotations.deletions[1], "x", "a deleted highlight still counts")
    local read = Pending.trimUnmoved({ document = "d1", percentage = 0.3, status = "reading" }, true)
    assertEqual(read.percentage, 0.3, "a turned page sends the position")
    assertEqual(read.status, "reading", "and the status")

    -- Read offline, then reopened and closed without moving: the earlier
    -- position is still owed.
    local queue = {}
    Pending.record(queue, { document = "d1", percentage = 0.5, progress = "p50", captured_at = 10 })
    Pending.record(queue, { document = "d1", status = "finished", captured_at = 20 })
    assertEqual(queue.d1.percentage, 0.5, "earlier position kept")
    assertEqual(queue.d1.captured_at, 10, "with the time it was captured")
    assertEqual(queue.d1.status, "finished", "and the new status added")
end

testOpeningABookWithoutReadingSendsNoPosition()
testLatestCaptureWinsAndALateDeliveryCannotDropIt()
testUnsentStatusAndHighlightsSurviveALaterPositionOnlyCapture()
testAQueuedPositionNeverOverwritesNewerReadingElsewhere()
testStatusIsSentOnlyWhenItSaysSomethingNew()
print("cwng_pending_test.lua: all tests passed")

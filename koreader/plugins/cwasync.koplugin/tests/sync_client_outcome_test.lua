package.path = table.concat({
    "./?.lua",
    "../?.lua",
    package.path,
}, ";")

-- What a failed sync call tells the user, and where.
--
-- #920: a KOReader delete showed "Server push failed" while the reporter's
-- server log recorded no push at all -- the request never left the device. The
-- error that would have named why was dropped twice over: it went to
-- `logger.dbg`, which KOReader suppresses unless debug logging is on, and the
-- callback was handed `res.body`, which is nil on a raise because `pcall`
-- returns the error rather than a response. So the one failure mode with no
-- server-side trace also had no device-side trace, and three diagnoses were
-- guessed instead of read.
--
-- These pin the contract that closes that: a failed call always reports a
-- reason, and always logs at a level the user actually gets.

local warnings = {}
local debugs = {}

package.preload["ui/uimanager"] = function()
    return { looper = nil, setInputTimeout = function() end }
end
package.preload["logger"] = function()
    return {
        warn = function(...) table.insert(warnings, { ... }) end,
        dbg = function(...) table.insert(debugs, { ... }) end,
        info = function() end,
        err = function() end,
    }
end
package.preload["socketutil"] = function()
    return { set_timeout = function() end, reset_timeout = function() end }
end

local CWASyncClient = require("CWASyncClient")

local function assertEqual(actual, expected, message)
    if actual ~= expected then
        error(string.format("%s\nexpected: %s\nactual: %s", message, tostring(expected), tostring(actual)), 2)
    end
end

local function assertTruthy(value, message)
    if not value then error(message, 2) end
end

local function testDescribeFailureNamesEveryShape()
    local describe = CWASyncClient.describeFailure
    -- lua-Spore raises a table; the transport raises a string. Both used to be
    -- flattened to nil.
    assertEqual(describe({ message = "connection refused" }), "connection refused",
        "table error: message is preferred")
    assertEqual(describe({ error = "bad_request" }), "bad_request",
        "table error: falls back to error")
    assertEqual(describe({ reason = "timeout" }), "timeout",
        "table error: falls back to reason")
    assertEqual(describe({ status = 503 }), "HTTP 503",
        "table error with only a status still names it")
    assertEqual(describe("api.json: missing required parameter"), "api.json: missing required parameter",
        "string error is passed through verbatim")
    -- The catch-all has to stay a sentence, never nil: an empty reason would
    -- put us straight back to a bare "Server push failed".
    assertEqual(describe(nil), "no response from server", "nil error still names something")
    assertEqual(describe(""), "no response from server", "empty error still names something")
    assertEqual(describe({}), "no response from server", "featureless table still names something")
end

local function testRaisedCallReportsAReasonAndWarns()
    warnings, debugs = {}, {}
    local seen
    CWASyncClient._reportOutcome(function(ok, body, reason)
        seen = { ok = ok, body = body, reason = reason }
    end, false, "attempt to index a nil value", "CWASyncClient:push_annotations")

    assertEqual(seen.ok, false, "a raise is not a success")
    -- The regression: this used to be nil, so the caller could only say
    -- "Server push failed" with nothing after the colon.
    assertEqual(seen.reason, "attempt to index a nil value", "the raise reason reaches the caller")
    assertEqual(#warnings, 1, "a failed call logs exactly once, at warn")
    assertEqual(#debugs, 0, "nothing is written at dbg, which users do not have on")
    assertTruthy(tostring(warnings[1][1]):find("push_annotations", 1, true),
        "the warning names which call failed")
end

local function testNon200ReportsItsStatus()
    warnings, debugs = {}, {}
    local seen
    CWASyncClient._reportOutcome(function(ok, body, reason)
        seen = { ok = ok, body = body, reason = reason }
    end, true, { status = 400, body = { error = "invalid_deleted" } }, "CWASyncClient:push_annotations")

    assertEqual(seen.ok, false, "400 is not a success")
    assertEqual(seen.reason, "HTTP 400", "a rejection reports its status")
    -- A call that reached the server keeps its body: the server names the field
    -- it objected to (#1101) and the caller may want it.
    assertEqual(seen.body.error, "invalid_deleted", "the server's own error survives")
    assertEqual(#warnings, 0, "a completed call is not a client-side fault")
end

local function testSuccessCarriesNoReason()
    warnings, debugs = {}, {}
    local seen
    CWASyncClient._reportOutcome(function(ok, body, reason)
        seen = { ok = ok, body = body, reason = reason }
    end, true, { status = 200, body = { created = 1 } }, "CWASyncClient:push_annotations")

    assertEqual(seen.ok, true, "200 is a success")
    assertEqual(seen.reason, nil, "a success has no reason, so callers can branch on it")
    assertEqual(seen.body.created, 1, "the response body is passed through")
    assertEqual(#warnings, 0, "a success logs nothing")
end

-- The server keeps every rejection funnelled through one `_reject` so the log
-- cannot regress to silence one branch at a time (#1101). The device half needs
-- the same guard: a new sync call that hand-rolls `logger.dbg` would be
-- invisible again, and only on that one path, which is the hardest kind of gap
-- to notice.
local function testNoSyncFailureIsWrittenAtDbg()
    local source = io.open("../CWASyncClient.lua", "r")
    assertTruthy(source, "CWASyncClient.lua is readable from the tests directory")
    local text = source:read("*a")
    source:close()

    -- The call form, not the bare name: the comment above `describeFailure`
    -- explains what dbg used to cost us and must stay quotable.
    assertEqual(text:find("logger.dbg(", 1, true), nil,
        "no failure path may log at dbg -- KOReader suppresses it unless the user "
        .. "enabled debug logging, which is how #920 lost its only device-side trace")
    assertTruthy(text:find("logger.warn(", 1, true), "failures are logged at warn")
end

testDescribeFailureNamesEveryShape()
testNoSyncFailureIsWrittenAtDbg()
testRaisedCallReportsAReasonAndWarns()
testNon200ReportsItsStatus()
testSuccessCarriesNoReason()

print("sync_client outcome-reporting tests passed")

package.path = table.concat({
    "./?.lua",
    "../?.lua",
    package.path,
}, ";")

-- Share my place, PR 2 (spec state/kindle-kids/specs/2026-10-11-share-my-place.md,
-- section 10): the server identifies a KOSync request only by the
-- X-CWNG-Device-ID header. Without it, a device's progress is always the
-- account's shared place and the Devices page cannot give it its own. So the
-- plugin's progress calls must send the header (and X-CWNG-Device-Name) on
-- both the pull and the push, while keeping what they already send.
--
-- The Spore client is replaced by a recorder that runs the middlewares the
-- client enabled, as lua-Spore does, and keeps the headers each call left.

package.preload["ui/uimanager"] = function()
    return { looper = nil, setInputTimeout = function() end }
end
package.preload["logger"] = function()
    return { warn = function() end, dbg = function() end, info = function() end,
             err = function() end }
end
package.preload["gettext"] = function()
    return function(text) return text end
end
package.preload["socketutil"] = function()
    return { set_timeout = function() end, reset_timeout = function() end }
end

local fake = { enabled = {}, calls = {} }

function fake:reset_middlewares() self.enabled = {} end

function fake:enable(name, args)
    table.insert(self.enabled, { name = name, args = args or {} })
end

local function dispatch(self, method, params)
    local req = { headers = {}, method = method, env = { spore = {} },
                  finalize = function() end }
    for _, middleware in ipairs(self.enabled) do
        local module = package.loaded["Spore.Middleware." .. middleware.name]
        if module and module.call then module.call(middleware.args, req) end
    end
    table.insert(self.calls, { method = method, params = params, headers = req.headers })
    return { status = 200, body = {} }
end

function fake:get_progress(params) return dispatch(self, "get_progress", params) end
function fake:update_progress(params) return dispatch(self, "update_progress", params) end

package.preload["Spore"] = function()
    return { new_from_spec = function() return fake end }
end

local CWNGSyncClient = require("CWNGSyncClient")

local function assertEqual(actual, expected, message)
    if actual ~= expected then
        error(string.format("%s\nexpected: %s\nactual: %s", message,
            tostring(expected), tostring(actual)), 2)
    end
end

local client = CWNGSyncClient:new({ service_spec = {}, service_url = "https://cwng.invalid" })

local function lastCall()
    local call = fake.calls[#fake.calls]
    assert(call ~= nil, "the client made no request")
    return call
end

local function testPullSendsTheDeviceHeaders()
    fake.calls = {}
    local outcome
    -- get_progress(username, password, document, device, device_id, callback)
    client:get_progress("reader", "secret", "0123abcd", "Kindle A", "raw-device-1",
        function(ok) outcome = ok end)
    local call = lastCall()
    assertEqual(call.method, "get_progress", "the pull goes out as get_progress")
    assertEqual(call.headers["X-CWNG-Device-ID"], "raw-device-1",
        "the pull names this device by its raw id")
    assertEqual(call.headers["X-CWNG-Device-Name"], "Kindle A",
        "the pull names this device's name")
    assertEqual(call.params.document, "0123abcd", "the document is still sent")
    assertEqual(call.params.position_kinds, "locator,percentage",
        "the pull still advertises percentage-only support (#1366)")
    assert(call.headers["Authorization"] ~= nil, "the pull is still authenticated")
    assertEqual(outcome, true, "the pull still reports its outcome")
end

local function testPushSendsTheDeviceHeaders()
    fake.calls = {}
    local outcome
    client:update_progress("reader", "secret", "0123abcd", "/body/p[1]/text().0", 0.42,
        "Kindle A", "raw-device-1", function(ok) outcome = ok end)
    local call = lastCall()
    assertEqual(call.method, "update_progress", "the push goes out as update_progress")
    assertEqual(call.headers["X-CWNG-Device-ID"], "raw-device-1",
        "the push names this device by its raw id in the header")
    assertEqual(call.headers["X-CWNG-Device-Name"], "Kindle A",
        "the push names this device's name in the header")
    assertEqual(call.params.device_id, "raw-device-1",
        "the body still carries device_id, as KOSync servers expect")
    assertEqual(call.params.device, "Kindle A", "the body still carries device")
    assert(call.headers["Authorization"] ~= nil, "the push is still authenticated")
    assertEqual(outcome, true, "the push still reports its outcome")
end

testPullSendsTheDeviceHeaders()
testPushSendsTheDeviceHeaders()

print("share-place device header tests passed")

-- Which refusals let a queued change go, through AutoSync:deliverPending.
--
-- A change made offline (a read status, a position) waits in the queue until
-- the server takes it. A refusal that sending again cannot change drops it;
-- one that asks to try later must keep it, or the change is lost.

package.path = table.concat({ "../?.lua", "./?.lua", package.path }, ";")
package.loaded["device"] = { model = "Kindle" }
package.loaded["docsettings"] = {}
package.loaded["json"] = { decode = function() return {} end }
package.loaded["ui/network/manager"] = { isConnected = function() return true end }
package.loaded["ui/uimanager"] = { nextTick = function(_, f) f() end, scheduleIn = function() end }
package.loaded["logger"] = { info = function() end, warn = function() end, dbg = function() end }

local AutoSync = require("cwng_auto_sync")

local function assertEqual(actual, expected, message)
    if actual ~= expected then
        error(string.format("%s\nexpected: %s\nactual: %s",
            message, tostring(expected), tostring(actual)), 2)
    end
end

-- Deliver a queued read status the server answers with `answer`.
local function deliverStatus(answer)
    local plugin = setmetatable({
        settings = { username = "reader", password = "app" },
        device_id = "device",
        bookIdFor = function() return 7 end,
        saveBookSetting = function() end,
    }, { __index = AutoSync })
    local client = {
        update_read_status = function(_, _, _, _, _, _, _, _, callback)
            callback(answer == nil, nil, answer)
        end,
    }
    local delivered
    plugin:deliverPending(client, { file = "/books/b.epub", book_id = 7, status = "finished" },
        function(ok) delivered = ok end)
    return delivered
end

local function testAChangeTheServerWillNeverTakeIsLetGo()
    assertEqual(deliverStatus(nil), true, "taken")
    assertEqual(deliverStatus("HTTP 404"), true, "a book the server no longer has: let go")
    assertEqual(deliverStatus("HTTP 422"), true, "a change the server cannot take: let go")
end

local function testAChangeTheServerAsksToSendLaterIsKept()
    for _, answer in ipairs({ "HTTP 408", "HTTP 425", "HTTP 429", "HTTP 401", "HTTP 403", "HTTP 503", "timeout" }) do
        assertEqual(deliverStatus(answer), false, answer .. " keeps the change queued")
    end
end

local function testAQueuedHighlightSaysWhichDeviceMadeIt()
    local plugin = setmetatable({
        settings = { username = "reader", password = "app" },
        device_id = "this-kindle",
        saveBookSetting = function() end,
    }, { __index = AutoSync })
    local pushed
    local client = {
        push_annotations = function(_, _, _, _, list, deleted, device, device_id, callback)
            pushed = { count = #list, device = device, device_id = device_id }
            callback(true, {}, nil)
        end,
    }
    local delivered
    plugin:deliverPending(client, { file = "/books/b.epub", document = "digest",
        annotations = { list = { { text = "mine" } }, deletions = {} } }, function(ok) delivered = ok end)
    assertEqual(delivered, true, "delivered")
    assertEqual(pushed and pushed.count, 1, "the highlight is pushed")
    assertEqual(pushed.device_id, "this-kindle", "with this device's id")
    assertEqual(pushed.device, "Kindle", "and its model")
end

testAChangeTheServerWillNeverTakeIsLetGo()
testAChangeTheServerAsksToSendLaterIsKept()
testAQueuedHighlightSaysWhichDeviceMadeIt()

print("cwng_auto_sync tests passed")

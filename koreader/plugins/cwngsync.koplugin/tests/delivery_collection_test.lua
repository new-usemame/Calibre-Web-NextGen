-- Behavioural coverage for CWNGSync:collectDeliveries.
--
-- The production function is loaded verbatim into a small asynchronous
-- sandbox.  The sandbox deliberately holds network, inventory, claim and
-- next-tick callbacks so two real entry-point calls can overlap.  This catches
-- the race itself: a source-string pin can prove that a guard was named, but
-- cannot prove that the second trigger was rejected or that the guard was
-- released on terminal paths.

local function assertEqual(actual, expected, message)
    if actual ~= expected then
        error(string.format("%s\nexpected: %s\nactual: %s",
            message, tostring(expected), tostring(actual)), 2)
    end
end

local function loadProductionFunction(env)
    package.path = table.concat({ "../?.lua", "./?.lua", package.path }, ";")
    local main_path = assert(package.searchpath("main", package.path),
        "cannot locate main.lua via package.path")
    local file = assert(io.open(main_path, "r"), "cannot read " .. main_path)
    local source = file:read("*a")
    file:close()

    local header = "function CWNGSync:collectDeliveries("
    local start = assert(source:find(header, 1, true),
        "collectDeliveries not found in main.lua")
    local following = assert(source:find("\nfunction CWNGSync:", start + 1, true),
        "function following collectDeliveries not found")
    local body = source:sub(start, following - 1)
    local chunk = "local CWNGSync = {}\n" .. body .. "\nreturn CWNGSync\n"

    local loaded, load_error = load(chunk, "collectDeliveries", "t", env)
    assert(loaded, "collectDeliveries failed to parse: " .. tostring(load_error))
    return loaded()
end

local function newHarness(options)
    options = options or {}
    local calls = {
        inventory = 0,
        claims = 0,
        installs = {},
        completions = {},
    }
    local pending = {
        network = {},
        inventory = {},
        claims = {},
        ticks = {},
    }
    local wait_for_network = options.wait_for_network or false
    local client = {}
    local env = {
        type = type,
        tostring = tostring,
        pcall = pcall,
        math = math,
        table = table,
        os = { rename = function() return true end },
        _ = function(value) return value end,
        T = function(value) return value end,
        promptLogin = function() end,
        ensureServerConfigured = function(server)
            return server ~= nil and server ~= ""
        end,
        InfoMessage = { new = function(_, value) return value end },
        logger = {
            dbg = function() end,
            info = function() end,
            warn = function() end,
        },
        lfs = { attributes = function() return {} end },
        util = {
            getSafeFilename = function(name) return name end,
            removeFile = function() end,
        },
        Device = { model = "test-device" },
        NetworkMgr = {
            willRerunWhenOnline = function(_, callback)
                if wait_for_network then
                    table.insert(pending.network, callback)
                    return true
                end
                return false
            end,
        },
        UIManager = {
            show = function() end,
            nextTick = function(_, callback)
                table.insert(pending.ticks, callback)
            end,
        },
        Delivery = {
            install = function(delivery)
                calls.installs[delivery.id] = (calls.installs[delivery.id] or 0) + 1
                if options.install_fails then
                    return nil, "install failed"
                end
                return {
                    path = "/books/queued.epub",
                    lpath = "queued.epub",
                    checksum = "checksum",
                    size = 42,
                    mtime = 123,
                }
            end,
        },
    }

    function client:claim_delivery(...)
        calls.claims = calls.claims + 1
        local arguments = { ... }
        table.insert(pending.claims, arguments[#arguments])
    end

    function client:complete_delivery(...)
        local arguments = { ... }
        local delivery_id = arguments[5]
        local callback = arguments[#arguments]
        calls.completions[delivery_id] = (calls.completions[delivery_id] or 0) + 1
        callback(not options.completion_fails, {},
            options.completion_fails and "completion failed" or nil)
    end

    env.require = function(name)
        assertEqual(name, "CWNGSyncClient", "unexpected module request")
        return { new = function() return client end }
    end

    local CWNGSync = loadProductionFunction(env)
    local server = "https://example.test"
    local username = "reader"
    local password = "password"
    if options.server == false then server = nil end
    if options.credentials == false then
        username = nil
        password = nil
    end
    local self_stub = {
        settings = {
            server = server,
            username = username,
            password = password,
        },
        path = "/plugin",
        device_id = "device-id",
    }
    self_stub.collectDeliveries = CWNGSync.collectDeliveries
    function self_stub:reportInventory(_, _, callback)
        calls.inventory = calls.inventory + 1
        table.insert(pending.inventory, callback)
    end
    function self_stub:getDeliveryRootPath()
        if options.root_missing then return nil end
        return "/books"
    end
    function self_stub:getStorageSpace()
        return 1024 * 1024, 2 * 1024 * 1024
    end
    function self_stub:getDeliveryReceipt() return nil end
    function self_stub:getDocumentDigest() return "checksum" end
    function self_stub:persistDeliveryReceipt() return true end
    function self_stub:clearDeliveryReceipt() end
    function self_stub:refreshLibraryViews() end

    return {
        calls = calls,
        pending = pending,
        self = self_stub,
        setNetworkReady = function() wait_for_network = false end,
        collect = function(ensure_networking)
            return CWNGSync.collectDeliveries(self_stub, false, ensure_networking)
        end,
    }
end

local delivery = {
    id = 77,
    filename = "Queued.epub",
    claim_token = "claim-token",
}

local function testOverlappingExternalTriggerIsRejectedButContinuationsRun()
    local harness = newHarness({ wait_for_network = true })

    harness.collect(true)
    assertEqual(#harness.pending.network, 1,
        "the owning collection waits for its network continuation")

    -- ReaderReady, NetworkConnected and the manual menu all call the public
    -- two-argument entry point.  This second one is a genuinely external
    -- trigger arriving while the first collection owns the chain.
    harness.collect(false)
    assertEqual(harness.calls.inventory, 0,
        "an overlapping external trigger must not start a second collection")

    harness.setNetworkReady()
    harness.pending.network[1]()
    assertEqual(harness.calls.inventory, 1,
        "the owner's network continuation must remain admissible")

    harness.pending.inventory[1](true, {})
    assertEqual(harness.calls.claims, 1,
        "the inventory continuation starts exactly one claim")
    harness.pending.claims[1](true, { delivery = delivery })
    assertEqual(harness.calls.installs[delivery.id], 1,
        "the overlapping triggers must install one copy of the delivery")
    assertEqual(harness.calls.completions[delivery.id], 1,
        "the overlapping triggers must acknowledge the delivery once")

    assertEqual(#harness.pending.ticks, 1,
        "a successful delivery schedules its pagination continuation")
    harness.pending.ticks[1]()
    assertEqual(harness.calls.claims, 2,
        "the owner's pagination continuation must remain admissible")
    harness.pending.claims[2](true, {})
    assertEqual(harness.self.delivery_collection_running, nil,
        "the guard clears when pagination reaches an empty queue")
    assertEqual(harness.calls.inventory, 2,
        "the completed run sends one final fire-and-forget inventory report")

    -- A stale callback from the old ownership chain cannot resurrect it.
    harness.pending.network[1]()
    assertEqual(harness.calls.inventory, 2,
        "a stale internal continuation must be rejected after release")

    harness.collect(false)
    assertEqual(harness.calls.inventory, 3,
        "a fresh external trigger is admitted after release")
end

local function assertReleased(options, drive, message)
    local harness = newHarness(options)
    harness.collect(false)
    drive(harness)
    assertEqual(harness.self.delivery_collection_running, nil, message)
end

local function testEveryTerminalReturnReleasesOwnership()
    assertReleased({ credentials = false }, function() end,
        "missing credentials must release collection ownership")
    assertReleased({ server = false }, function() end,
        "a missing server must release collection ownership")
    assertReleased({ root_missing = true }, function(harness)
        harness.pending.inventory[1](true, {})
    end, "a missing library root must release collection ownership")
    assertReleased({}, function(harness)
        harness.pending.inventory[1](false, nil, "inventory failed")
    end, "an inventory failure must release collection ownership")
    assertReleased({}, function(harness)
        harness.pending.inventory[1](true, {})
        harness.pending.claims[1](false, nil, "claim failed")
    end, "a claim failure must release collection ownership")
    assertReleased({}, function(harness)
        harness.pending.inventory[1](true, {})
        harness.pending.claims[1](true, {})
    end, "an empty queue must release collection ownership")
    assertReleased({ install_fails = true }, function(harness)
        harness.pending.inventory[1](true, {})
        harness.pending.claims[1](true, { delivery = delivery })
    end, "an install failure must release collection ownership")
    assertReleased({ completion_fails = true }, function(harness)
        harness.pending.inventory[1](true, {})
        harness.pending.claims[1](true, { delivery = delivery })
    end, "a completion failure must release collection ownership")
end

local tests = {
    testOverlappingExternalTriggerIsRejectedButContinuationsRun,
    testEveryTerminalReturnReleasesOwnership,
}

for _, test in ipairs(tests) do test() end
print(string.format("delivery_collection_test.lua: %d tests passed", #tests))

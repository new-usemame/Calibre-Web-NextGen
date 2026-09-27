-- Behavioural coverage for the deletion half of CWNGSync:syncDeviceCapabilities.
--
-- #2328: several files queued for deletion from the devices page, one "Sync
-- now", and only one file went. The server hands out one named deletion per
-- claim, so a sync that claims once drains one row per sync. The production
-- function is loaded verbatim and driven against a fake server queue that
-- behaves like cps/services/device_capabilities.py: a claim returns the row
-- still CLAIMED if there is one, otherwise the oldest REQUESTED row.

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

    local header = "function CWNGSync:syncDeviceCapabilities("
    local start = assert(source:find(header, 1, true),
        "syncDeviceCapabilities not found in main.lua")
    local following = assert(source:find("\nfunction CWNGSync:", start + 1, true),
        "function following syncDeviceCapabilities not found")
    local chunk = "local CWNGSync = {}\n" .. source:sub(start, following - 1)
        .. "\nreturn CWNGSync\n"
    local loaded, load_error = load(chunk, "syncDeviceCapabilities", "t", env)
    assert(loaded, "syncDeviceCapabilities failed to parse: " .. tostring(load_error))
    return loaded()
end

-- options.paths: queued deletions, oldest first.
-- options.fail_ack_on: claim number whose acknowledgement is lost in transit.
-- options.refuse: path the device declines to delete (a digest mismatch, say).
local function newHarness(options)
    local rows = {}
    for index, path in ipairs(options.paths) do
        rows[index] = { id = index, lpath = path, claim_token = "t" .. index, state = "requested" }
    end
    local observed = { claims = 0, removed = {}, acks = {}, collections = 0, refreshed = {} }
    local ticks = {}

    local client = {}
    function client.claim_deletion(_, _user, _password, _model, _device_id, callback)
        observed.claims = observed.claims + 1
        assert(observed.claims <= 200, "deletion claims did not terminate")
        local chosen
        for _, row in ipairs(rows) do
            if row.state == "claimed" then chosen = row break end
        end
        if not chosen then
            for _, row in ipairs(rows) do
                if row.state == "requested" then chosen = row break end
            end
        end
        if not chosen then return callback(true, { deletion = nil }) end
        chosen.state = "claimed"
        callback(true, { deletion = {
            id = chosen.id, lpath = chosen.lpath, claim_token = chosen.claim_token,
        } })
    end
    function client.complete_deletion(_, _user, _password, _model, _device_id,
            deletion_id, claim_token, deleted, _reason, callback)
        if options.fail_ack_on == observed.claims then
            return callback(false, nil, "network down")
        end
        local row = rows[deletion_id]
        assertEqual(row.claim_token, claim_token, "acknowledgement carries the claim token")
        row.state = deleted and "completed" or "failed"
        table.insert(observed.acks, deletion_id)
        callback(true, {})
    end

    local env = {
        type = type,
        tostring = tostring,
        pcall = pcall,
        table = table,
        ensureServerConfigured = function(server) return server ~= nil end,
        NetworkMgr = { willRerunWhenOnline = function() return false end },
        logger = { dbg = function() end, info = function() end, warn = function() end },
        lfs = { attributes = function() return {} end },
        util = { removeFile = function() end },
        Device = { model = "test-device" },
        UIManager = { nextTick = function(_, fn) table.insert(ticks, fn) end },
        DeviceActions = {
            deleteNamed = function(deletion, root)
                if deletion.lpath == options.refuse then
                    return false, "checksum mismatch"
                end
                local path = root .. "/" .. deletion.lpath
                table.insert(observed.removed, deletion.lpath)
                return true, nil, path
            end,
        },
        require = function(name)
            assertEqual(name, "CWNGSyncClient", "only the sync client is required")
            return { new = function() return client end }
        end,
    }
    local CWNGSync = loadProductionFunction(env)
    local plugin = setmetatable({
        settings = { username = "reader", password = "secret", server = "https://cwng.test" },
        device_id = "device-1",
        path = "/plugins/cwngsync.koplugin",
    }, { __index = CWNGSync })
    function plugin:getDeliveryRootPath() return "/books" end
    function plugin:getDocumentDigest() return "digest" end
    function plugin:libraryEnabled() return true end
    function plugin:retireSnapshotCollections() observed.collections = observed.collections + 1 end
    function plugin:refreshLibraryViews(paths)
        for _, path in ipairs(paths) do table.insert(observed.refreshed, path) end
    end

    local function sync()
        plugin:syncDeviceCapabilities(true, false)
        while #ticks > 0 do table.remove(ticks, 1)() end
    end
    return sync, observed, rows
end

-- The reported case: three queued files, one sync, all three go.
do
    local sync, observed = newHarness({ paths = { "a.epub", "b.epub", "c.epub" } })
    sync()
    assertEqual(#observed.removed, 3, "one sync deletes every queued file")
    assertEqual(table.concat(observed.removed, ","), "a.epub,b.epub,c.epub",
        "deletions run in queue order")
    assertEqual(#observed.acks, 3, "every deletion is acknowledged")
    assertEqual(#observed.refreshed, 3, "the library view hears about every removed file")
    assertEqual(observed.collections, 1, "collections sync once, after the drain")
end

-- An empty queue still reaches the collection step, exactly once.
do
    local sync, observed = newHarness({ paths = {} })
    sync()
    assertEqual(observed.claims, 1, "an empty queue costs one claim")
    assertEqual(observed.collections, 1, "collections still sync with nothing to delete")
end

-- A file the device declines is reported failed and the drain moves on; the
-- server will not hand a failed row back, so the rest must still go.
do
    local sync, observed, rows = newHarness({
        paths = { "a.epub", "keep.epub", "c.epub" }, refuse = "keep.epub" })
    sync()
    assertEqual(table.concat(observed.removed, ","), "a.epub,c.epub",
        "a refused deletion does not stop the ones after it")
    assertEqual(rows[2].state, "failed", "the refused row is reported, not dropped")
    assertEqual(observed.collections, 1, "collections sync once after a mixed drain")
end

-- A lost acknowledgement leaves the row CLAIMED; the server would hand the same
-- row straight back, so the drain has to stop rather than spin on it.
do
    local sync, observed, rows = newHarness({
        paths = { "a.epub", "b.epub", "c.epub" }, fail_ack_on = 2 })
    sync()
    assertEqual(observed.claims, 2, "the drain stops at the unacknowledged claim")
    assertEqual(rows[2].state, "claimed", "the unacknowledged row waits for the next sync")
    assertEqual(rows[3].state, "requested", "later rows are left for the next sync")
    assertEqual(observed.collections, 1, "collections still sync after a failed ack")
    -- The next sync picks the claimed row back up (idempotent device delete) and finishes.
    sync()
    assertEqual(rows[2].state, "completed", "the next sync completes the stranded claim")
    assertEqual(rows[3].state, "completed", "and drains the rest")
end

-- A very large queue is drained in bounded batches, never an unbounded loop.
do
    local paths = {}
    for index = 1, 120 do paths[index] = "book" .. index .. ".epub" end
    local sync, observed = newHarness({ paths = paths })
    sync()
    assert(#observed.removed >= 20 and #observed.removed < 120,
        "one sync drains a bounded batch, got " .. #observed.removed)
    assertEqual(observed.collections, 1, "a capped drain still syncs collections once")
    local first = #observed.removed
    sync()
    assert(#observed.removed > first, "the next sync continues where the last stopped")
end

print("deletion_drain_test: 5 scenarios passed")

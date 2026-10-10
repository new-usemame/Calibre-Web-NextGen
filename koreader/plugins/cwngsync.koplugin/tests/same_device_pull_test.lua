-- Behavioural coverage for #2380: a pull must not refuse the server's
-- position just because this device was the last one to push it.
--
-- The reporter wiped KOReader's history on a Kobo, opened the book at 0% and
-- chose "Pull progress from other devices". The server still held the
-- Kobo's own 40% push, and the plugin answered "Latest progress is coming
-- from this device" and left the reader at the front of the book. The same
-- device_id is only proof the position is stale when the device still has a
-- position of its own; after a wipe it is the only copy of where they were.
--
-- Like document_digest_test.lua, this slices the real CWNGSync:getProgress
-- out of main.lua and runs it against stubs, so the decision under test is
-- the shipped one, not a restatement of it.

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

local function loadGetProgress(env)
    local main_path = assert(package.searchpath("main", package.path),
        "cannot locate main.lua via package.path")
    local f = assert(io.open(main_path, "r"), "cannot read " .. main_path)
    local source = f:read("*a")
    f:close()

    local header = "function CWNGSync:getProgress(ensure_networking, interactive)"
    local start = source:find(header, 1, true)
    assert(start, "getProgress not found in main.lua")
    local stop = source:find("\nend\n", start, true)
    assert(stop, "getProgress is unterminated")

    local chunk = "local CWNGSync = {}\n" .. source:sub(start, stop + 4) .. "\nreturn CWNGSync\n"
    local loaded, err = load(chunk, "getProgress", "t", env)
    assert(loaded, "getProgress failed to parse: " .. tostring(err))
    return loaded()
end

local THIS_MODEL, THIS_ID = "Kobo_spaBW", "kobo-device-id"

-- One pull of an open book. `remote` is the server's body; `local_percent`
-- is where the open book currently is (0-1, KOReader's rounding).
local function pull(opts)
    local seen = { messages = {}, synced_to = nil, confirms = 0 }
    local env = {
        pcall = pcall, type = type, tonumber = tonumber, require = function(name)
            if name == "CWNGSyncClient" then
                local Client = {
                    get_progress = function(_, _, _, _, callback) callback(true, opts.remote) end,
                }
                Client.new = function(_, o) return setmetatable(o, { __index = Client }) end
                return Client
            end
            error("unexpected require " .. name)
        end,
        logger = { dbg = function() end, warn = function() end },
        _ = function(s) return s end,
        T = function(s) return s end,
        Json = { decode = function() error("no json expected") end },
        Math = {
            roundPercent = function(p) return math.floor(p * 10000 + 0.5) / 10000 end,
            round = function(p) return math.floor(p + 0.5) end,
        },
        SyncLogic = SyncLogic,
        Device = { model = THIS_MODEL },
        NetworkMgr = { willRerunWhenOnline = function() return false end },
        UIManager = {
            getElapsedTimeSinceBoot = function() return 1000 end,
            show = function(_, widget)
                if widget.ok_callback then
                    seen.confirms = seen.confirms + 1
                    widget.ok_callback()
                else
                    table.insert(seen.messages, widget.text)
                end
            end,
        },
        InfoMessage = { new = function(_, o) return o end },
        ConfirmBox = { new = function(_, o) return o end },
        showSyncError = function() table.insert(seen.messages, "sync error") end,
        showSyncedMessage = function() table.insert(seen.messages, "synced") end,
        promptLogin = function() end,
        ensureServerConfigured = function() return true end,
        API_CALL_DEBOUNCE_DELAY = 25,
        SYNC_STRATEGY = { PROMPT = 1, SILENT = 2, DISABLE = 3 },
        table = table,
        setmetatable = setmetatable,
    }
    local CWNGSync = loadGetProgress(env)
    local plugin = setmetatable({
        settings = {
            username = "u", password = "p", server = "http://cwng",
            sync_forward = env.SYNC_STRATEGY.PROMPT, sync_backward = env.SYNC_STRATEGY.DISABLE,
        },
        device_id = THIS_ID,
        pull_timestamp = 0,
        last_page_turn_timestamp = 0,
        path = ".",
        hasCurrentDocument = function() return true end,
        getCurrentDocumentFile = function() return "/mnt/onboard/book.epub" end,
        getDocumentDigest = function() return "digest" end,
        getLastPercent = function() return opts.local_percent end,
        getLastProgress = function() return opts.local_progress end,
        syncToProgress = function(_, remote) seen.synced_to = remote end,
    }, { __index = CWNGSync })
    plugin:getProgress(false, opts.interactive)
    return seen
end

local function ownPush(percentage, progress)
    return {
        device = THIS_MODEL, device_id = THIS_ID,
        percentage = percentage, progress = progress, timestamp = 10,
    }
end

-- The report, exactly: history wiped, manual pull.
local function testManualPullAfterWipeRestoresOwnPosition()
    local seen = pull({
        interactive = true, local_percent = 0, local_progress = "/body/DocFragment[1].0",
        remote = ownPush(0.4, "/body/DocFragment[9].0"),
    })
    assert(seen.synced_to, "a manual pull after a wipe must move to the server's position; got: "
        .. table.concat(seen.messages, " | "))
    assertEqual(seen.confirms, 0, "with nothing local to lose it applies without asking")
    assertEqual(seen.synced_to.progress, "/body/DocFragment[9].0", "moves to the stored locator")
end

-- A manual pull is the user asking for the server's position. If this
-- device's own push differs from where the book is open, it is offered
-- rather than refused -- but asked, since the reader has usually moved on.
local function testManualPullHonoursOwnDifferingPosition()
    local seen = pull({
        interactive = true, local_percent = 0.1, local_progress = "/body/DocFragment[2].0",
        remote = ownPush(0.4, "/body/DocFragment[9].0"),
    })
    assertEqual(seen.confirms, 1, "going back to its own earlier position is confirmed first")
    assert(seen.synced_to, "an explicit pull must apply a differing position once confirmed")
end

-- Matching position: still reported as already in sync, not re-applied.
local function testManualPullAtSamePositionIsAlreadySynced()
    local seen = pull({
        interactive = true, local_percent = 0.4, local_progress = "/body/DocFragment[9].0",
        remote = ownPush(0.4, "/body/DocFragment[9].0"),
    })
    assertEqual(seen.synced_to, nil, "nothing to apply")
    assertEqual(seen.messages[1], "The progress has already been synchronized.", "says so")
end

-- Opening a wiped book with automatic sync on: the device has no position
-- of its own, so its earlier push is the only record. It goes through the
-- normal forward-sync strategy (a prompt here) instead of being dropped.
local function testAutomaticPullAfterWipeOffersOwnPosition()
    local seen = pull({
        interactive = false, local_percent = 0, local_progress = "/body/DocFragment[1].0",
        remote = ownPush(0.4, "/body/DocFragment[9].0"),
    })
    assertEqual(seen.confirms, 1, "asks before moving, per the forward-sync setting")
    assert(seen.synced_to, "and moves once confirmed")
end

-- What the same-device rule exists for: this device has read further than
-- its last push reached the server (offline, or the push still queued).
-- Opening the book must not drag it back to its own stale position.
local function testAutomaticPullKeepsUnpushedLocalReading()
    local seen = pull({
        interactive = false, local_percent = 0.6, local_progress = "/body/DocFragment[12].0",
        remote = ownPush(0.4, "/body/DocFragment[9].0"),
    })
    assertEqual(seen.synced_to, nil, "own stale push must not move the reader")
    assertEqual(seen.confirms, 0, "and must not even ask")
end

-- Another device's position is untouched by this change.
local function testOtherDeviceStillApplies()
    local body = ownPush(0.4, "/body/DocFragment[9].0")
    body.device_id = "phone-id"
    body.device = "Pixel"
    local seen = pull({
        interactive = true, local_percent = 0.6, local_progress = "/body/DocFragment[12].0",
        remote = body,
    })
    assert(seen.synced_to, "another device's position applies on a manual pull")
end

local function testPolicy()
    local own = ownPush(0.4, "x")
    local ignore = SyncLogic.shouldIgnoreOwnRemoteProgress
    assertEqual(ignore(own, THIS_MODEL, THIS_ID, false, 0.6), true, "own push, local reading: ignore")
    assertEqual(ignore(own, THIS_MODEL, THIS_ID, false, 0), false, "own push, no local position: use it")
    assertEqual(ignore(own, THIS_MODEL, THIS_ID, false, nil), false, "own push, no sidecar at all: use it")
    assertEqual(ignore(own, THIS_MODEL, THIS_ID, true, 0.6), false, "explicit pull: never ignored")
    assertEqual(ignore(own, "Other", THIS_ID, false, 0.6), false, "another device is never ignored")
end

testManualPullAfterWipeRestoresOwnPosition()
testManualPullHonoursOwnDifferingPosition()
testManualPullAtSamePositionIsAlreadySynced()
testAutomaticPullAfterWipeOffersOwnPosition()
testAutomaticPullKeepsUnpushedLocalReading()
testOtherDeviceStillApplies()
testPolicy()

print("same_device_pull tests passed")

-- Share my place rollout (spec state/kindle-kids/specs/2026-10-11-share-my-place.md,
-- sections 10 and 12): the per-device gates are behind a server switch, so the
-- plugin's built-in-Progress-sync guard must stay dormant unless the server
-- says the gates are enforced for this account. The server says so in the
-- auth reply as `share_place_gates`; the plugin hands that value to the guard
-- as `share_place_gates`. false, or absent (an old server), means the guard
-- touches nothing: no setting written, no notice, no wrapped pull.
package.path = '../?.lua;./?.lua;' .. package.path
local Guard = require('builtin_sync_guard')

local function setup(gates)
    local persisted = { settings = { custom_server = 'https://cwng.invalid/base/kosync',
                                     auto_sync = true, username = 'reader' } }
    local counts = { flushes = 0, events = 0, stops = 0, notices = 0, pulls = 0 }
    local seen = {}
    local reader = { readSetting = function(_, key) return seen[key] end,
        saveSetting = function(_, key, value) seen[key] = value end,
        flush = function() end }
    local store = { readSetting = function(_, key) return persisted[key] end,
        saveSetting = function(_, key, value) persisted[key] = value end,
        flush = function() counts.flushes = counts.flushes + 1 end }
    local live = { settings = persisted.settings, settings_obj = store,
        periodic_push_task = function() end, periodic_push_scheduled = true,
        registerEvents = function() counts.events = counts.events + 1 end,
        getProgress = function() counts.pulls = counts.pulls + 1 end }
    local args = { server = 'https://cwng.invalid/base', reader_settings = reader,
        open_settings = function() return store end, builtin = live,
        unschedule = function() counts.stops = counts.stops + 1 end,
        notice = function() counts.notices = counts.notices + 1 end,
        share_place_gates = gates }
    return args, persisted, counts, seen
end

local function assertDormant(gates, label)
    local args, persisted, counts, seen = setup(gates)
    assert(not Guard.check(args), label .. ': the guard reports it did nothing')
    assert(persisted.settings.auto_sync == true, label .. ': built-in automatic sync left on')
    assert(counts.flushes + counts.events + counts.stops + counts.notices == 0,
        label .. ': no write, no re-registration, no unscheduling, no notice')
    assert(args.builtin.periodic_push_scheduled == true, label .. ': scheduled push left alone')
    assert(next(seen) == nil, label .. ': nothing remembered, so it can act once enabled')
    -- An automatic pull the built-in plugin already queued still runs.
    args.builtin:getProgress(true, false)
    assert(counts.pulls == 1, label .. ': the built-in automatic pull is not suppressed')
end

assertDormant(false, 'server says gates are off')
assertDormant(nil, 'old server says nothing')

local args, persisted, counts = setup(true)
assert(Guard.check(args), 'gates on: the guard acts')
assert(persisted.settings.auto_sync == false, 'gates on: built-in automatic sync turned off')
assert(counts.notices == 1, 'gates on: the reader is told once')
args.builtin:getProgress(true, false)
assert(counts.pulls == 0, 'gates on: a queued automatic pull is suppressed')

print('share-place guard dormant tests passed')

--[[
Device-write provider seam (Phase 2, generic transport).

Selects how annotations the server says belong on this device get written
locally. Today there is one provider — KoboReader.sqlite (Kobo only), which
puts highlights onto stock Nickel. A KOReader-native (`.sdr` sidecar) provider
that works on every KOReader device is a future addition behind this same
interface; nothing else in the plugin needs to change when it lands.

Provider interface:
    available()                       -> bool   (is this provider usable here?)
    readAll(volume_id)                -> list|nil (device's annotations, portable;
                                                  nil when they could not be read)
    applyToDevice(portables, vol_id)  -> count   (write server annotations locally)
    backup()                          -> path|false

`readAll` returning nil rather than {} is load-bearing, not a style choice. The
caller diffs the returned list against the ids it last pushed to name deletions,
so "could not read" and "the user deleted everything" produce opposite actions
from the same empty table. Providers must therefore report failure as nil and
reserve {} for a device that genuinely holds no highlights (#920). Read the
result through SyncLogic.resolveLocalSet, which enforces this and treats a
provider that throws as unreadable too.
]]--

local DeviceAnnotations = {}

local PROVIDERS = {
    require("koreader_annotations_provider"),
    require("kobo_sqlite_provider"),
}

-- First provider that reports itself usable on this device, or nil.
function DeviceAnnotations.getProvider(ui, document_digest)
    for _, p in ipairs(PROVIDERS) do
        if p.setContext then p.setContext(ui, document_digest) end
        local ok, usable = pcall(p.available)
        if ok and usable then
            return p
        end
    end
    return nil
end

function DeviceAnnotations.available()
    return DeviceAnnotations.getProvider() ~= nil
end

return DeviceAnnotations

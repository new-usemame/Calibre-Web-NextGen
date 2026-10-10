-- Keeps this e-reader's sync ID its own.
--
-- The server tells e-readers apart by KOReader's `device_id`, which lives in
-- settings.reader.lua. Copying the whole koreader folder to a second Kindle
-- (to avoid typing passwords again) copies that ID too, and the server then
-- merges both e-readers into one device, each skipping the other's progress
-- as its own (#2351). Next to the ID we keep a hash of the e-reader's
-- hardware serial. The hash travels with a copied settings file, so when it
-- no longer matches the hardware it is on, this e-reader gets a new ID.
--
-- Only e-readers that expose a serial are covered (Kindle, Kobo). Elsewhere
-- the ID is left exactly as it was.

local DeviceIdentity = {}

local FINGERPRINT_KEY = "cwngsync_hardware_fingerprint"
DeviceIdentity.FINGERPRINT_KEY = FINGERPRINT_KEY

local function readFirstLine(path)
    local file = io.open(path, "r")
    if not file then return nil end
    local line = file:read("*l")
    file:close()
    return line
end

-- The hardware serial, or nil when this e-reader has none we can read.
-- Kindle: /proc/usid. Kobo: the first field of .kobo/version.
function DeviceIdentity.serial(device, read_line)
    read_line = read_line or readFirstLine
    local serial
    if device.isKindle then
        serial = read_line("/proc/usid")
    elseif device.isKobo then
        local version = read_line("/mnt/onboard/.kobo/version")
        serial = version and version:match("^([^,]*)")
    end
    serial = serial and serial:match("^%s*(.-)%s*$")
    if serial == nil or serial == "" then return nil end
    return serial
end

-- Settle the ID for this e-reader. `settings` is KOReader's
-- G_reader_settings (readSetting / saveSetting). Returns true only when the
-- ID was replaced because it belonged to another e-reader.
function DeviceIdentity.settle(settings, device, hash, new_id, read_line)
    local device_id = settings:readSetting("device_id")
    if device_id == nil or device_id == "" then
        device_id = new_id()
        settings:saveSetting("device_id", device_id)
    end

    local serial = DeviceIdentity.serial(device, read_line)
    if not serial then return false end

    -- Only the hash is kept, so a shared settings file does not carry the serial.
    local fingerprint = hash("cwngsync-hardware:" .. serial)
    local recorded = settings:readSetting(FINGERPRINT_KEY)
    if recorded == fingerprint then return false end

    settings:saveSetting(FINGERPRINT_KEY, fingerprint)
    if recorded == nil then return false end

    settings:saveSetting("device_id", new_id())
    return true
end

return DeviceIdentity

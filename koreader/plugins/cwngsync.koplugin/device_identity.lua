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
-- Kindle and Kobo are fingerprinted by their serial. Other e-readers (the
-- Android build on a Tolino or a Boox, PocketBook) expose no serial we can
-- read, so they are fingerprinted by KOReader's model name instead. That still
-- catches a settings file restored onto a different kind of e-reader, such as
-- a Kindle's onto a Tolino (#2952), but not one copied between two e-readers
-- of the same model. A Kindle or Kobo whose serial cannot be read on one start
-- is left exactly as it was.

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

-- What identifies this e-reader's hardware, hashed, or nil when nothing can be
-- said this time. Only the hash is kept, so a shared settings file does not
-- carry the serial.
function DeviceIdentity.fingerprint(device, hash, read_line)
    if device.isKindle or device.isKobo then
        local serial = DeviceIdentity.serial(device, read_line)
        if not serial then return nil end
        return hash("cwngsync-hardware:" .. serial)
    end
    local model = type(device.model) == "string" and device.model:match("^%s*(.-)%s*$")
    if not model or model == "" then return nil end
    return hash("cwngsync-model:" .. model)
end

-- Settle the ID for this e-reader. `settings` is KOReader's
-- G_reader_settings (readSetting / saveSetting / flush). Returns true only
-- when the ID was replaced because it belonged to another e-reader.
--
-- Anything it records is flushed at once. KOReader otherwise writes the file
-- when it exits, so a copy taken while it runs would carry the ID without the
-- fingerprint, and the second e-reader would keep the first one's ID (#2351).
function DeviceIdentity.settle(settings, device, hash, new_id, read_line)
    local changed = false
    local function save(key, value)
        settings:saveSetting(key, value)
        changed = true
    end
    local replaced = false

    local device_id = settings:readSetting("device_id")
    if device_id == nil or device_id == "" then
        save("device_id", new_id())
    end

    local fingerprint = DeviceIdentity.fingerprint(device, hash, read_line)
    local recorded = settings:readSetting(FINGERPRINT_KEY)
    if fingerprint and recorded ~= fingerprint then
        save(FINGERPRINT_KEY, fingerprint)
        if recorded ~= nil then
            save("device_id", new_id())
            replaced = true
        end
    end

    if changed and settings.flush then settings:flush() end
    return replaced
end

return DeviceIdentity

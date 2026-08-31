-- Atomic, retry-safe installation of one server-claimed book.
--
-- This module has no KOReader UI dependencies so the crash window can be
-- exercised with the shipped Lua runtime. The caller supplies filesystem,
-- checksum, download and durable-settings operations.

local Delivery = {}
local STORAGE_HEADROOM = 128 * 1024

local function join(root, name)
    return root:gsub("/+$", "") .. "/" .. name
end

local function validName(name)
    return type(name) == "string" and name ~= ""
        and name ~= "." and name ~= ".."
        and not name:find("[/\\]")
        and not name:find("%z")
end

local function receiptMatches(receipt, delivery, path, opts)
    if type(receipt) ~= "table" or receipt.delivery_id ~= delivery.id
            or receipt.path ~= path or type(receipt.size) ~= "number"
            or type(receipt.checksum) ~= "string" then
        return false
    end
    local attributes = opts.attributes(path)
    if type(attributes) ~= "table" or attributes.size ~= receipt.size then
        return false
    end
    return opts.digest(path) == receipt.checksum
end

function Delivery.install(delivery, root, opts)
    if type(delivery) ~= "table" or type(delivery.id) ~= "number"
            or type(delivery.filename) ~= "string" or type(root) ~= "string"
            or root == "" then
        return nil, "invalid delivery"
    end
    local required = {
        "attributes", "digest", "sanitize", "remove",
        "persist_receipt", "rename", "download",
    }
    for _, name in ipairs(required) do
        if type(opts and opts[name]) ~= "function" then
            return nil, "missing delivery operation: " .. name
        end
    end

    local filename = opts.sanitize(delivery.filename, root)
    if not validName(filename) then
        return nil, "invalid delivery filename"
    end
    local final_path = join(root, filename)
    local temp_path = final_path .. ".cwngsync.part"

    if receiptMatches(opts.receipt, delivery, final_path, opts) then
        local attributes = opts.attributes(final_path)
        return {
            path = final_path,
            lpath = filename,
            checksum = opts.receipt.checksum,
            size = attributes.size,
            mtime = math.floor(attributes.modification or os.time()),
            reused = true,
        }
    end

    -- This is the authoritative fit check: it runs immediately before the
    -- downloader opens its temporary file. Server measurements are useful for
    -- queue/claim admission, but disk space can change after either response.
    if type(opts.available_space) == "function" and type(delivery.size) == "number" then
        local available = tonumber(opts.available_space(root))
        if available == nil or available < delivery.size + STORAGE_HEADROOM then
            return nil, "insufficient storage", available
        end
    end

    local downloaded, content_length, response_checksum, reason = opts.download(temp_path)
    if not downloaded then
        opts.remove(temp_path)
        return nil, reason or "download failed"
    end
    local attributes = opts.attributes(temp_path)
    if type(attributes) ~= "table" or type(attributes.size) ~= "number" then
        opts.remove(temp_path)
        return nil, "downloaded file could not be read"
    end

    local expected_size = content_length or delivery.size
    if type(expected_size) == "number" and attributes.size ~= expected_size then
        opts.remove(temp_path)
        return nil, string.format(
            "download size mismatch: expected %d, received %d",
            expected_size, attributes.size)
    end
    local checksum = opts.digest(temp_path)
    if type(checksum) ~= "string" or checksum == "" then
        opts.remove(temp_path)
        return nil, "downloaded file checksum could not be calculated"
    end
    local expected_checksum = response_checksum or delivery.checksum
    if type(expected_checksum) == "string" and expected_checksum ~= ""
            and checksum ~= expected_checksum then
        opts.remove(temp_path)
        return nil, "download checksum mismatch"
    end

    -- Persist before rename. If power disappears immediately after the atomic
    -- rename, the next lease can prove that this exact file was installed and
    -- acknowledge it without downloading a duplicate. If power disappears
    -- before rename, the receipt is ignored because the final file is absent.
    local receipt = {
        delivery_id = delivery.id,
        path = final_path,
        lpath = filename,
        checksum = checksum,
        size = attributes.size,
    }
    local persisted, persist_error = opts.persist_receipt(receipt)
    if persisted == false then
        opts.remove(temp_path)
        return nil, persist_error or "delivery receipt could not be saved"
    end
    local renamed, rename_error = opts.rename(temp_path, final_path)
    if not renamed then
        opts.remove(temp_path)
        return nil, rename_error or "atomic install failed"
    end

    local final_attributes = opts.attributes(final_path) or attributes
    return {
        path = final_path,
        lpath = filename,
        checksum = checksum,
        size = final_attributes.size,
        mtime = math.floor(final_attributes.modification or os.time()),
        reused = false,
    }
end

return Delivery

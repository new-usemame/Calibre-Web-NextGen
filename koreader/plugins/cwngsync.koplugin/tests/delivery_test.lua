package.path = table.concat({
    "./?.lua",
    "../?.lua",
    package.path,
}, ";")

local Delivery = require("delivery")

local function assertEqual(actual, expected, message)
    if actual ~= expected then
        error(string.format("%s\nexpected: %s\nactual: %s",
            message, tostring(expected), tostring(actual)), 2)
    end
end

local function newFilesystem()
    local files = {}
    local function attributes(path)
        local value = files[path]
        if not value then return nil end
        return { size = #value, modification = 1777777777 }
    end
    return files, attributes
end

local function digestFor(files, path)
    local value = files[path]
    return value and ("digest:" .. value) or nil
end

local function testInterruptedAfterRenameReusesOneAtomicFile()
    local files, attributes = newFilesystem()
    local receipt
    local downloads = 0
    local root = "/books"
    local delivery = {
        id = 9,
        filename = "Queued Book.epub",
        checksum = "digest:complete-bytes",
        size = 14,
    }
    local opts = {
        attributes = attributes,
        digest = function(path) return digestFor(files, path) end,
        sanitize = function(name) return name end,
        remove = function(path) files[path] = nil end,
        persist_receipt = function(value) receipt = value end,
        rename = function(source, destination)
            assert(receipt, "receipt must be durable before the atomic rename")
            files[destination] = assert(files[source])
            files[source] = nil
            return true
        end,
        download = function(path)
            downloads = downloads + 1
            files[path] = "complete-bytes"
            return true, 14, "digest:complete-bytes"
        end,
    }

    local installed, reason = Delivery.install(delivery, root, opts)
    assert(installed, reason)
    assertEqual(files["/books/Queued Book.epub"], "complete-bytes",
        "the complete temp file is renamed into place")
    assertEqual(files["/books/Queued Book.epub.cwngsync.part"], nil,
        "no partial file remains after rename")

    -- Simulate a flat battery after rename and before the completion request.
    -- The server reclaims the same queue row; the durable receipt must turn the
    -- retry into an acknowledgement of the same file, not another download.
    opts.receipt = receipt
    opts.download = function()
        error("a retry with a valid receipt must not download again")
    end
    local retried, retry_reason = Delivery.install(delivery, root, opts)
    assert(retried, retry_reason)

    assertEqual(downloads, 1, "one delivery produces one downloaded file")
    assertEqual(retried.path, installed.path, "the retry acknowledges the same path")
    assertEqual(retried.reused, true, "the retry is identified as receipt reuse")
end

local function testTruncatedDownloadNeverReachesTheFinalPath()
    local files, attributes = newFilesystem()
    local receipt_writes = 0
    local opts = {
        attributes = attributes,
        digest = function(path) return digestFor(files, path) end,
        sanitize = function(name) return name end,
        remove = function(path) files[path] = nil end,
        persist_receipt = function() receipt_writes = receipt_writes + 1 end,
        rename = function(source, destination)
            files[destination] = files[source]
            files[source] = nil
            return true
        end,
        download = function(path)
            files[path] = "short"
            return true, 14, "digest:complete-bytes"
        end,
    }
    local delivery = {
        id = 9,
        filename = "Queued Book.epub",
        checksum = "digest:complete-bytes",
        size = 14,
    }

    local installed, reason = Delivery.install(delivery, "/books", opts)

    assertEqual(installed, nil, "a truncated response is not installed")
    assert(reason:match("size"), "the failure reason names the size mismatch")
    assertEqual(files["/books/Queued Book.epub"], nil,
        "the final path is never populated with partial bytes")
    assertEqual(files["/books/Queued Book.epub.cwngsync.part"], nil,
        "the rejected partial file is removed")
    assertEqual(receipt_writes, 0, "no durable receipt describes rejected bytes")
end

local tests = {
    testInterruptedAfterRenameReusesOneAtomicFile,
    testTruncatedDownloadNeverReachesTheFinalPath,
}

for _, test in ipairs(tests) do test() end
print(string.format("delivery_test.lua: %d tests passed", #tests))

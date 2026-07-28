-- Behavioural coverage for CWASync:getDocumentDigest (#991).
--
-- sync_logic_test.lua proves the precedence POLICY. This proves the production
-- WIRING: it slices the real getDocumentDigest source out of main.lua and loads
-- it into a sandbox with stubbed util / io / DocSettings / self.ui, so the
-- closures under test are the ones that ship. A cache-first regression, a
-- swapped argument pair, or hashing the wrong path all fail here rather than
-- surviving a text match.
--
-- Loading all of main.lua would mean stubbing the whole KOReader widget stack,
-- so only the one function is extracted. It stays honest because the extraction
-- is verbatim: if the function stops parsing, or stops calling the policy, the
-- test errors instead of silently passing.

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

-- Verbatim slice of the shipped function. Every nested block in main.lua is
-- indented, so the first column-zero `end` closes it.
local function loadProductionFunction(env)
    -- Resolved through package.path (set above) rather than a literal "../",
    -- so this runs from the plugin dir and from tests/ alike -- the same way
    -- sync_logic_test.lua and device_annotations_test.lua locate their modules.
    -- A hardcoded relative path passes only from tests/ and fails elsewhere
    -- with "cannot read", which reads like a missing file rather than a cwd.
    local main_path = assert(package.searchpath("main", package.path),
        "cannot locate main.lua via package.path")
    local f = assert(io.open(main_path, "r"), "cannot read " .. main_path)
    local source = f:read("*a")
    f:close()

    local header = "function CWASync:getDocumentDigest(file_path)"
    local start = source:find(header, 1, true)
    assert(start, "getDocumentDigest not found in main.lua")
    local stop = source:find("\nend\n", start, true)
    assert(stop, "getDocumentDigest is unterminated")

    local body = source:sub(start, stop + 4)
    local chunk = "local CWASync = {}\n" .. body .. "\nreturn CWASync\n"

    local loaded, err = load(chunk, "getDocumentDigest", "t", env)
    assert(loaded, "getDocumentDigest failed to parse: " .. tostring(err))
    return loaded()
end

-- A sandbox recording what the function reached for, so call ORDER and the
-- PATH it hashed are both assertable.
local function newEnv(opts)
    opts = opts or {}
    local calls = {
        hashed = {},
        sidecar_opened = {},
        ui_sidecar_reads = 0,
        samples = {}, -- every {offset, size} the sampler asked for, in order
        closes = 0,
    }

    local env = {
        pcall = pcall,
        type = type,
        table = table,
        io = {
            open = function(path)
                table.insert(calls.hashed, path)
                -- io_empty opens successfully and reads EOF at every offset:
                -- a zero-byte file, which is NOT the same as an unopenable one.
                if not opts.io_bytes and not opts.io_empty and not opts.io_every then
                    return nil
                end
                local pos = 0
                return {
                    seek = function(_, _, offset)
                        if opts.throw_at_offset and offset == opts.throw_at_offset then
                            error("SD card detached")
                        end
                        pos = offset
                        return offset
                    end,
                    read = function(_, size)
                        table.insert(calls.samples, { offset = pos, size = size })
                        if opts.io_empty then
                            return nil
                        end
                        -- io_every keeps the loop running to its full extent so
                        -- every sampled offset is observable; io_bytes stops it
                        -- at the first empty read, one offset in.
                        if opts.io_every then
                            return opts.io_every
                        end
                        return pos == 0 and opts.io_bytes or nil
                    end,
                    close = function() calls.closes = calls.closes + 1 end,
                }
            end,
        },
        -- LuaJIT semantics, which the sampler's offsets depend on and the
        -- server mirrors: only the low 5 bits of the shift count are used and
        -- the result wraps to 32 bits, so lshift(1024, -2) is 0, not 1024.
        -- The previous stub returned the operand unshifted, which made every
        -- offset look like 1024 and pinned nothing.
        bit = {
            lshift = function(a, b)
                local masked = b % 32
                return (a * 2 ^ masked) % 2 ^ 32
            end,
        },
        md5 = function(s) return "io-fallback:" .. s end,
        util = {},
        SyncLogic = SyncLogic,
        require = function(name)
            assert(name == "docsettings", "unexpected require: " .. name)
            if opts.no_docsettings then
                error("docsettings unavailable")
            end
            return {
                open = function(_, path)
                    table.insert(calls.sidecar_opened, path)
                    return {
                        readSetting = function(_, key)
                            assertEqual(key, "partial_md5_checksum", "sidecar key")
                            return opts.sidecar
                        end,
                    }
                end,
            }
        end,
    }

    if opts.partial_md5 ~= nil then
        env.util.partialMD5 = function(path)
            table.insert(calls.hashed, path)
            if opts.partial_md5 == false then
                error("unreadable")
            end
            return opts.partial_md5
        end
    end

    local self_stub = {
        getCurrentDocumentFile = function() return opts.current_file end,
        ui = {
            doc_settings = opts.ui_sidecar ~= nil and {
                readSetting = function(_, key)
                    assertEqual(key, "partial_md5_checksum", "ui sidecar key")
                    calls.ui_sidecar_reads = calls.ui_sidecar_reads + 1
                    return opts.ui_sidecar
                end,
            } or nil,
        },
    }

    return env, self_stub, calls
end

local function digest(opts, file_path)
    local env, self_stub, calls = newEnv(opts)
    local CWASync = loadProductionFunction(env)
    self_stub.getDocumentDigest = CWASync.getDocumentDigest
    return CWASync.getDocumentDigest(self_stub, file_path), calls
end

-- The regression. The device holds bytes the server never registered because
-- the sidecar outlived the file being replaced; hashing has to win.
local function testStaleSidecarLosesToTheFile()
    local value, calls = digest({
        current_file = "/mnt/us/documents/Book.epub",
        partial_md5 = "fresh",
        ui_sidecar = "stale",
    })
    assertEqual(value, "fresh", "the digest of the bytes on disk must win")
    assertEqual(calls.ui_sidecar_reads, 0,
        "the sidecar must not even be read when the file could be hashed")
    assertEqual(calls.hashed[1], "/mnt/us/documents/Book.epub",
        "the current document's file is the one hashed")
end

-- An unchanged book must behave exactly as it did before the fix.
local function testAccurateSidecarIsANoOp()
    local value = digest({
        current_file = "/b.epub", partial_md5 = "same", ui_sidecar = "same",
    })
    assertEqual(value, "same", "an accurate cache resolves to the same digest")
end

-- Detached SD card / deleted file: a stale digest still beats none.
local function testUnhashableFileFallsBackToSidecar()
    local value = digest({
        current_file = "/gone.epub", partial_md5 = false, ui_sidecar = "cached",
    })
    assertEqual(value, "cached", "an unhashable file falls back to the cache")

    local none = digest({ current_file = "/gone.epub", partial_md5 = false })
    assertEqual(none, nil, "no file and no cache resolves to nil")
end

-- Bulk pull passes an explicit path; both hashing and the sidecar must follow
-- that path rather than whatever document happens to be open.
local function testExplicitPathIsHonoured()
    local value, calls = digest({
        current_file = "/open-book.epub",
        partial_md5 = "explicit",
        sidecar = "other",
    }, "/library/Target.epub")
    assertEqual(value, "explicit", "the explicit path is hashed")
    assertEqual(calls.hashed[1], "/library/Target.epub",
        "the explicit path is hashed, not the open document")
    assertEqual(#calls.sidecar_opened, 0,
        "a successful hash short-circuits opening the sidecar")
end

local function testExplicitPathFallbackOpensThatPathsSidecar()
    local value, calls = digest({
        current_file = "/open-book.epub", partial_md5 = false, sidecar = "from-sidecar",
    }, "/library/Target.epub")
    assertEqual(value, "from-sidecar", "the explicit path's sidecar is the fallback")
    assertEqual(calls.sidecar_opened[1], "/library/Target.epub",
        "the sidecar opened is the explicit path's, not the open document's")
end

-- Old KOReader builds without util.partialMD5 must still hash, via the inline
-- sampler, rather than silently falling through to the cache.
local function testInlineSamplerIsUsedWhenPartialMD5IsAbsent()
    local value, calls = digest({
        current_file = "/b.epub", io_bytes = "BYTES", ui_sidecar = "stale",
    })
    assertEqual(value, "io-fallback:BYTES", "the inline sampler hashes the file")
    assertEqual(calls.ui_sidecar_reads, 0, "the sampler still beats the cache")
end

-- The sampler's offsets ARE the digest. If they drift, every digest this
-- device reports stops matching the server's and sync breaks for everyone on a
-- build without util.partialMD5 -- silently, because the value still looks like
-- a hash. The expected sequence is KOReader's, mirrored by the server in
-- cps/progress_syncing/checksums/koreader.py.
local function testInlineSamplerReadsKOReaderOffsets()
    -- Every read returns bytes, so the loop runs to its full extent and all
    -- twelve offsets are observable. A stub that answers only at offset 0 stops
    -- the loop after one sample and pins nothing beyond it.
    local value, calls = digest({ current_file = "/b.epub", io_every = "X" })

    local expected = {
        0, 1024, 4096, 16384, 65536, 262144,
        1048576, 4194304, 16777216, 67108864, 268435456, 1073741824,
    }
    assertEqual(#calls.samples, #expected, "the sampler takes exactly 12 samples")
    for i, sample in ipairs(calls.samples) do
        assertEqual(sample.offset, expected[i], "sample " .. i .. " offset")
        assertEqual(sample.size, 1024, "sample " .. i .. " reads exactly 1 KiB")
    end
    assertEqual(value, "io-fallback:" .. string.rep("X", #expected),
        "the digest covers every sample, in order")
end

-- Sampling must stop at the first empty read rather than walking all twelve
-- offsets past the end of a short file.
local function testInlineSamplerStopsAtEOF()
    local _, calls = digest({ current_file = "/b.epub", io_bytes = "BYTES" })
    assertEqual(#calls.samples, 2, "a short file stops at the first empty read")
    assertEqual(calls.samples[1].offset, 0, "first sample is the file head")
end

-- The whole point of #991 is never reporting bytes we do not hold. A zero-byte
-- file is readable, so it has a real digest -- the server returns md5("") for
-- it -- and must not be answered from the stale sidecar.
local function testEmptyFileHashesRatherThanFallingBackToSidecar()
    local value, calls = digest({
        current_file = "/empty.epub", io_empty = true, ui_sidecar = "stale",
    })
    assertEqual(value, "io-fallback:", "a zero-byte file hashes to md5 of no bytes")
    assertEqual(calls.ui_sidecar_reads, 0, "the empty file still beats the cache")
end

-- A card yanked mid-hash must not strand the descriptor: bulk pull runs this
-- once per book, so a leak per book exhausts the device.
local function testHandleIsClosedWhenHashingThrows()
    local value, calls = digest({
        current_file = "/b.epub",
        io_bytes = "BYTES",
        throw_at_offset = 1024,
        ui_sidecar = "cached",
    })
    assertEqual(calls.closes, 1, "the file handle is closed even when seek throws")
    assertEqual(value, "cached", "a throwing hash falls back to the cache")
end

-- A broken docsettings module must not take the sync down.
local function testMissingDocSettingsModuleIsSurvivable()
    local value = digest({
        current_file = "/b.epub", partial_md5 = false, no_docsettings = true,
    }, "/library/Target.epub")
    assertEqual(value, nil, "an unavailable docsettings module resolves to nil")
end

testInlineSamplerReadsKOReaderOffsets()
testInlineSamplerStopsAtEOF()
testEmptyFileHashesRatherThanFallingBackToSidecar()
testHandleIsClosedWhenHashingThrows()
testStaleSidecarLosesToTheFile()
testAccurateSidecarIsANoOp()
testUnhashableFileFallsBackToSidecar()
testExplicitPathIsHonoured()
testExplicitPathFallbackOpensThatPathsSidecar()
testInlineSamplerIsUsedWhenPartialMD5IsAbsent()
testMissingDocSettingsModuleIsSurvivable()

print("document_digest tests passed")

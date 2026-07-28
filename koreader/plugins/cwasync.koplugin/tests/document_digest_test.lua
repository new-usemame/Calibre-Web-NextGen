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
    local f = assert(io.open("../main.lua", "r"), "cannot read ../main.lua")
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
    local calls = { hashed = {}, sidecar_opened = {}, ui_sidecar_reads = 0 }

    local env = {
        pcall = pcall,
        type = type,
        table = table,
        io = {
            open = function(path)
                table.insert(calls.hashed, path)
                if not opts.io_bytes then
                    return nil
                end
                local pos = 0
                return {
                    seek = function(_, _, offset) pos = offset; return offset end,
                    read = function() return pos == 0 and opts.io_bytes or nil end,
                    close = function() end,
                }
            end,
        },
        bit = { lshift = function(a, b) return b < 0 and 0 or a end },
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

-- A broken docsettings module must not take the sync down.
local function testMissingDocSettingsModuleIsSurvivable()
    local value = digest({
        current_file = "/b.epub", partial_md5 = false, no_docsettings = true,
    }, "/library/Target.epub")
    assertEqual(value, nil, "an unavailable docsettings module resolves to nil")
end

testStaleSidecarLosesToTheFile()
testAccurateSidecarIsANoOp()
testUnhashableFileFallsBackToSidecar()
testExplicitPathIsHonoured()
testExplicitPathFallbackOpensThatPathsSidecar()
testInlineSamplerIsUsedWhenPartialMD5IsAbsent()
testMissingDocSettingsModuleIsSurvivable()

print("document_digest tests passed")

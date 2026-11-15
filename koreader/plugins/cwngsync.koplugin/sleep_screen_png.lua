-- SPDX-License-Identifier: GPL-3.0-or-later
-- Validate the server's canonical L8 PNG without a native image dependency.
-- This is also useful in a headless KOReader: inflate is bounded by the exact
-- scanline count, checks both zlib and PNG checksums, and accepts no animation.
local PNG = {}
local function u32(s, p)
    local a, b, c, d = s:byte(p, p + 3)
    assert(d, "short PNG")
    return ((a * 256 + b) * 256 + c) * 256 + d
end
local function xor(a, b)
    local n, p = 0, 1
    while a > 0 or b > 0 do
        if a % 2 ~= b % 2 then n = n + p end
        a, b, p = math.floor(a / 2), math.floor(b / 2), p * 2
    end
    return n
end
local bit_ok, bit = pcall(require, "bit")
if bit_ok and bit.bxor then xor = function(a, b) return bit.bxor(a, b) % 4294967296 end end
local crc_table
local function crc(s)
    if not crc_table then
        crc_table = {}
        for i = 0, 255 do
            local n = i
            for _ = 1, 8 do
                n = n % 2 == 1 and xor(math.floor(n / 2), 0xedb88320) or math.floor(n / 2)
            end
            crc_table[i] = n
        end
    end
    local n = 0xffffffff
    for i = 1, #s do n = xor(math.floor(n / 256), crc_table[xor(n % 256, s:byte(i))]) end
    return xor(n, 0xffffffff)
end
local function tree(lengths)
    local count, nextcode, nodes, max = {}, {}, {}, 0
    for _, len in ipairs(lengths) do
        assert(len >= 0 and len <= 15, "bad Huffman length")
        if len > 0 then count[len] = (count[len] or 0) + 1; max = math.max(max, len) end
    end
    local code = 0
    for bits = 1, max do
        code = (code + (count[bits - 1] or 0)) * 2
        assert(code + (count[bits] or 0) <= 2 ^ bits, "oversubscribed Huffman tree")
        nextcode[bits] = code
    end
    for symbol, len in ipairs(lengths) do
        if len > 0 then
            nodes[len] = nodes[len] or {}
            nodes[len][nextcode[len]] = symbol - 1
            nextcode[len] = nextcode[len] + 1
        end
    end
    return { nodes = nodes, max = max }
end
local lengthbase = {3,4,5,6,7,8,9,10,11,13,15,17,19,23,27,31,35,43,51,59,67,83,99,115,131,163,195,227,258}
local lengthextra = {0,0,0,0,0,0,0,0,1,1,1,1,2,2,2,2,3,3,3,3,4,4,4,4,5,5,5,5,0}
local distbase = {1,2,3,4,5,7,9,13,17,25,33,49,65,97,129,193,257,385,513,769,1025,1537,2049,3073,4097,6145,8193,12289,16385,24577}
local distextra = {0,0,0,0,1,1,2,2,3,3,4,4,5,5,6,6,7,7,8,8,9,9,10,10,11,11,12,12,13,13}
-- RFC 1951's code-length alphabet order (19 entries).
local order = {16,17,18,0,8,7,9,6,10,5,11,4,12,3,13,2,14,1,15}
local function inflate(s, expected, width)
    assert(#s >= 6, "short zlib")
    local cmf, flg = s:byte(1, 2)
    assert(cmf % 16 == 8 and math.floor(cmf / 16) <= 7 and (cmf * 256 + flg) % 31 == 0
        and math.floor(flg / 32) % 2 == 0, "bad zlib header")
    local pos, bitpos = 3, 0
    local function bits(n)
        local result, power = 0, 1
        for _ = 1, n do
            assert(pos <= #s - 4, "short deflate")
            result = result + math.floor(s:byte(pos) / 2 ^ bitpos) % 2 * power
            power, bitpos = power * 2, bitpos + 1
            if bitpos == 8 then pos, bitpos = pos + 1, 0 end
        end
        return result
    end
    local function symbol(t)
        local code = 0
        for n = 1, t.max do
            code = code * 2 + bits(1)
            local found = t.nodes[n] and t.nodes[n][code]
            if found ~= nil then return found end
        end
        error("bad deflate symbol")
    end
    -- Keep only the 32 KiB deflate window, not a Lua number per pixel.
    local window, total, a, b = {}, 0, 1, 0
    local function emit(byte)
        assert(total < expected, "inflated PNG too large")
        if total % (width + 1) == 0 then assert(byte <= 4, "bad PNG row filter") end
        total = total + 1
        window[(total - 1) % 32768 + 1] = byte
        a = (a + byte) % 65521; b = (b + a) % 65521
    end
    local last = 0
    while last == 0 do
        last = bits(1)
        local kind = bits(2)
        if kind == 0 then
            if bitpos ~= 0 then pos, bitpos = pos + 1, 0 end
            local n, complement = bits(16), bits(16)
            assert(n + complement == 65535, "bad stored block")
            for _ = 1, n do emit(bits(8)) end
        else
            assert(kind == 1 or kind == 2, "reserved deflate block")
            local literals, distances = {}, {}
            if kind == 1 then
                for i = 0, 287 do literals[i + 1] = i < 144 and 8 or (i < 256 and 9 or (i < 280 and 7 or 8)) end
                for i = 1, 32 do distances[i] = 5 end
            else
                local nl, nd, nc = bits(5) + 257, bits(5) + 1, bits(4) + 4
                assert(nl <= 286 and nd <= 32, "bad deflate alphabet")
                local codes = {}
                for i = 1, 19 do codes[i] = 0 end
                for i = 1, nc do codes[order[i] + 1] = bits(3) end
                local ct, lengths = tree(codes), {}
                while #lengths < nl + nd do
                    local sym = symbol(ct)
                    if sym <= 15 then lengths[#lengths + 1] = sym
                    else
                        local len, repeatcount
                        if sym == 16 then assert(#lengths > 0, "missing repeat"); len, repeatcount = lengths[#lengths], bits(2) + 3
                        elseif sym == 17 then len, repeatcount = 0, bits(3) + 3
                        else len, repeatcount = 0, bits(7) + 11 end
                        assert(#lengths + repeatcount <= nl + nd, "excess code lengths")
                        for _ = 1, repeatcount do lengths[#lengths + 1] = len end
                    end
                end
                for i = 1, nl do literals[i] = lengths[i] end
                for i = 1, nd do distances[i] = lengths[nl + i] end
            end
            assert(literals[257] and literals[257] > 0, "missing end symbol")
            local lt, dt = tree(literals), tree(distances)
            while true do
                local sym = symbol(lt)
                if sym < 256 then emit(sym)
                elseif sym == 256 then break
                else
                    assert(sym <= 285, "bad length symbol")
                    local index = sym - 256
                    local length = lengthbase[index] + bits(lengthextra[index])
                    local ds = symbol(dt) + 1
                    assert(ds <= 30, "bad distance symbol")
                    local distance = distbase[ds] + bits(distextra[ds])
                    assert(distance <= total and distance <= 32768, "invalid backreference")
                    for _ = 1, length do emit(window[(total - distance) % 32768 + 1]) end
                end
            end
        end
    end
    if bitpos ~= 0 then pos = pos + 1 end
    assert(pos == #s - 3 and total == expected, "incorrect PNG scanline length")
    assert(b * 65536 + a == u32(s, #s - 3), "bad zlib checksum")
end
function PNG.validate(bytes, width, height)
    local ok = pcall(function()
        assert(type(bytes) == "string" and bytes:sub(1, 8) == "\137PNG\13\10\26\10", "not PNG")
        assert(type(width) == "number" and type(height) == "number" and width >= 1 and height >= 1
            and width % 1 == 0 and height % 1 == 0 and width <= 12000 and height <= 12000
            and width * height <= 25000000, "bad dimensions")
        local pos, data, header, ended, after_data = 9, {}, false, false, false
        while pos <= #bytes do
            local n = u32(bytes, pos)
            assert(n <= #bytes - pos - 11, "short chunk")
            local kind = bytes:sub(pos + 4, pos + 7)
            local content = bytes:sub(pos + 8, pos + 7 + n)
            assert(crc(kind .. content) == u32(bytes, pos + 8 + n), "bad PNG checksum")
            if not header then
                assert(kind == "IHDR" and n == 13 and u32(content, 1) == width and u32(content, 5) == height
                    and content:sub(9) == "\8\0\0\0\0", "not canonical L8 PNG")
                header = true
            elseif kind == "IDAT" then
                assert(not after_data, "noncontiguous pixels")
                data[#data + 1] = content
            elseif kind == "IEND" then
                assert(n == 0 and #data > 0 and pos + 12 == #bytes + 1, "bad PNG end")
                ended = true; break
            else
                assert(kind ~= "IHDR" and kind ~= "acTL" and kind:byte(1) >= 97, "unsupported PNG chunk")
                if #data > 0 then after_data = true end
            end
            pos = pos + n + 12
        end
        assert(header and ended, "unfinished PNG")
        inflate(table.concat(data), (width + 1) * height, width)
    end)
    return ok
end
return PNG

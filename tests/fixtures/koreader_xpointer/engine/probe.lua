-- Headless crengine ground-truth probe (KOReader's own CreDocument, new-book defaults).
-- usage: luajit probe.lua <epub> <mode> [input.json] > out.json
--   mode "texts": input = JSON array of xpointers; prints getTextFromXPointers(xp[i], xp[i+1])
--   mode "words": prints every visible word {s, e, t} in document order
--   mode "check": input = JSON array of {s,e}; prints validity + text for each
require("setupkoenv")
G_defaults = require("luadefaults"):open()
local DataStorage = require("datastorage")
G_reader_settings = require("luasettings"):open(DataStorage:getDataDir().."/settings.reader.lua")
-- A minimal stand-in for the hardware Device: CreDocument only needs a
-- screen geometry (a Kindle Kids-like 1072x1448 at 300 dpi).
local function no() return false end
local screen = { fb_bpp = 8, night_mode = false, sw_dithering = false }
function screen:getWidth() return 1072 end
function screen:getHeight() return 1448 end
function screen:getDPI() return 300 end
function screen:isColorEnabled() return false end
function screen:scaleBySize(x) return x end
local Device = { screen = screen, isAndroid = no, isDesktop = no, isEmulator = no,
  isKindle = no, isPocketBook = no, hasSystemFonts = no, hasEinkScreen = function() return true end,
  canHWDither = no, hasBGRFrameBuffer = no, hasColorScreen = no }
package.loaded["device"] = Device
require("document/canvascontext"):init(Device)
local json = require("json")
local CreDocument = require("document/credocument")

local path, mode, input = arg[1], arg[2], arg[3]
local doc = CreDocument:new{file = path}
doc:requestDomVersion(doc:getLatestDomVersion())
doc:setupDefaultView()
doc:setStyleSheet("./data/epub.css", "")
doc:setEmbeddedStyleSheet(1)
doc:setBlockRenderingFlags(0x7FFFFFFF)
assert(doc:loadDocument(), "load failed")
doc:render()

local function readjson(p)
  local f = assert(io.open(p)); local s = f:read("*a"); f:close(); return json.decode(s)
end

local out = {}
if mode == "texts" then
  local xps = readjson(input)
  for i, xp in ipairs(xps) do
    local nxt = xps[i+1]
    local t = nxt and doc:getTextFromXPointers(xp, nxt) or doc:getTextFromXPointer(xp)
    out[#out+1] = { xp = xp, valid = doc:isXPointerInDocument(xp), text = t and t:sub(1, 400) or json.null }
  end
elseif mode == "words" then
  local xp = doc:getPageXPointer(1)
  local seen = 0
  while xp and seen < 200000 do
    local s = doc:getNextVisibleWordStart(xp)
    if not s or s == xp and seen > 0 then break end
    local e = doc:getNextVisibleWordEnd(s)
    if not e then break end
    out[#out+1] = { s = s, e = e, t = doc:getTextFromXPointers(s, e) }
    if e == xp then break end
    xp = e
    seen = seen + 1
  end
elseif mode == "check" then
  for _, r in ipairs(readjson(input)) do
    local ok = doc:isXPointerInDocument(r.s) and doc:isXPointerInDocument(r.e)
    out[#out+1] = { s = r.s, e = r.e, valid = ok, text = ok and doc:getTextFromXPointers(r.s, r.e) or json.null,
                    norm_s = ok and doc:getNormalizedXPointer(r.s) or json.null }
  end
end
io.stdout:write("@@JSON@@", json.encode(out), "\n")
-- (no close: teardown needs the full UI stack)

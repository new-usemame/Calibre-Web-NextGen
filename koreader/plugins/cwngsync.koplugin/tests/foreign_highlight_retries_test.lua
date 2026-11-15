package.path = "../?.lua;" .. package.path
package.preload.json = function() return { encode = function(v) return tostring(v[1]) .. tostring(v[3]) end } end
package.preload["ffi/sha2"] = function() return { md5 = function(s) return "md5(" .. s .. ")" end } end
package.preload["ui/event"] = function() return { new = function() return {} end } end
package.preload["ui/uimanager"] = function() return { setDirty = function() end } end
package.loaded.kobo_sqlite_provider = {}
local Native = require("koreader_annotations_provider")
local Foreign = require("cwng_foreign_annotations")
local native = { page = "p", text = "Made here" }
local original_id = "koreader-md5(digestp)"
local imported = { cwng_id = original_id, text = "Made elsewhere" }
local ui = { annotation = { annotations = { native, imported } }, bookmark = {}, handleEvent = function() end }
ui.bookmark.removeItemByIndex = function(_, index) table.remove(ui.annotation.annotations, index) end
Native.setContext(ui, "digest")
local forgotten, removed = Native.removeForeign({ original_id }, "device-a")
assert(removed == 1 and #ui.annotation.annotations == 1 and ui.annotation.annotations[1] == native)
assert(forgotten[1] == original_id and native.cwng_native_id and native.cwng_native_id ~= original_id)
local list = Native.readAll()
assert(list[1].annotation_id == native.cwng_native_id, "colliding native highlight gets a persistent identity")
print("PASS native collision keeps local highlight and removes proven imported copy")
local values = {}
local doc = { readSetting = function(_, key) return values[key] end,
    saveSetting = function(_, key, value) values[key] = value end }
local plugin = { settings = { server = "http://192.0.2.10", username = "user-a" }, device_id = "device-a" }
local set = Foreign.remember(doc, plugin, forgotten)
local stale = { list = { { annotation_id = original_id }, { annotation_id = native.cwng_native_id } }, deletions = { original_id, "own-deleted" } }
local clean = Foreign.filterPayload(stale, Foreign.proofs(doc, plugin))
assert(#clean.list == 1 and clean.list[1].annotation_id == native.cwng_native_id)
assert(#clean.deletions == 1 and clean.deletions[1] == "own-deleted")
assert(#Foreign.filterIds({ original_id }, set) == 0, "late callback cannot restore a foreign watermark")
print("PASS stale queue and late watermark filter survive persisted proof")
for _, other in ipairs({
    { settings = plugin.settings, device_id = "device-b" },
    { settings = { server = "http://192.0.2.10", username = "user-b" }, device_id = "device-a" },
    { settings = { server = "http://192.0.2.11", username = "user-a" }, device_id = "device-a" },
}) do
    assert(next(Foreign.proofs(doc, other)) == nil, "proof must stay with its login and physical device")
end
print("PASS proof isolation across accounts, servers and devices")
-- Execute the real offline delivery path, including a callback that returns
-- after ownership proof arrived while its network request was in flight.
package.loaded.device = { model = "Kindle" }
package.loaded.docsettings = { open = function() return doc end }
package.loaded["ui/network/manager"] = { isConnected = function() return true end }
package.loaded.logger = { info = function() end, warn = function() end }
local AutoSync = require("cwng_auto_sync")
local delivery = setmetatable({ settings = plugin.settings, device_id = plugin.device_id }, { __index = AutoSync })
local sent, callback, finished
local client = { push_annotations = function(_, _, _, _, list, deletions, _, _, done)
    sent = { list = list, deletions = deletions }; callback = done
end }
delivery:deliverPending(client, { file = "/books/book.epub", document = "digest", annotations = stale },
    function(ok) finished = ok end)
assert(#sent.list == 1 and #sent.deletions == 1, "real queue delivery strips foreign upserts and tombstones")
callback(true, {})
assert(finished == true)
print("PASS real queued delivery excludes proven foreign ids")
-- A second foreign id was still own when this request left; proof arrives
-- before its success callback saves the watermark.
local later = "foreign-later"
delivery:deliverPending(client, { file = "/books/book.epub", document = "digest",
    annotations = { list = { { annotation_id = later } }, deletions = {} } }, function() end)
assert(sent.list[1].annotation_id == later)
Foreign.remember(doc, plugin, { later })
callback(true, {})
local watermark = values.cwasync_pushed_annotation_ids
assert(watermark and #watermark == 0, "late successful delivery cannot restore the removed foreign id")
print("PASS real late delivery callback cannot revive foreign watermark")
print("foreign_highlight_retries tests passed (5)")

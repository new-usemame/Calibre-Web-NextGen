-- Proven foreign highlights are local housekeeping, never server deletions.
-- Keep proof with the book, scoped to the login and physical device. A late
-- upload callback or a queued snapshot from before cleanup cannot revive it.
local Foreign = {}
local KEY = "cwng_foreign_annotation_ids"

local function namespace(plugin)
    local settings = plugin.settings or {}
    return table.concat({ settings.server or "", settings.username or "", plugin.device_id or "" }, "\0")
end

function Foreign.proofs(doc_settings, plugin)
    local saved = doc_settings and doc_settings.readSetting and doc_settings:readSetting(KEY)
    local ids = type(saved) == "table" and saved[namespace(plugin)] or nil
    local set = {}
    for _, id in ipairs(type(ids) == "table" and ids or {}) do set[id] = true end
    return set
end

function Foreign.filterIds(ids, set)
    local kept = {}
    for _, id in ipairs(ids or {}) do
        if not set[id] then kept[#kept + 1] = id end
    end
    return kept
end

function Foreign.filterPayload(payload, set)
    local list = {}
    for _, item in ipairs(payload.list or {}) do
        if not set[item.annotation_id] then list[#list + 1] = item end
    end
    return { list = list, deletions = Foreign.filterIds(payload.deletions, set) }
end

function Foreign.remember(doc_settings, plugin, ids)
    local all = doc_settings:readSetting(KEY)
    if type(all) ~= "table" then all = {} end
    local set = Foreign.proofs(doc_settings, plugin)
    for _, id in ipairs(ids) do set[id] = true end
    local merged = {}
    for id in pairs(set) do merged[#merged + 1] = id end
    table.sort(merged)
    all[namespace(plugin)] = merged
    doc_settings:saveSetting(KEY, all)
    return set
end

return Foreign

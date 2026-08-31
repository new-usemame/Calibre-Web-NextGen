-- Apply server shelves as KOReader collections without crossing account scopes.

local Collections = {}

local function scopedName(name, scope)
    local label = type(name) == "string" and name ~= "" and name or "Shelf"
    local scope_tag = scope:sub(1, 4) .. scope:sub(-4)
    return string.format("%s [CWNG %s]", label, scope_tag)
end

local function join(root, relative)
    return root:gsub("/+$", "") .. "/" .. relative
end

function Collections.apply(snapshot, root, read_collection, state)
    if type(snapshot) ~= "table" or type(snapshot.scope) ~= "string"
            or snapshot.scope == "" or type(snapshot.revision) ~= "number"
            or type(snapshot.collections) ~= "table" or type(root) ~= "string"
            or type(read_collection) ~= "table" or type(state) ~= "table" then
        return false, "invalid collection snapshot"
    end
    local previous = state[snapshot.scope] or { names = {} }
    local next_names = {}
    local updated = {}
    for _, shelf in ipairs(snapshot.collections) do
        if type(shelf) ~= "table" or type(shelf.id) ~= "string"
                or type(shelf.books) ~= "table" then
            return false, "invalid collection"
        end
        local name = scopedName(shelf.name, snapshot.scope)
        next_names[shelf.id] = name
        -- Rebuild each managed collection so membership removals are applied,
        -- not merely additions. Names are account-scoped, so this cannot touch
        -- another account's or a user's unmanaged KOReader collection.
        if previous.names[shelf.id] then
            read_collection:removeCollection(previous.names[shelf.id])
        end
        read_collection:addCollection(name)
        updated[name] = true
        for _, lpath in ipairs(shelf.books) do
            if type(lpath) == "string" and lpath ~= "" and not lpath:find("..", 1, true)
                    and lpath:sub(1, 1) ~= "/" then
                read_collection:addItem(join(root, lpath), name)
            end
        end
    end
    for shelf_id, old_name in pairs(previous.names) do
        if next_names[shelf_id] == nil then
            read_collection:removeCollection(old_name)
            updated[old_name] = true
        end
    end
    local written, reason = read_collection:write(updated)
    if written == false then return false, reason or "collection write failed" end
    state[snapshot.scope] = { revision = snapshot.revision, names = next_names }
    return true
end

return Collections

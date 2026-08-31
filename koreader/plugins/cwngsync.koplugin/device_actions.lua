-- Exact, device-side execution of a server-named file deletion.

local Actions = {}

local function validRelativePath(path)
    if type(path) ~= "string" or path == "" or path:sub(1, 1) == "/"
            or path:find("\\") or path:find("%z") then
        return false
    end
    for part in path:gmatch("[^/]+") do
        if part == "." or part == ".." then return false end
    end
    return not path:find("//", 1, true) and path:sub(-1) ~= "/"
end

function Actions.deleteNamed(request, root, opts)
    if type(request) ~= "table" or not validRelativePath(request.lpath)
            or type(request.checksum) ~= "string" or request.checksum == ""
            or type(root) ~= "string" or root == "" then
        return false, "invalid named deletion"
    end
    for _, operation in ipairs({ "attributes", "digest", "remove" }) do
        if type(opts and opts[operation]) ~= "function" then
            return false, "missing deletion operation: " .. operation
        end
    end
    local path = root:gsub("/+$", "") .. "/" .. request.lpath
    if opts.attributes(path) == nil then
        return true -- Idempotent retry after deletion but before acknowledgement.
    end
    if opts.digest(path) ~= request.checksum then
        return false, "checksum mismatch"
    end
    local removed, reason = opts.remove(path)
    if removed == false then
        return false, reason or "delete failed"
    end
    if opts.attributes(path) ~= nil then
        return false, "file remains after deletion"
    end
    return true, nil, path
end

return Actions

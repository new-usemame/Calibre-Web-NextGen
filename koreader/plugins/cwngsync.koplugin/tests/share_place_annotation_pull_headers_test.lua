package.path = table.concat({
    "./?.lua",
    "../?.lua",
    package.path,
}, ";")

-- Share my place, PR 5 (spec state/kindle-kids/specs/2026-10-11-share-my-place.md,
-- section 17): a device is served only the highlights it made. The server can
-- tell which device is pulling only by the X-CWNG-Device-ID header (a GET has
-- no body), and without it the pull stays today's full set. So the plugin's
-- annotation pull must send the same identity headers its progress pull sends,
-- while keeping what it already sends.
--
-- Contract: pull_annotations(username, password, document, device, device_id,
-- callback), the argument order get_progress already uses.
--
-- The Spore client is replaced by a recorder that runs the middlewares the
-- client enabled, as lua-Spore does, and keeps the headers each call left.

package.preload["ui/uimanager"] = function()
    return { looper = nil, setInputTimeout = function() end }
end
package.preload["logger"] = function()
    return { warn = function() end, dbg = function() end, info = function() end,
             err = function() end }
end
package.preload["gettext"] = function()
    return function(text) return text end
end
package.preload["socketutil"] = function()
    return { set_timeout = function() end, reset_timeout = function() end }
end

local fake = { enabled = {}, calls = {} }

function fake:reset_middlewares() self.enabled = {} end

function fake:enable(name, args)
    table.insert(self.enabled, { name = name, args = args or {} })
end

local function dispatch(self, method, params)
    local req = { headers = {}, method = method, env = { spore = {} },
                  finalize = function() end }
    for _, middleware in ipairs(self.enabled) do
        local module = package.loaded["Spore.Middleware." .. middleware.name]
        if module and module.call then module.call(middleware.args, req) end
    end
    table.insert(self.calls, { method = method, params = params, headers = req.headers })
    return { status = 200, body = { annotations = {}, book_known = true } }
end

function fake:pull_annotations(params) return dispatch(self, "pull_annotations", params) end

package.preload["Spore"] = function()
    return { new_from_spec = function() return fake end }
end

local CWNGSyncClient = require("CWNGSyncClient")

local function assertEqual(actual, expected, message)
    if actual ~= expected then
        error(string.format("%s\nexpected: %s\nactual: %s", message,
            tostring(expected), tostring(actual)), 2)
    end
end

local client = CWNGSyncClient:new({ service_spec = {}, service_url = "https://cwng.invalid" })

local function testAnnotationPullSendsTheDeviceHeaders()
    fake.calls = {}
    local outcome, body
    client:pull_annotations("reader", "secret", "0123abcd", "Kindle A", "raw-device-1",
        function(ok, response) outcome, body = ok, response end)
    local call = fake.calls[#fake.calls]
    assert(call ~= nil, "the client made no request")
    assertEqual(call.method, "pull_annotations", "the pull goes out as pull_annotations")
    assertEqual(call.headers["X-CWNG-Device-ID"], "raw-device-1",
        "the annotation pull names this device by its raw id")
    assertEqual(call.headers["X-CWNG-Device-Name"], "Kindle A",
        "the annotation pull names this device's name")
    assertEqual(call.params.document, "0123abcd", "the document is still sent")
    assert(call.headers["Authorization"] ~= nil, "the pull is still authenticated")
    assertEqual(outcome, true, "the pull still reports its outcome")
    assertEqual(type(body), "table", "the pull still hands back the server's body")
end

-- And main.lua's highlight sync passes this device's model and raw id to it,
-- the same pair its progress calls pass. syncAnnotations is lifted out of
-- main.lua (as same_device_pull_test.lua lifts getProgress) and run on stubs.
local function loadSyncAnnotations(env)
    local main_path = assert(package.searchpath("main", package.path),
        "cannot locate main.lua via package.path")
    local f = assert(io.open(main_path, "r"), "cannot read " .. main_path)
    local source = f:read("*a")
    f:close()
    local header = "function CWNGSync:syncAnnotations(interactive)"
    local start = source:find(header, 1, true)
    assert(start, "syncAnnotations not found in main.lua")
    local stop = source:find("\nend\n", start, true)
    assert(stop, "syncAnnotations is unterminated")
    local chunk = "local CWNGSync = {}\n" .. source:sub(start, stop + 4) .. "\nreturn CWNGSync\n"
    local loaded, err = load(chunk, "syncAnnotations", "t", env)
    assert(loaded, "syncAnnotations failed to parse: " .. tostring(err))
    return loaded()
end

local function testHighlightSyncPullsAsThisDevice()
    local pulled
    local Client = {
        pull_annotations = function(_, ...) pulled = table.pack(...) end,
    }
    Client.new = function(_, o) return setmetatable(o or {}, { __index = Client }) end
    local env = setmetatable({
        require = function(name)
            if name == "CWNGSyncClient" then return Client end
            if name == "device_annotations" then
                return { getProvider = function() return {} end }
            end
            return require(name)
        end,
        Device = { model = "Kindle A" },
        ensureServerConfigured = function() return true end,
    }, { __index = _G })
    local CWNGSync = loadSyncAnnotations(env)
    local plugin = setmetatable({
        settings = { sync_annotations = true, username = "reader",
                     password = "secret", server = "https://cwng.invalid" },
        device_id = "raw-device-1",
        path = ".",
        hasCurrentDocument = function() return true end,
        getDocumentDigest = function() return "0123abcd" end,
    }, { __index = CWNGSync })

    plugin:syncAnnotations(false)

    assert(pulled ~= nil, "highlight sync did not pull")
    assertEqual(pulled[3], "0123abcd", "the pull names the open document")
    assertEqual(pulled[4], "Kindle A", "the pull passes this device's model")
    assertEqual(pulled[5], "raw-device-1", "the pull passes this device's raw id")
    assertEqual(type(pulled[6]), "function", "the callback comes last")
end

testAnnotationPullSendsTheDeviceHeaders()
testHighlightSyncPullsAsThisDevice()

print("share-place annotation pull header tests passed")

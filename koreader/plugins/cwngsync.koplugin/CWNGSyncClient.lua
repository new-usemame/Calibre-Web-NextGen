local UIManager = require("ui/uimanager")
local logger = require("logger")
local socketutil = require("socketutil")

-- Push/Pull
local PROGRESS_TIMEOUTS = { 2,  5 }
-- Authentication
local AUTH_TIMEOUTS     = { 5, 10 }
-- Annotations: payloads can be larger than a progress ping, so allow longer.
local ANNOTATION_TIMEOUTS = { 5, 15 }
-- A full library observation hashes local files before this call; the wire
-- payload itself can also be larger than progress or annotation traffic.
local INVENTORY_TIMEOUTS = { 10, 30 }

local CWNGSyncClient = {
    service_spec = nil,
    service_url = nil,
}

-- Turn whatever a failed call produced into one line a user can read off the
-- screen or find in crash.log.
--
-- Two shapes arrive here. A call that reached the server returns a response, so
-- the useful fact is its status. A call that never got that far raises, and
-- `pcall` hands back the error itself -- a string from the transport, or a table
-- from lua-Spore.
--
-- This exists because both used to be thrown away. The failure was logged with
-- `logger.dbg`, which KOReader suppresses unless the user has switched on debug
-- logging, and the callback was handed `res.body` -- always nil on a raise,
-- since the error is not a response. A push that never reached the server
-- therefore left no trace on the device and none in the server log either, and
-- "Server push failed" was the whole of what anyone could report. #920 survived
-- three wrong diagnoses that way; the server's half of the same gap was closed
-- in #1101, and this is the device's half.
local function describeFailure(err)
    if type(err) == "table" then
        local detail = err.message or err.error or err.reason
        if detail then return tostring(detail) end
        if err.status then return "HTTP " .. tostring(err.status) end
    elseif type(err) == "string" and err ~= "" then
        return err
    end
    return "no response from server"
end

CWNGSyncClient.describeFailure = describeFailure


-- Report a completed call. `reason` is nil when it succeeded, and otherwise
-- names why, so callers never have to guess from a bare boolean.
local function finish(callback, ok, res, label)
    if ok then
        local succeeded = res.status == 200
        -- Not `succeeded and nil or ...`: that idiom cannot yield nil, so a
        -- success would carry the reason "HTTP 200" and callers branching on
        -- `reason` would treat every sync as failed.
        local reason
        if not succeeded then reason = "HTTP " .. tostring(res.status) end
        callback(succeeded, res.body, reason)
    else
        -- The projection, not the raw error. Raising this from dbg to warn puts
        -- it in crash.log, which is the file users paste into issue threads, so
        -- what goes in is kept to the message/status rather than whatever object
        -- the transport happened to raise.
        logger.warn(label .. " failure:", describeFailure(res))
        callback(false, nil, describeFailure(res))
    end
end

-- Exposed for the offline tests: every sync call funnels its outcome through
-- here, and the contract (a reason whenever ok is false) is what stops a
-- failure going unreported again.
CWNGSyncClient._reportOutcome = finish

function CWNGSyncClient:new(o)
    if o == nil then o = {} end
    setmetatable(o, self)
    self.__index = self
    if o.init then o:init() end
    return o
end

function CWNGSyncClient:init()
    local Spore = require("Spore")
    self.client = Spore.new_from_spec(self.service_spec, {
        base_url = self.service_url,
    })
    package.loaded["Spore.Middleware.GinClient"] = {}
    require("Spore.Middleware.GinClient").call = function(_, req)
        req.headers["accept"] = "application/vnd.koreader.v1+json"
    end
    package.loaded["Spore.Middleware.CWNGSyncAuth"] = {}
    require("Spore.Middleware.CWNGSyncAuth").call = function(args, req)
        -- Use HTTP Basic Authentication
        local credentials = args.username .. ":" .. args.password
        -- Base64 encode the credentials (compatible implementation)
        local base64_chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
        local function base64_encode(data)
            local encoded = ""
            local i = 1
            while i <= #data do
                local a = string.byte(data, i) or 0
                local b = string.byte(data, i + 1) or 0
                local c = string.byte(data, i + 2) or 0

                -- Use bit operations compatible with Lua 5.1+
                local bitmap = a * 65536 + b * 256 + c

                -- Extract 6-bit chunks
                local c1 = math.floor(bitmap / 262144) % 64  -- bits 18-23
                local c2 = math.floor(bitmap / 4096) % 64    -- bits 12-17
                local c3 = math.floor(bitmap / 64) % 64      -- bits 6-11
                local c4 = bitmap % 64                       -- bits 0-5

                encoded = encoded .. string.sub(base64_chars, c1 + 1, c1 + 1)
                encoded = encoded .. string.sub(base64_chars, c2 + 1, c2 + 1)

                if i + 1 <= #data then
                    encoded = encoded .. string.sub(base64_chars, c3 + 1, c3 + 1)
                else
                    encoded = encoded .. "="
                end

                if i + 2 <= #data then
                    encoded = encoded .. string.sub(base64_chars, c4 + 1, c4 + 1)
                else
                    encoded = encoded .. "="
                end

                i = i + 3
            end
            return encoded
        end

        local base64_credentials = base64_encode(credentials)
        req.headers["Authorization"] = "Basic " .. base64_credentials
    end
    package.loaded["Spore.Middleware.AsyncHTTP"] = {}
    require("Spore.Middleware.AsyncHTTP").call = function(args, req)
        -- disable async http if Turbo looper is missing
        if not UIManager.looper then return end
        req:finalize()
        local result
        require("httpclient"):new():request({
            url = req.url,
            method = req.method,
            body = req.env.spore.payload,
            on_headers = function(headers)
                for header, value in pairs(req.headers) do
                    if type(header) == "string" then
                        headers:add(header, value)
                    end
                end
            end,
        }, function(res)
            result = res
            -- Turbo HTTP client uses code instead of status
            -- change to status so that Spore can understand
            result.status = res.code
            coroutine.resume(args.thread)
        end)
        return coroutine.create(function() coroutine.yield(result) end)
    end
end

function CWNGSyncClient:authorize(username, password)
    self.client:reset_middlewares()
    self.client:enable("Format.JSON")
    self.client:enable("GinClient")
    self.client:enable("CWNGSyncAuth", {
        username = username,
        password = password,
    })
    socketutil:set_timeout(AUTH_TIMEOUTS[1], AUTH_TIMEOUTS[2])
    local ok, res = pcall(function()
        return self.client:authorize()
    end)
    socketutil:reset_timeout()
    if ok then
        return res.status == 200, res.body
    else
        -- Same reasoning as `finish`, and it matters most here: this is the one
        -- call that is handed a password.
        logger.warn("CWNGSyncClient:authorize failure:", describeFailure(res))
        return false, nil, describeFailure(res)
    end
end

function CWNGSyncClient:update_progress(
        username,
        password,
        document,
        progress,
        percentage,
        device,
        device_id,
        callback)
    self.client:reset_middlewares()
    self.client:enable("Format.JSON")
    self.client:enable("GinClient")
    self.client:enable("CWNGSyncAuth", {
        username = username,
        password = password,
    })
    -- Set *very* tight timeouts to avoid blocking for too long...
    socketutil:set_timeout(PROGRESS_TIMEOUTS[1], PROGRESS_TIMEOUTS[2])
    local co = coroutine.create(function()
        local ok, res = pcall(function()
            return self.client:update_progress({
                document = document,
                progress = tostring(progress),
                percentage = percentage,
                device = device,
                device_id = device_id,
            })
        end)
        finish(callback, ok, res, "CWNGSyncClient:update_progress")
    end)
    self.client:enable("AsyncHTTP", {thread = co})
    coroutine.resume(co)
    if UIManager.looper then UIManager:setInputTimeout() end
    socketutil:reset_timeout()
end

function CWNGSyncClient:get_progress(
        username,
        password,
        document,
        callback)
    self.client:reset_middlewares()
    self.client:enable("Format.JSON")
    self.client:enable("GinClient")
    self.client:enable("CWNGSyncAuth", {
        username = username,
        password = password,
    })
    socketutil:set_timeout(PROGRESS_TIMEOUTS[1], PROGRESS_TIMEOUTS[2])
    local co = coroutine.create(function()
        local ok, res = pcall(function()
            return self.client:get_progress({
                document = document,
                -- Tell the server this build can act on a percentage with no
                -- locator (#1366). Servers that predate it ignore the
                -- parameter; servers that understand it withhold those rows
                -- from clients that stay silent, so sending it is what opts
                -- this device into web-reader and Kobo positions.
                position_kinds = "locator,percentage",
            })
        end)
        finish(callback, ok, res, "CWNGSyncClient:get_progress")
    end)
    self.client:enable("AsyncHTTP", {thread = co})
    coroutine.resume(co)
    if UIManager.looper then UIManager:setInputTimeout() end
    socketutil:reset_timeout()
end

function CWNGSyncClient:report_inventory(
        username, password, device, device_id, inventory, free_space, total_space, callback)
    self.client:reset_middlewares()
    self.client:enable("Format.JSON")
    self.client:enable("GinClient")
    self.client:enable("CWNGSyncAuth", {
        username = username,
        password = password,
    })
    socketutil:set_timeout(INVENTORY_TIMEOUTS[1], INVENTORY_TIMEOUTS[2])
    local co = coroutine.create(function()
        local ok, res = pcall(function()
            return self.client:report_inventory({
                device = device,
                device_id = device_id,
                inventory = inventory,
                free_space = free_space,
                total_space = total_space,
            })
        end)
        finish(callback, ok, res, "CWNGSyncClient:report_inventory")
    end)
    self.client:enable("AsyncHTTP", {thread = co})
    coroutine.resume(co)
    if UIManager.looper then UIManager:setInputTimeout() end
    socketutil:reset_timeout()
end

function CWNGSyncClient:claim_delivery(
        username, password, device, device_id, free_space, total_space, callback)
    self.client:reset_middlewares()
    self.client:enable("Format.JSON")
    self.client:enable("GinClient")
    self.client:enable("CWNGSyncAuth", {
        username = username,
        password = password,
    })
    socketutil:set_timeout(INVENTORY_TIMEOUTS[1], INVENTORY_TIMEOUTS[2])
    local co = coroutine.create(function()
        local ok, res = pcall(function()
            return self.client:claim_delivery({
                device = device,
                device_id = device_id,
                free_space = free_space,
                total_space = total_space,
            })
        end)
        finish(callback, ok, res, "CWNGSyncClient:claim_delivery")
    end)
    self.client:enable("AsyncHTTP", {thread = co})
    coroutine.resume(co)
    if UIManager.looper then UIManager:setInputTimeout() end
    socketutil:reset_timeout()
end

local function jsonRequest(self, operation, username, password, callback, label)
    self.client:reset_middlewares()
    self.client:enable("Format.JSON")
    self.client:enable("GinClient")
    self.client:enable("CWNGSyncAuth", { username = username, password = password })
    socketutil:set_timeout(INVENTORY_TIMEOUTS[1], INVENTORY_TIMEOUTS[2])
    local co = coroutine.create(function()
        local ok, res = pcall(operation)
        finish(callback, ok, res, label)
    end)
    self.client:enable("AsyncHTTP", { thread = co })
    coroutine.resume(co)
    if UIManager.looper then UIManager:setInputTimeout() end
    socketutil:reset_timeout()
end

function CWNGSyncClient:refuse_delivery(
        username, password, device, device_id, delivery_id, claim_token, reason,
        free_space, total_space, callback)
    jsonRequest(self, function()
        return self.client:refuse_delivery({
            device = device,
            device_id = device_id,
            delivery_id = delivery_id,
            claim_token = claim_token,
            reason = reason,
            free_space = free_space,
            total_space = total_space,
        })
    end, username, password, callback, "CWNGSyncClient:refuse_delivery")
end

function CWNGSyncClient:claim_deletion(username, password, device, device_id, callback)
    jsonRequest(self, function()
        return self.client:claim_deletion({
            device = device,
            device_id = device_id,
        })
    end, username, password, callback, "CWNGSyncClient:claim_deletion")
end

function CWNGSyncClient:complete_deletion(
        username, password, device, device_id, deletion_id, claim_token,
        deleted, failure_reason, callback)
    jsonRequest(self, function()
        return self.client:complete_deletion({
            device = device,
            device_id = device_id,
            deletion_id = deletion_id,
            claim_token = claim_token,
            deleted = deleted,
            failure_reason = failure_reason,
        })
    end, username, password, callback, "CWNGSyncClient:complete_deletion")
end

function CWNGSyncClient:get_collections(username, password, device, device_id, callback)
    jsonRequest(self, function()
        return self.client:get_collections({
            device = device,
            device_id = device_id,
        })
    end, username, password, callback, "CWNGSyncClient:get_collections")
end

function CWNGSyncClient:complete_collections(
        username, password, device, device_id, revision, callback)
    jsonRequest(self, function()
        return self.client:complete_collections({
            device = device,
            device_id = device_id,
            revision = revision,
        })
    end, username, password, callback, "CWNGSyncClient:complete_collections")
end

function CWNGSyncClient:complete_delivery(
        username, password, device, device_id, delivery_id, claim_token,
        lpath, checksum, size, mtime, callback)
    self.client:reset_middlewares()
    self.client:enable("Format.JSON")
    self.client:enable("GinClient")
    self.client:enable("CWNGSyncAuth", {
        username = username,
        password = password,
    })
    socketutil:set_timeout(INVENTORY_TIMEOUTS[1], INVENTORY_TIMEOUTS[2])
    local co = coroutine.create(function()
        local ok, res = pcall(function()
            return self.client:complete_delivery({
                device = device,
                device_id = device_id,
                delivery_id = delivery_id,
                claim_token = claim_token,
                lpath = lpath,
                checksum = checksum,
                size = size,
                mtime = mtime,
            })
        end)
        finish(callback, ok, res, "CWNGSyncClient:complete_delivery")
    end)
    self.client:enable("AsyncHTTP", {thread = co})
    coroutine.resume(co)
    if UIManager.looper then UIManager:setInputTimeout() end
    socketutil:reset_timeout()
end

local function responseHeader(headers, wanted)
    if type(headers) ~= "table" then return nil end
    wanted = wanted:lower()
    for name, value in pairs(headers) do
        if type(name) == "string" and name:lower() == wanted then
            return value
        end
    end
end

-- Book bytes intentionally use LuaSocket's file sink instead of Spore's JSON
-- middleware: the latter buffers the complete body in memory. Claim and
-- completion still travel through Spore and its double-declared wire contract.
function CWNGSyncClient:download_delivery(
        username, password, device, device_id, delivery, local_path)
    -- Load the byte-stream stack only on the delivery path. KOReader ships
    -- these modules; the plugin's pure host-Lua contract tests deliberately
    -- stub only the JSON/Spore path and must remain runnable without LuaSocket.
    local http = require("socket.http")
    local ltn12 = require("ltn12")
    local mime = require("mime")
    local socket = require("socket")
    if type(delivery) ~= "table" or type(delivery.download_path) ~= "string"
            or type(delivery.claim_token) ~= "string" then
        return false, nil, nil, "invalid delivery response"
    end
    local handle, open_error = io.open(local_path, "wb")
    if not handle then
        return false, nil, nil, open_error or "could not open temporary file"
    end

    socketutil:set_timeout(socketutil.FILE_BLOCK_TIMEOUT, socketutil.FILE_TOTAL_TIMEOUT)
    local credentials = mime.b64(username .. ":" .. password):gsub("%s", "")
    local ok, code, headers, status = pcall(function()
        return socket.skip(1, http.request {
            url = self.service_url .. delivery.download_path,
            method = "GET",
            headers = {
                ["Accept-Encoding"] = "identity",
                ["Authorization"] = "Basic " .. credentials,
                ["X-CWNG-Device-ID"] = device_id,
                ["X-CWNG-Device-Name"] = device,
                ["X-CWNG-Claim-Token"] = delivery.claim_token,
            },
            sink = ltn12.sink.file(handle),
        })
    end)
    -- LuaSocket normally closes this through the sink's end-of-stream call.
    -- A transport exception can bypass that callback, so close defensively.
    pcall(handle.close, handle)
    socketutil:reset_timeout()

    if not ok then
        os.remove(local_path)
        return false, nil, nil, describeFailure(code)
    end
    if code ~= 200 then
        os.remove(local_path)
        return false, nil, nil, status or ("HTTP " .. tostring(code))
    end
    return true,
        tonumber(responseHeader(headers, "content-length")),
        responseHeader(headers, "x-cwng-checksum"),
        nil
end

-- Phase 2: pull annotations for a document (server -> device).
function CWNGSyncClient:pull_annotations(username, password, document, callback)
    self.client:reset_middlewares()
    self.client:enable("Format.JSON")
    self.client:enable("GinClient")
    self.client:enable("CWNGSyncAuth", {
        username = username,
        password = password,
    })
    socketutil:set_timeout(ANNOTATION_TIMEOUTS[1], ANNOTATION_TIMEOUTS[2])
    local co = coroutine.create(function()
        local ok, res = pcall(function()
            return self.client:pull_annotations({
                document = document,
            })
        end)
        finish(callback, ok, res, "CWNGSyncClient:pull_annotations")
    end)
    self.client:enable("AsyncHTTP", {thread = co})
    coroutine.resume(co)
    if UIManager.looper then UIManager:setInputTimeout() end
    socketutil:reset_timeout()
end

-- Phase 2: push annotations for a document (device -> server).
--
-- ``complete`` marks this as the device's whole live set for the document, which
-- is what lets the server observe deletions: KOReader keeps no tombstone when a
-- highlight is removed, so a delete reaches us only as an omission (#905). Only
-- pass it when the full set was actually read — the server reaps what's absent.
-- `deleted` is the list of annotation_ids this device used to have and the user
-- has since removed. The server never infers a deletion from an omission (#920),
-- so anything not named here is left alone.
--
-- Every key sent must be listed TWICE in api.json for push_annotations, in two
-- lists that Spore treats as unrelated:
--   * `payload` — Spore rebuilds the request body from exactly that list and
--     silently drops everything else, which is how #906's `complete` flag was
--     thrown away before it ever reached the wire.
--   * `required_params` or `optional_params` — Spore's validate() runs first and
--     raises "<key> is not expected for method push_annotations" on any name it
--     was not told to expect. #924 declared these two in `payload` alone, so
--     every delete cycle died inside the plugin and no request went out (#920).
-- Declaring one and not the other is not caught by review or by a server-side
-- HTTP test; tests/unit/test_cwngsync_plugin_wire_contract.py pins both.
function CWNGSyncClient:push_annotations(username, password, document, annotations, deleted, callback)
    self.client:reset_middlewares()
    self.client:enable("Format.JSON")
    self.client:enable("GinClient")
    self.client:enable("CWNGSyncAuth", {
        username = username,
        password = password,
    })
    socketutil:set_timeout(ANNOTATION_TIMEOUTS[1], ANNOTATION_TIMEOUTS[2])
    local co = coroutine.create(function()
        local ok, res = pcall(function()
            return self.client:push_annotations({
                document = document,
                annotations = annotations,
                deleted = (deleted and #deleted > 0) and deleted or nil,
                delete_source = (deleted and #deleted > 0) and "koreader" or nil,
            })
        end)
        finish(callback, ok, res, "CWNGSyncClient:push_annotations")
    end)
    self.client:enable("AsyncHTTP", {thread = co})
    coroutine.resume(co)
    if UIManager.looper then UIManager:setInputTimeout() end
    socketutil:reset_timeout()
end

return CWNGSyncClient

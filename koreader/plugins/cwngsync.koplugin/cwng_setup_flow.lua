--[[
Connecting this device to a CWNG server: the screens and calls around
setup.lua's decisions. Mixed into the CWNGSync plugin class by main.lua.
]]

local ButtonDialog = require("ui/widget/buttondialog")
local Device = require("device")
local InfoMessage = require("ui/widget/infomessage")
local InputDialog = require("ui/widget/inputdialog")
local Json = require("json")
local NetworkMgr = require("ui/network/manager")
local PairingScreen = require("cwng_pairing_screen")
local Setup = require("cwng_setup")
local UIManager = require("ui/uimanager")
local lfs = require("libs/libkoreader-lfs")
local logger = require("logger")
local util = require("util")
local T = require("ffi/util").template
local _ = require("gettext")

local SetupFlow = {}

-- Per KOReader session: the welcome is offered once, one pairing at a time.
local session = { welcomed = false, pairing = nil }

local function hostOf(server)
    return (server or ""):match("^https?://([^/]+)") or server
end

local function newClient(plugin, server)
    local CWNGSyncClient = require("CWNGSyncClient")
    return CWNGSyncClient:new{
        service_url = server .. "/kosync",
        service_spec = plugin.path .. "/api.json",
    }
end

-- Make the library the home screen, once. Later changes the reader makes to
-- any of these settings are theirs to keep.
function SetupFlow:applyReaderDefaults()
    local root = self:getLibraryRoot()
    if root and not util.directoryExists(root) then util.makePath(root) end
    if not G_reader_settings:isTrue("cwngsync_reader_defaults_applied") and root then
        for key, value in pairs(Setup.readerDefaults(root)) do
            G_reader_settings:saveSetting(key, value)
        end
        local coverbrowser = self.ui and self.ui.coverbrowser
        if coverbrowser and coverbrowser.setDisplayMode then
            pcall(coverbrowser.setDisplayMode, coverbrowser, "mosaic_image")
        end
        local ok, BookInfoManager = pcall(require, "bookinfomanager")
        if ok and BookInfoManager and BookInfoManager.saveSetting then
            if not coverbrowser then
                pcall(BookInfoManager.saveSetting, BookInfoManager, "filemanager_display_mode", "mosaic_image")
            end
            pcall(BookInfoManager.saveSetting, BookInfoManager, "show_progress_in_mosaic", true)
        end
        G_reader_settings:makeTrue("cwngsync_reader_defaults_applied")
    end
    pcall(G_reader_settings.flush, G_reader_settings)
    self:showLibrary()
end

function SetupFlow:showLibrary()
    local root = self:getLibraryRoot()
    local chooser = self.ui and self.ui.file_chooser
    if root and chooser and chooser.changeToPath and util.directoryExists(root) then
        chooser:changeToPath(root)
    end
    self:showHome()
end

-- Verify credentials with the server, then keep them and turn everything on.
-- on_done(ok, why) with why = "rejected" (the server said no) or
-- "unreachable" (no answer).
function SetupFlow:connectWith(config, how, on_done)
    local client = newClient(self, config.server)
    Device:setIgnoreInput(true)
    local called, authorized, _body, reason = pcall(client.authorize, client, config.username, config.password)
    Device:setIgnoreInput(false)
    if not called or not authorized then
        local why = (called and reason == nil) and "rejected" or "unreachable"
        logger.warn("CWNGSync: connection check failed", how, why, called and reason or authorized)
        if on_done then on_done(false, why) end
        return
    end
    self.settings.server = config.server
    self.settings.username = config.username
    self.settings.password = config.password
    self.settings.library_enabled = true
    self.settings.welcome_dismissed = nil
    Setup.applyPluginDefaults(self.settings)
    G_reader_settings:saveSetting(self.settings_key, self.settings)
    self:registerEvents()
    self:applyReaderDefaults()
    UIManager:show(InfoMessage:new{
        text = T(_("Connected to %1.\n\nYour library is being added now. Covers appear as they arrive; tap any book to read it."),
            hostOf(config.server)),
        timeout = 6,
    })
    logger.info("CWNGSync: connected via", how, "to", hostOf(config.server))
    self:syncLibrary({
        force = true,
        on_done = function()
            self:syncDeviceCapabilities(false, false)
            self:collectDeliveries(false, false)
        end,
    })
    if on_done then on_done(true) end
end

-- A plugin downloaded ready-made from the website carries setup.json. It is a
-- secret, so it is deleted as soon as it has been used or found unusable.
function SetupFlow:importSetupBundle()
    local path = self.path .. "/" .. Setup.BUNDLE_FILE
    if lfs.attributes(path, "mode") ~= "file" then return false end
    if self:isConfigured() then
        os.remove(path)
        return false
    end
    local handle = io.open(path, "rb")
    local text = handle and handle:read("*a")
    if handle then handle:close() end
    local config, reason = Setup.parseBundle(text, Json.decode)
    if not config then
        os.remove(path)
        logger.warn("CWNGSync: setup file unusable", reason)
        UIManager:show(InfoMessage:new{
            text = _("The setup file inside the CWNG plugin could not be used. Connect with a code instead: Tools ▸ CWNG library ▸ Connect this device."),
        })
        return false
    end
    if not NetworkMgr:isConnected() then
        -- Ask for Wi-Fi once; if that is declined, the file stays and the
        -- next connection (onNetworkConnected) finishes the job.
        session.bundle_pending = true
        if not session.bundle_asked then
            session.bundle_asked = true
            NetworkMgr:runWhenConnected(function() self:importSetupBundle() end)
        end
        return true
    end
    session.bundle_pending = false
    self:connectWith(config, "ready-made plugin", function(ok, why)
        if ok then
            os.remove(path)
        elseif why == "rejected" then
            os.remove(path)
            UIManager:show(InfoMessage:new{
                text = _("This ready-made plugin's sign-in was switched off on the website. Download a new one, or connect with a code: Tools ▸ CWNG library ▸ Connect this device."),
            })
        else
            session.bundle_pending = true
        end
    end)
    return true
end

function SetupFlow:bundlePending()
    return session.bundle_pending == true
end

-- First start without a connection: offer the ways in, once per session.
function SetupFlow:maybeWelcome()
    if session.welcomed or self:isConfigured() or self.settings.welcome_dismissed then return end
    if self.ui and self.ui.document then return end
    session.welcomed = true
    self:showConnectChoices()
end

function SetupFlow:showConnectChoices()
    local dialog
    dialog = ButtonDialog:new{
        title = _("Read your CWNG library on this device.\n\nChoose how to connect:"),
        title_align = "center",
        buttons = {
            {{
                text = _("Connect with a code (easiest)"),
                callback = function()
                    UIManager:close(dialog)
                    self:startPairing()
                end,
            }},
            {{
                text = _("Sign in with username and password"),
                callback = function()
                    UIManager:close(dialog)
                    self:askServer(function(server)
                        self:login(nil, function(username, password)
                            self:connectWith({ server = server, username = username, password = password },
                                "sign-in", function(ok, why)
                                    if ok then return end
                                    UIManager:show(InfoMessage:new{
                                        text = why == "rejected"
                                            and _("That username or password was not accepted. Try again from Tools ▸ CWNG library ▸ Connect this device.")
                                            or T(_("Could not reach %1. Check the address and that this device is on Wi-Fi."), hostOf(server)),
                                    })
                                end)
                        end)
                    end)
                end,
            }},
            {{
                text = _("Not now"),
                callback = function() UIManager:close(dialog) end,
            }},
            {{
                text = _("Don't ask again"),
                callback = function()
                    UIManager:close(dialog)
                    self.settings.welcome_dismissed = true
                end,
            }},
        },
    }
    UIManager:show(dialog)
end

-- Ask for the server address unless it is already known, then continue.
function SetupFlow:askServer(continue)
    if self.settings.server and self.settings.server ~= "" then
        continue(self.settings.server)
        return
    end
    local dialog
    dialog = InputDialog:new{
        title = _("Your CWNG address"),
        description = _("The address you open CWNG at in a browser, for example books.example.com or 192.168.1.20:8083."),
        input = "",
        input_hint = "books.example.com",
        buttons = {{
            {
                text = _("Cancel"),
                id = "close",
                callback = function() UIManager:close(dialog) end,
            },
            {
                text = _("Continue"),
                is_enter_default = true,
                callback = function()
                    local server = Setup.normalizeServer(dialog:getInputText())
                    if not server then
                        UIManager:show(InfoMessage:new{
                            text = _("That doesn't look like a web address. Type it the way you would in a browser."),
                            timeout = 4,
                        })
                        return
                    end
                    UIManager:close(dialog)
                    self.settings.server = server
                    continue(server)
                end,
            },
        }},
    }
    UIManager:show(dialog)
    dialog:onShowKeyboard()
end

local function stopPairing(pairing)
    if not pairing or pairing.stopped then return end
    pairing.stopped = true
    if pairing.poll_task then UIManager:unschedule(pairing.poll_task) end
    if pairing.screen then UIManager:close(pairing.screen) end
    if session.pairing == pairing then session.pairing = nil end
end

function SetupFlow:startPairing()
    if session.pairing then return end
    NetworkMgr:runWhenConnected(function()
        self:askServer(function(server) self:requestPairing(server) end)
    end)
end

function SetupFlow:requestPairing(server)
    local client = newClient(self, server)
    local pairing = { server = server }
    session.pairing = pairing
    client:pair_start(Device.model, self.device_id, function(ok, body, reason)
        if pairing.stopped then return end
        if not ok or type(body) ~= "table" or type(body.device_code) ~= "string"
                or type(body.user_code) ~= "string" then
            stopPairing(pairing)
            local text
            if reason == "HTTP 409" then
                text = T(_("%1 cannot approve devices by code yet. Choose Sign in with username and password instead."),
                    hostOf(server))
            else
                text = T(_("Could not start pairing with %1: %2\n\nCheck the address, and that KOReader sync is switched on in CWNG's settings."),
                    hostOf(server), reason or _("no response from server"))
            end
            UIManager:show(InfoMessage:new{ text = text })
            return
        end
        local expires_at = os.time() + (tonumber(body.expires_in) or 600)
        local interval = math.max(3, tonumber(body.interval) or 5)
        local link = type(body.verify_url_complete) == "string" and body.verify_url_complete:match("^https?://")
            and body.verify_url_complete or Setup.verifyLink(body.verify_url, server, body.user_code)
        local address = type(body.verify_url) == "string" and body.verify_url:match("^https?://(.+)$")
            or (hostOf(server) .. "/pair")
        pairing.screen = PairingScreen:new{
            link = link,
            code = body.user_code,
            address = address,
            on_cancel = function() stopPairing(pairing) end,
        }
        UIManager:show(pairing.screen)

        local errors = 0
        pairing.poll_task = function()
            if pairing.stopped then return end
            if os.time() > expires_at then
                stopPairing(pairing)
                UIManager:show(InfoMessage:new{ text = _("The code expired. Choose Connect this device to get a new one.") })
                return
            end
            client:pair_poll(body.device_code, function(poll_ok, poll_body, poll_reason)
                if pairing.stopped then return end
                local outcome = Setup.pairingOutcome(poll_ok, poll_body, poll_reason, server)
                if outcome.state == "approved" then
                    stopPairing(pairing)
                    self:connectWith(outcome.credentials, "pairing", function(connected)
                        if not connected then
                            UIManager:show(InfoMessage:new{
                                text = _("Approved, but signing in failed. Try Connect this device again."),
                            })
                        end
                    end)
                elseif outcome.state == "denied" then
                    stopPairing(pairing)
                    UIManager:show(InfoMessage:new{ text = _("The request was declined on the website.") })
                elseif outcome.state == "expired" then
                    stopPairing(pairing)
                    UIManager:show(InfoMessage:new{ text = _("The code expired. Choose Connect this device to get a new one.") })
                else
                    if outcome.state == "error" then errors = errors + 1 end
                    if errors >= 12 then
                        stopPairing(pairing)
                        UIManager:show(InfoMessage:new{
                            text = T(_("Lost contact with %1 while waiting for approval."), hostOf(server)),
                        })
                        return
                    end
                    UIManager:scheduleIn(interval, pairing.poll_task)
                end
            end)
        end
        UIManager:scheduleIn(interval, pairing.poll_task)
    end)
end

-- Forget this device's connection. The library folder and every downloaded
-- book stay; only the sign-in goes.
function SetupFlow:disconnect()
    self.settings.password = nil
    self.settings.username = nil
    G_reader_settings:saveSetting(self.settings_key, self.settings)
    pcall(G_reader_settings.flush, G_reader_settings)
    self:registerEvents()
    UIManager:show(InfoMessage:new{
        text = _("This device is disconnected from CWNG. Your downloaded books stay on it."),
        timeout = 4,
    })
end

return SetupFlow

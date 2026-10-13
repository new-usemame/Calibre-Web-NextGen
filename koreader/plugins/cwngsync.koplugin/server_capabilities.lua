-- SPDX-License-Identifier: GPL-3.0-or-later
-- Auth capabilities belong to the server that replied, and replace older ones.
local Capabilities = {}

function Capabilities.remember(settings, body)
    local sleep_screen = type(body) == "table" and body.sleep_screen_v1 == true
    if type(body) == "table" and type(body.capabilities) == "table" then
        for _, capability in ipairs(body.capabilities) do
            if capability == "sleep_screen_v1" then sleep_screen = true end
        end
    end
    settings.server_capabilities = {
        server = settings.server,
        share_place_gates = type(body) == "table" and body.share_place_gates == true,
        sleep_screen_v1 = sleep_screen,
    }
end

function Capabilities.sleepScreen(settings)
    local capabilities = settings.server_capabilities
    return type(capabilities) == "table" and capabilities.server == settings.server
        and capabilities.sleep_screen_v1 == true
end

function Capabilities.gatesEnforced(settings)
    local capabilities = settings.server_capabilities
    return type(capabilities) == "table"
        and capabilities.server == settings.server
        and capabilities.share_place_gates == true
end

return Capabilities

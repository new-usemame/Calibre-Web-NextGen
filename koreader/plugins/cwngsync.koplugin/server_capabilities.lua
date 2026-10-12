-- SPDX-License-Identifier: GPL-3.0-or-later
-- Auth capabilities belong to the server that replied, and replace older ones.
local Capabilities = {}

function Capabilities.remember(settings, body)
    settings.server_capabilities = {
        server = settings.server,
        share_place_gates = type(body) == "table" and body.share_place_gates == true,
    }
end

function Capabilities.gatesEnforced(settings)
    local capabilities = settings.server_capabilities
    return type(capabilities) == "table"
        and capabilities.server == settings.server
        and capabilities.share_place_gates == true
end

return Capabilities

-- SPDX-License-Identifier: GPL-3.0-or-later

-- The cwasync -> cwngsync rename changes KOReader's plugin identity because
-- PluginLoader keys plugins by their *.koplugin directory basename.  Keep the
-- safety decision and settings copy in this dependency-free module so the
-- migration can be exercised outside a running KOReader instance.
local Migration = {}

local LEGACY_PLUGIN_NAME = "cwasync"
local LEGACY_SETTINGS_KEY = "cwasync"
local SETTINGS_KEY = "cwngsync"

Migration.BLOCKED_ERROR = "cwngsync startup blocked by installed cwasync.koplugin"
Migration.BLOCKED_MESSAGE = [[NextGen Sync cannot start while cwasync.koplugin is installed. Remove cwasync.koplugin, then restart KOReader. Sync is disabled until the old plugin is removed to prevent duplicate updates and conflicts.]]

local function listContainsLegacyPlugin(plugins)
    for _, plugin in ipairs(plugins or {}) do
        if plugin.name == LEGACY_PLUGIN_NAME then
            return true
        end
    end
    return false
end

function Migration.canStart(plugin_loader)
    if not plugin_loader then
        return true
    end

    local is_loaded = false
    if type(plugin_loader.isPluginLoaded) == "function" then
        is_loaded = plugin_loader:isPluginLoaded(LEGACY_PLUGIN_NAME)
    elseif plugin_loader.loaded_plugins then
        is_loaded = plugin_loader.loaded_plugins[LEGACY_PLUGIN_NAME] ~= nil
    end

    -- The discovery lists matter independently of loaded_plugins.  Depending
    -- on creation order the legacy module may be installed and enabled without
    -- having been instantiated yet; disabled_plugins covers an installed copy
    -- the user may re-enable later without removing the new plugin.
    local is_installed = listContainsLegacyPlugin(plugin_loader.enabled_plugins)
        or listContainsLegacyPlugin(plugin_loader.disabled_plugins)

    if is_loaded or is_installed then
        return false, Migration.BLOCKED_MESSAGE
    end
    return true
end

local function copyTable(value, seen)
    if type(value) ~= "table" then
        return value
    end
    seen = seen or {}
    if seen[value] then
        return seen[value]
    end
    local copy = {}
    seen[value] = copy
    for key, item in pairs(value) do
        copy[copyTable(key, seen)] = copyTable(item, seen)
    end
    return copy
end

function Migration.migrateSettings(reader_settings)
    local current = reader_settings:readSetting(SETTINGS_KEY)
    if current ~= nil then
        return current, false
    end

    local legacy = reader_settings:readSetting(LEGACY_SETTINGS_KEY)
    if legacy == nil then
        return nil, false
    end

    local migrated = copyTable(legacy)
    reader_settings:saveSetting(SETTINGS_KEY, migrated)
    return migrated, true
end

return Migration

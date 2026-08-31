-- SPDX-License-Identifier: GPL-3.0-or-later

local InfoMessage = require("ui/widget/infomessage")
local UIManager = require("ui/uimanager")
local WidgetContainer = require("ui/widget/container/widgetcontainer")
local _ = require("gettext")

local LegacySyncNotice = WidgetContainer:extend{
    name = "cwasync",
    title = _("NextGen Sync has moved"),
    version = "4.1.43",
}

local function showMigrationNotice()
    UIManager:show(InfoMessage:new{
        text = _([[NextGen Sync is now cwngsync.koplugin. Remove cwasync.koplugin, install cwngsync.koplugin, then restart KOReader. This legacy plugin no longer synchronizes progress or highlights.]]),
    })
end

function LegacySyncNotice:init()
    self.ui.menu:registerToMainMenu(self)
    UIManager:nextTick(showMigrationNotice)
end

function LegacySyncNotice:addToMainMenu(menu_items)
    menu_items.cwasync_migration_notice = {
        text = self.title,
        sorting_hint = "tools",
        callback = showMigrationNotice,
    }
end

return LegacySyncNotice

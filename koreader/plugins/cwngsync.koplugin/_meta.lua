-- Renamed from the legacy cwasync.koplugin bundled by Calibre-Web NextGen.

local _ = require("gettext")
return {
    name = "cwngsync",
    fullname = _("NextGen Progress Sync"),
    description = _([[Synchronizes your reading progress to Calibre-Web NextGen and across your KOReader devices.]]),
    version = "4.1.43",  -- Updates Manager reads this; keep in lockstep with main.lua and the CWNG release tag
}

--[[
The CWNG home: what a reader sees in place of the file browser once the
library is on. Tabs for the books being read, the newest additions, shelves,
authors and series; search across every book, downloaded or not; tap a cover
to open it (a cloud book downloads first, see cwng_library_runtime.lua); hold
it to mark it read or see its details.

Drawn with KOReader's own book list, as the Cover browser's grid when that
plugin is on. What it lists comes from cwng_catalog.lua over the library
manifest, so a library of thousands browses without opening a single file.
]]

local BD = require("ui/bidi")
local Blitbuffer = require("ffi/blitbuffer")
local BookList = require("ui/widget/booklist")
local Button = require("ui/widget/button")
local ButtonDialog = require("ui/widget/buttondialog")
local Catalog = require("cwng_catalog")
local Device = require("device")
local DocumentRegistry = require("document/documentregistry")
local Geom = require("ui/geometry")
local HorizontalGroup = require("ui/widget/horizontalgroup")
local InfoMessage = require("ui/widget/infomessage")
local InputDialog = require("ui/widget/inputdialog")
local LineWidget = require("ui/widget/linewidget")
local Menu = require("ui/widget/menu")
local ReadHistory = require("readhistory")
local Size = require("ui/size")
local TitleBar = require("ui/widget/titlebar")
local UIManager = require("ui/uimanager")
local VerticalGroup = require("ui/widget/verticalgroup")
local filemanagerutil = require("apps/filemanager/filemanagerutil")
local lfs = require("libs/libkoreader-lfs")
local logger = require("logger")
local util = require("util")
local T = require("ffi/util").template
local _ = require("gettext")
local N_ = _.ngettext
local Screen = Device.screen

local TABS = {
    { id = "reading", text = _("Reading") },
    { id = "recent", text = _("Recent") },
    { id = "shelf", text = _("Shelves") },
    { id = "author", text = _("Authors") },
    { id = "series", text = _("Series") },
}

-- The title bar with the tab row under it. Menu takes it as its title bar,
-- so it answers the few calls Menu and the Cover browser make on one.
local Header = VerticalGroup:extend{
    align = "left",
    width = nil,
    title_bar = nil,
    active = nil,
    on_tab = nil,
    show_parent = nil,
}

function Header:init()
    self.tab_row = HorizontalGroup:new{}
    self:buildTabs()
    self[1] = self.title_bar
    self[2] = self.tab_row
    self[3] = LineWidget:new{
        background = Blitbuffer.COLOR_GRAY,
        dimen = Geom:new{ w = self.width, h = Size.line.medium },
    }
    self.dimen = Geom:new{ x = 0, y = 0, w = self.width, h = self:getSize().h }
end

function Header:buildTabs()
    self.tab_row:clear()
    local tab_w = math.floor(self.width / #TABS)
    for i, tab in ipairs(TABS) do
        local active = tab.id == self.active
        local button = Button:new{
            text = tab.text,
            width = i == #TABS and self.width - tab_w * (#TABS - 1) or tab_w,
            bordersize = 0,
            text_font_size = 18,
            text_font_bold = active,
            callback = function() self.on_tab(tab.id) end,
            show_parent = self.show_parent,
        }
        table.insert(self.tab_row, VerticalGroup:new{
            button,
            LineWidget:new{
                background = active and Blitbuffer.COLOR_BLACK or Blitbuffer.COLOR_WHITE,
                dimen = Geom:new{ w = button:getSize().w, h = Size.line.thick * 2 },
            },
        })
    end
    self.tab_row:resetLayout()
    self:resetLayout()
end

function Header:setActive(id)
    if id ~= self.active then
        self.active = id
        self:buildTabs()
    end
end

function Header:getHeight() return self.dimen.h end
function Header:setTitle(title, no_refresh) self.title_bar:setTitle(title, no_refresh) end
function Header:setSubTitle(subtitle, no_refresh) self.title_bar:setSubTitle(subtitle, no_refresh) end
function Header:setLeftIcon(icon) self.title_bar:setLeftIcon(icon) end
function Header:generateVerticalLayout() return self.title_bar:generateVerticalLayout() end

local cover_modules
local function coverModules()
    if cover_modules == nil then
        local ok_menu, CoverMenu = pcall(require, "covermenu")
        local ok_mosaic, MosaicMenu = pcall(require, "mosaicmenu")
        local ok_info, BookInfoManager = pcall(require, "bookinfomanager")
        cover_modules = ok_menu and ok_mosaic and ok_info
            and { CoverMenu = CoverMenu, MosaicMenu = MosaicMenu, BookInfoManager = BookInfoManager }
            or false
    end
    return cover_modules or nil
end

local Home = BookList:extend{
    name = "cwng_home",
    is_borderless = true,
    is_popout = false,
    covers_fullscreen = true,
    plugin = nil,
    ui = nil,
}

-- The home on screen, if any; there is one file browser and one home.
Home.current = nil

function Home:init()
    self.stack = {}
    self.downloaded_only = self.plugin.settings.home_downloaded_only == true
    local width = Screen:getWidth()
    self.header = Header:new{
        width = width,
        show_parent = self,
        on_tab = function(id) self:showTab(id) end,
        title_bar = TitleBar:new{
            width = width,
            fullscreen = true,
            align = "center",
            title = _("Library"),
            subtitle = "",
            title_top_padding = Screen:scaleBySize(6),
            button_padding = Screen:scaleBySize(5),
            left_icon = "appbar.menu",
            left_icon_size_ratio = 1,
            left_icon_tap_callback = function() self:showHomeMenu() end,
            right_icon = "appbar.search",
            right_icon_size_ratio = 1,
            right_icon_tap_callback = function() self:askSearch() end,
            show_parent = self,
        },
    }
    self.custom_title_bar = self.header
    BookList.init(self)
    -- Menu enables its back arrow while `paths` is not empty.
    self.paths = self.stack
    self.fold = Catalog.memoize(Catalog.makeFold(util.stringLower))
    self:loadCatalog()
    self:showTab(self:defaultTab())
end

-- Book files in the library folder the library does not track (see
-- Catalog.localEntries). Only those are looked at closely.
local function untrackedFiles(root, tracked)
    local files = {}
    local ok, iterator, dir = pcall(lfs.dir, root)
    if not ok then return files end
    for name in iterator, dir do
        local path = root .. "/" .. name
        if not tracked[path] and name:sub(1, 1) ~= "." and not name:find("%.part$")
                and DocumentRegistry:hasProvider(path) then
            local attributes = lfs.attributes(path)
            if attributes and attributes.mode == "file" then
                files[#files + 1] = { name = name, path = path, mtime = attributes.modification }
            end
        end
    end
    table.sort(files, function(a, b) return a.name < b.name end)
    return files
end

local function cachedMetadata(path)
    local modules = coverModules()
    if not modules then return nil end
    local ok, info = pcall(modules.BookInfoManager.getBookInfo, modules.BookInfoManager, path, false)
    if not ok or type(info) ~= "table" or info.ignore_meta or type(info.title) ~= "string" then return nil end
    local authors
    if type(info.authors) == "string" and info.authors ~= "" then
        authors = {}
        for author in info.authors:gmatch("[^\n]+") do authors[#authors + 1] = author end
    end
    return { title = info.title, authors = authors }
end

function Home:loadCatalog()
    local state = self.plugin:getLibraryState()
    local root = self.plugin:getLibraryRoot()
    local all = Catalog.entries(state.manifest or {}, state.books or {}, root)
    local present, tracked = {}, {}
    for _, entry in ipairs(all) do
        if entry.present then present[#present + 1] = entry end
        tracked[entry.path] = true
    end
    for _, known in pairs(state.books or {}) do
        if known.path then tracked[known.path] = true end
    end
    for _, entry in ipairs(Catalog.localEntries(untrackedFiles(root, tracked), tracked, cachedMetadata)) do
        present[#present + 1] = entry
    end
    self.all_entries = present
    self.shelves = state.shelves or {}
end

function Home:entries()
    if self.downloaded_only then return Catalog.downloadedOnly(self.all_entries) end
    return self.all_entries
end

local function historyPaths(root)
    local paths = {}
    local prefix = root .. "/"
    for _, item in ipairs(ReadHistory.hist or {}) do
        if type(item.file) == "string" and item.file:sub(1, #prefix) == prefix then
            paths[#paths + 1] = item.file
        end
    end
    return paths
end

-- KOReader's own status for a book here, or nil when it has none.
local function localStatus(path)
    local status = BookList.getBookStatus(path)
    if status == "new" then return nil end
    return status
end

function Home:readingNow()
    return Catalog.continueReading(self:entries(), historyPaths(self.plugin:getLibraryRoot()), nil, localStatus)
end

function Home:defaultTab()
    if #self:readingNow() > 0 then return "reading" end
    return "recent"
end

-- The book grid when the Cover browser is on, a plain list otherwise and for
-- the lists of shelves, authors and series.
function Home:useGrid(on)
    local modules = on and self.ui and self.ui.coverbrowser and coverModules()
    if modules then
        if not self.grid_ready then
            local settings = modules.BookInfoManager
            self.nb_cols_portrait = settings:getSetting("nb_cols_portrait") or 3
            self.nb_rows_portrait = settings:getSetting("nb_rows_portrait") or 3
            self.nb_cols_landscape = settings:getSetting("nb_cols_landscape") or 4
            self.nb_rows_landscape = settings:getSetting("nb_rows_landscape") or 2
            self._do_cover_images = true
            self._do_hint_opened = true
            self._do_center_partial_rows = false
            self.grid_ready = true
        end
        self.display_mode_type = "mosaic"
        self.updateItems = modules.CoverMenu.updateItems
        self._recalculateDimen = modules.MosaicMenu._recalculateDimen
        self._updateItemsBuildUI = modules.MosaicMenu._updateItemsBuildUI
        self.used_covers = true
    else
        -- Back to the class's own (Menu's) list drawing.
        self.display_mode_type = nil
        self.updateItems = nil
        self._recalculateDimen = nil
        self._updateItemsBuildUI = nil
    end
end

local function bookItems(list)
    local items = {}
    for i, entry in ipairs(list) do
        items[i] = { text = entry.title, path = entry.path, is_file = true, book = entry }
    end
    return items
end

local function groupItems(groups, kind)
    local items = {}
    for i, group in ipairs(groups) do
        items[i] = {
            text = group.name,
            sort_text = group.sort_name,
            mandatory = tostring(group.count),
            group = { kind = kind, key = group.key, name = group.name },
        }
    end
    return items
end

function Home:bookCount(n)
    if self.downloaded_only then
        return T(N_("1 downloaded book", "%1 downloaded books", n), n)
    end
    return T(N_("1 book", "%1 books", n), n)
end

local GROUP_COUNTS = {
    shelf = function(n) return T(N_("1 shelf", "%1 shelves", n), n) end,
    author = function(n) return T(N_("1 author", "%1 authors", n), n) end,
    series = function(n) return T(N_("1 series", "%1 series", n), n) end,
}

function Home:emptyText(view)
    if view.query then
        return T(_("No books match “%1”."), view.query)
    elseif self.downloaded_only and #self.all_entries > 0 then
        return _("No downloaded books here. Choose Show all books in the menu.")
    elseif #self.all_entries == 0 then
        return _("Your books appear here after the first sync.")
    elseif view.tab == "reading" then
        return _("Books you start reading appear here.")
    elseif view.tab == "shelf" then
        return _("Shelves you make in CWNG appear here.")
    elseif view.tab == "series" then
        return _("No books in a series.")
    end
    return _("Nothing here yet.")
end

function Home:viewContent(view)
    local entries = self:entries()
    if view.query then
        return "books", Catalog.search(entries, view.query, self.fold), T(_("“%1”"), view.query)
    elseif view.group then
        return "books", Catalog.members(entries, view.group.kind, view.group.key, self.fold), view.group.name
    elseif view.tab == "reading" then
        return "books", self:readingNow(), _("Library")
    elseif view.tab == "recent" then
        return "books", Catalog.recentlyAdded(entries), _("Library")
    end
    return "groups", Catalog.groups(entries, view.tab, self.shelves, self.fold), _("Library")
end

function Home:render(view, page)
    self.view = view
    -- Search results belong to no tab.
    self.header:setActive(not view.query and view.tab or nil)
    local kind, list, title = self:viewContent(view)
    local items, subtitle
    if #list == 0 then
        self:useGrid(false)
        items = { { text = self:emptyText(view), dim = true } }
        subtitle = ""
    elseif kind == "books" then
        self:useGrid(true)
        items = bookItems(list)
        subtitle = self:bookCount(#list)
    else
        self:useGrid(false)
        items = groupItems(list, view.tab)
        subtitle = GROUP_COUNTS[view.tab](#list)
    end
    self.header:setTitle(title, true)
    self.header:setSubTitle(subtitle, true)
    self.item_table = items
    self.search_index = nil
    self.itemnumber = nil
    self.page = page or 1
    self:updateItems(1)
end

-- The back arrow only when there is somewhere to go back to.
function Home:updatePageInfo(select_number)
    BookList.updatePageInfo(self, select_number)
    self.page_return_arrow:showHide(#self.stack > 0)
end

function Home:clearStack()
    for i = #self.stack, 1, -1 do self.stack[i] = nil end
end

function Home:push()
    table.insert(self.stack, { view = self.view, page = self.page })
end

function Home:showTab(id)
    self:clearStack()
    self:render({ tab = id })
end

function Home:onReturn()
    local previous = table.remove(self.stack)
    if previous then self:render(previous.view, previous.page) end
    return true
end

-- The Back key (on devices that have one) steps back; the home itself stays.
function Home:onClose()
    if #self.stack > 0 then return self:onReturn() end
    return true
end

-- A swipe down from the top brings KOReader's menu, as in the file browser.
function Home:onSwipe(arg, ges_ev)
    local direction = BD.flipDirectionIfMirroredUILayout(ges_ev.direction)
    if direction == "south" then
        if ges_ev.pos and ges_ev.pos.y <= self.header:getHeight() then
            self:showKOReaderMenu()
        end
        return true
    end
    return BookList.onSwipe(self, arg, ges_ev)
end

function Home:refresh()
    self:loadCatalog()
    if self.view then self:render(self.view, self.page) end
end

function Home:setDownloadedOnly(on)
    self.downloaded_only = on
    self.plugin.settings.home_downloaded_only = on or nil
    self:clearStack()
    self:render({ tab = self.view and self.view.tab or "recent" })
end

-- "Go to letter" (tap the page number) follows the list's order: authors
-- by surname, accents ignored.
function Home:goToMenuItemMatching(search_string, goto_letter)
    if not goto_letter then
        return BookList.goToMenuItemMatching(self, search_string, goto_letter)
    end
    local prefix = self.fold(search_string)
    for i, item in ipairs(self.item_table) do
        if self.fold(item.sort_text or item.text):sub(1, #prefix) == prefix then
            self.itemnumber = i
            self:onGotoPage(self:getPageNumber(i))
            return
        end
    end
end

function Home:onMenuSelect(item)
    if item.group then
        self:push()
        self:render({ tab = self.view.tab, group = item.group })
    elseif item.path then
        self:openBook(item)
    end
    return true
end

function Home:openBook(item)
    if lfs.attributes(item.path, "mode") ~= "file" then
        UIManager:show(InfoMessage:new{
            text = _("This book is still being added to this device. Try again in a moment."),
            timeout = 3,
        })
        self.plugin:syncLibrary({ force = true })
        return
    end
    filemanagerutil.openFile(self.ui, item.path)
end

local function bookHeading(book)
    if book and #book.authors > 0 then
        return book.title .. "\n" .. table.concat(book.authors, ", ")
    end
    return book and book.title or ""
end

function Home:onMenuHold(item)
    if not item.path or lfs.attributes(item.path, "mode") ~= "file" then return true end
    local file = item.path
    local dialog
    local function close() UIManager:close(dialog) end
    local function closeAndRefresh()
        UIManager:close(dialog)
        self:refresh()
    end
    local book_props = self.ui.coverbrowser and self.ui.coverbrowser:getBookInfo(file)
    local doc_settings_or_file = file
    if BookList.hasBookBeenOpened(file) then
        doc_settings_or_file = BookList.getDocSettings(file)
    end
    dialog = ButtonDialog:new{
        title = bookHeading(item.book),
        title_align = "center",
        buttons = {
            filemanagerutil.genStatusButtonsRow(doc_settings_or_file, closeAndRefresh),
            {},
            {
                filemanagerutil.genBookInformationButton(doc_settings_or_file, book_props, close),
                filemanagerutil.genBookDescriptionButton(file, book_props, close),
            },
            {
                {
                    text = item.book and item.book.downloaded and _("Open") or _("Download and open"),
                    callback = function()
                        close()
                        self:openBook(item)
                    end,
                },
            },
        },
    }
    UIManager:show(dialog)
    return true
end

function Home:askSearch()
    local dialog
    dialog = InputDialog:new{
        title = _("Search your library"),
        input = self.last_query or "",
        input_hint = _("Title, author or series"),
        buttons = {
            {
                {
                    text = _("Cancel"),
                    id = "close",
                    callback = function() UIManager:close(dialog) end,
                },
                {
                    text = _("Search"),
                    is_enter_default = true,
                    callback = function()
                        local query = util.trim(dialog:getInputText() or "")
                        if query == "" then return end
                        UIManager:close(dialog)
                        self.last_query = query
                        if not (self.view and self.view.query) then self:push() end
                        self:render({ tab = self.view and self.view.tab or "recent", query = query })
                    end,
                },
            },
        },
    }
    UIManager:show(dialog)
    dialog:onShowKeyboard()
end

function Home:showKOReaderMenu()
    if self.ui and self.ui.menu and self.ui.menu.onShowMenu then
        self.ui.menu:onShowMenu()
    end
end

function Home:browseFiles()
    UIManager:close(self)
end

function Home:showHomeMenu()
    local plugin = self.plugin
    local dialog
    local function close() UIManager:close(dialog) end
    dialog = ButtonDialog:new{
        title = T(_("CWNG library of %1"), plugin.settings.username or ""),
        title_align = "center",
        buttons = {
            { { text = _("Sync now"), callback = function()
                close()
                plugin:syncEverythingNow()
            end } },
            { { text = self.downloaded_only and _("Show all books") or _("Show only downloaded books"),
                callback = function()
                    close()
                    self:setDownloadedOnly(not self.downloaded_only)
                end } },
            { { text = _("Browse files"), callback = function()
                close()
                self:browseFiles()
            end } },
            { { text = _("KOReader menu"), callback = function()
                close()
                self:showKOReaderMenu()
            end } },
        },
    }
    UIManager:show(dialog)
end

-- A book is opening: the reader takes the screen, and the file browser this
-- home sits on closes too.
function Home:onShowingReader()
    UIManager:close(self)
end

function Home:onCloseWidget()
    if Home.current == self then Home.current = nil end
    local modules = self.used_covers and coverModules()
    if modules then
        modules.CoverMenu.onCloseWidget(self)
    else
        Menu.onCloseWidget(self)
    end
end

function Home.show(plugin)
    local current = Home.current
    if current then
        if current.ui == plugin.ui then
            current:refresh()
            return current
        end
        UIManager:close(current)
    end
    local ok, home = pcall(Home.new, Home, { plugin = plugin, ui = plugin.ui })
    if not ok then
        logger.warn("CWNGSync: could not show the library home", home)
        return nil
    end
    Home.current = home
    UIManager:show(home)
    return home
end

function Home.refreshShown()
    local home = Home.current
    if home then
        local ok, err = pcall(home.refresh, home)
        if not ok then logger.warn("CWNGSync: library home refresh failed", err) end
    end
end

-- A book sent from the website has just landed: say so, and show it first
-- in Recent unless the reader is busy browsing something else.
function Home.bookArrived(path)
    local home = Home.current
    if not home or type(path) ~= "string" then return end
    local title = Catalog.parseFilename(path:match("([^/]+)$") or path)
    local view = home.view
    if #home.stack == 0 and view and not view.query and (view.tab == "reading" or view.tab == "recent") then
        home:showTab("recent")
    end
    UIManager:show(InfoMessage:new{ text = T(_("New on this device: %1"), title), timeout = 4 })
end

function Home.closeFor(ui)
    local home = Home.current
    if home and home.ui == ui then UIManager:close(home) end
end

return Home

-- Load the whole plugin and use its menu callbacks, settings and digest method.
package.path = '../?.lua;./?.lua;' .. package.path
local function stub()
    return setmetatable({}, { __index = function() return stub() end,
        __call = function() return stub() end })
end
local data, hashed, file_reads, flushed = {}, {}, 0, 0
local real_md5 = os.getenv('CWNG_REAL_MD5') == '1' and require('ffi/sha2').md5 or nil
local function nameDigest(value) return real_md5 and real_md5(value) or ('hash<' .. value .. '>') end
local reader_settings = {
    readSetting = function(_, key, default) return data[key] or default end,
    saveSetting = function(_, key, value) data[key] = value end,
    flush = function() flushed = flushed + 1 end,
    delSetting = function(_, key) data[key] = nil end,
}
local env
env = setmetatable({
    G_reader_settings = reader_settings,
    require = function(name)
        if name == 'ui/widget/container/widgetcontainer' then
            return { extend = function(_, value) return value end }
        elseif name == 'gettext' then return function(value) return value end
        elseif name == 'ffi/sha2' then return { md5 = function(value)
            hashed[#hashed + 1] = value
            return nameDigest(value)
        end }
        elseif name == 'sync_logic' or name == 'cwng_pending' then return require(name)
        elseif name == 'cwng_auto_sync' then return assert(loadfile('../cwng_auto_sync.lua', 't', env))()
        elseif name == 'cwng_library_runtime' then return assert(loadfile('../cwng_library_runtime.lua', 't', env))()
        elseif name == 'ui/network/manager' then return { isConnected = function() return false end }
        elseif name == 'optmath' then return { roundPercent = function(value) return value end }
        elseif name == 'libs/libkoreader-lfs' then return { attributes = function() return { size = 4096, modification = 1700000000 } end }
        elseif name == 'util' then return { partialMD5 = function(path)
            file_reads = file_reads + 1
            return 'binary<' .. path .. '>'
        end }
        elseif name == 'device_identity' then return { settle = function() end }
        elseif name == 'ffi/util' then return { template = function(value) return value end }
        end
        return stub()
    end,
}, { __index = _G })
local plugin = assert(loadfile('../main.lua', 't', env))()
local function instance(settings, path)
    return setmetatable({ settings_key = 'cwngsync', settings = settings or {},
        ui = { document = { file = path or '/books/Title - Author.epub' } },
        push_timestamp = 100, pull_timestamp = 100,
    }, { __index = plugin })
end
local function find(items, text)
    for _, item in ipairs(items) do
        if item.text == text then return item end
        local child = find(item.sub_item_table or {}, text)
        if child then return child end
    end
end
local p = instance()
assert(p:getDocumentDigest() == 'binary</books/Title - Author.epub>', 'old settings must retain binary matching')
local menu = assert(find(p:getAdvancedMenuItems(), 'Document matching method'), 'matching setting must be reachable from Advanced')
local binary = assert(find(menu.sub_item_table, 'Binary: match file contents'))
local filename = assert(find(menu.sub_item_table, 'Filename: match exact names'))
assert(binary.checked_func() and not filename.checked_func(), 'existing settings select binary')
filename.callback()
assert(filename.checked_func() and not binary.checked_func(), 'filename radio choice updates')
assert(data.cwngsync.document_matching == 'filename' and flushed == 1, 'choice is saved and flushed')
assert(p.push_timestamp == 0 and p.pull_timestamp == 0, 'old identity debounce does not suppress new sync')
local reads_before = file_reads
assert(p:getDocumentDigest() == nameDigest('Title - Author.epub'), 'current document uses basename including extension')
assert(p:getDocumentDigest('/other/Title - Author.epub') == nameDigest('Title - Author.epub'), 'explicit library path uses same exact identity')
assert(p:getDocumentDigest('/mnt/École - الكاتب.EPUB') == nameDigest('École - الكاتب.EPUB'), 'UTF-8 and extension case are preserved')
assert(file_reads == reads_before, 'filename matching must not read or rehash the file bytes')
assert(instance(data.cwngsync):getDocumentDigest() == nameDigest('Title - Author.epub'), 'choice survives a new plugin instance')
assert(p:getDocumentDigest('') == nil and p:getDocumentDigest('/directory/') == nil, 'empty names never create an identity')
p.ui.document = nil
assert(p:getDocumentDigest() == nil, 'no open document remains a safe no-op')
binary.callback()
assert(binary.checked_func() and not filename.checked_func(), 'binary can be restored')
assert(data.cwngsync.document_matching == 'binary' and flushed == 2, 'restored choice persists')
assert(p:getDocumentDigest('/other/book.epub') == 'binary</other/book.epub>', 'binary algorithm is unchanged')
assert(instance({ document_matching = 'future-value' }):getDocumentDigest() == 'binary</books/Title - Author.epub>', 'unknown stored values preserve binary fallback')
print('document matching menu/digest/persistence tests passed')

-- Capture through the real auto-sync mixin, preserving offline identities when
-- the user changes the method. Existing captures must not be renamed in place.
local q = instance({ server = 'http://library', username = 'reader' })
q.isConfigured = function() return true end
q.accountOwner = function() return 'http://library|reader' end
q.ui.document.info = { has_pages = false }
q.ui.rolling = { getLastProgress = function() return '/body/p[7]' end,
    getLastPercent = function() return 0.37 end }
q:queueOpenBook(false, nil, true)
local old_id = 'binary</books/Title - Author.epub>'
local captured = assert(data.cwngsync_pending[old_id])
assert(captured.percentage == 0.37 and captured.progress == '/body/p[7]', 'real capture records reader position')
q:setDocumentMatching('filename')
assert(data.cwngsync_pending[old_id] == captured and captured.document == old_id,
    'changing method cannot rewrite an offline capture identity')
q:queueOpenBook(false, nil, true)
local name_id = nameDigest('Title - Author.epub')
assert(data.cwngsync_pending[old_id] == captured, 'new captures preserve old offline work')
assert(data.cwngsync_pending[name_id].document == name_id and data.cwngsync_pending[name_id].percentage == 0.37,
    'future automatic captures use filename identity and the real reader position')
print('document matching automatic/offline capture tests passed')

local managed = instance({ document_matching = 'filename' })
assert(managed:libraryProbe().digest('/books/download.epub.cwngsync.part') == 'binary</books/download.epub.cwngsync.part>',
    'download verification and managed-file ownership always hash bytes even in filename mode')

local inventory = managed:buildInventory({ '/books/Title - Author.epub' }, '/books')
assert(#inventory == 1 and inventory[1].checksum == nameDigest('Title - Author.epub'),
    'inventory identifies sideloaded copies by the selected matching channel')
assert(managed:getDocumentContentDigest('/books/Title - Author.epub') == 'binary</books/Title - Author.epub>',
    'content verification remains binary for the same filename-mode book')
print('document matching identity/content seam tests passed')

if real_md5 then
    assert(nameDigest('More Everything Forever - Adam Becker.epub') == '9ea1b31e133214bb1169acce6ff4affb',
        'real KOReader sha2 must agree with the server filename-channel oracle')
    print('real KOReader SHA2 filename oracle passed')
end

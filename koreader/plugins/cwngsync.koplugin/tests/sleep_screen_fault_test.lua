-- Real runtime module, actual atomic settings/image files, bounded PNG decode,
-- with faults at transport, file publication, fsync and recovery seams.
package.path = "../?.lua;" .. package.path
local function copy(x)
    if type(x) ~= "table" then return x end
    local y = {}; for k,v in pairs(x) do y[k] = copy(v) end; return y
end
local function same(a,b)
    if type(a) ~= type(b) then return false end
    if type(a) ~= "table" then return a == b end
    for k,v in pairs(a) do if not same(v,b[k]) then return false end end
    for k in pairs(b) do if a[k] == nil then return false end end
    return true
end
local function dump(x)
    if type(x) == "string" then return string.format("%q",x) end
    if type(x) ~= "table" then return tostring(x) end
    local items = {}; for k,v in pairs(x) do items[#items+1] = "["..dump(k).."]="..dump(v) end
    return "{"..table.concat(items,",").."}"
end
local function read(path)
    local f = io.open(path,"rb"); if not f then return nil end
    local b = f:read("*a"); f:close(); return b
end
local function write(path,bytes)
    local f = assert(io.open(path,"wb")); assert(f:write(bytes)); assert(f:close())
end
local root = os.tmpname(); os.remove(root); assert(os.execute("mkdir -p "..root))
local original = { screensaver_type="random_image", screensaver_show_message=false,
    screensaver_stretch_images=true, screensaver_img_background="black", screensaver_message="mine",
    screensaver_rotate_auto_for_best_fit={ preference=false }, screensaver_dir=root.."/user.png" }
write(original.screensaver_dir,"a user file")
local settings = {data=copy(original),file=root.."/settings.reader.lua"}
function settings:has(k) return self.data[k] ~= nil end
function settings:readSetting(k) return self.data[k] end
function settings:saveSetting(k,v) self.data[k]=v end
function settings:delSetting(k) self.data[k]=nil end
_G.G_reader_settings=settings
write(settings.file,"return "..dump(settings.data))
local connected, supported, reports, fetches, acks = true,true,{},0,{}
local manifest_requests={}
local answer, reject_ack, bad_bytes, http_error, conditional = nil,false,nil,nil,false
local settings_publishes, fail_directory_at, fail_rename_at = 0,nil,nil
local real_rename = os.rename
os.rename = function(from,to)
    if to == settings.file then
        settings_publishes=settings_publishes+1
        if settings_publishes==fail_rename_at then return nil,"injected settings rename failure" end
    end
    return real_rename(from,to)
end
local function install(n,v) package.loaded[n]=v end
install("device", {model="Kindle",isKindle=function() return true end,isKobo=function() return false end,
    supportsScreensaver=function() return supported end,
    screen={getWidth=function() return 12 end,getHeight=function() return 8 end}})
install("datastorage",{getFullDataDir=function() return root end})
install("ui/network/manager",{isConnected=function() return connected end})
install("libs/libkoreader-lfs",{dir=function() return function() return nil end end})
install("util",{makePath=function(path) return os.execute("mkdir -p "..path) end})
install("logger",{warn=function() end})
install("dump",dump)
install("ffi/util",{fsyncOpenedFile=function(file) return file:flush() ~= nil end,
    fsyncDirectory=function(path) return not(path==settings.file and settings_publishes==fail_directory_at) end})
-- Stable byte-sensitive digest stub; the production code uses ffi/sha2 SHA256.
local function digest(bytes)
    local n=0; for i=1,#bytes do n=(n*31+bytes:byte(i))%2147483647 end
    return tostring(n)
end
install("ffi/sha2",{sha256=digest})
local image=assert(read("fixtures/sleep-screen.png"))
local replacement=assert(read("fixtures/sleep-screen-replacement.png"))
local assets={}
local function desired(revision,bytes)
    assets[revision]=bytes
    answer={revision=revision,state="image",width=8,height=12,size=#bytes,sha256=digest(bytes),
        content_type="image/png",download_path="/syncs/sleep-screen/image?revision="..revision,etag='"'..revision..'"'}
end
local client={}
function client:report_sleep_screen_device(_,_,_,_,report)
    reports[#reports+1]=copy(report)
    if http_error then return false,nil,http_error end
    return true,{}
end
function client:get_sleep_screen_manifest(_,_,_,_,etag)
    manifest_requests[#manifest_requests+1]=etag or false
    if conditional and etag==answer.etag then return true,{status=304} end
    return true,copy(answer)
end
function client:download_sleep_screen_image(_,_,_,_,_,path)
    fetches=fetches+1
    write(path,bad_bytes or assets[answer.revision])
    return true,{status=200}
end
function client:ack_sleep_screen(_,_,_,_,receipt)
    acks[#acks+1]=copy(receipt)
    return not reject_ack,{}
end
install("CWNGSyncClient",{new=function() return client end})
local Sleep=require("sleep_screen")
local plugin=setmetatable({settings={server="https://cwng.invalid/",username="reader",password="test"},
    device_id="reader-1",path="..",version="4.1.47",isConfigured=function() return true end},{__index=Sleep})
local function managed()
    assert(settings.data.screensaver_type=="document_cover")
    local path=settings.data.screensaver_document_cover
    assert(read(path),"managed settings reference an existing asset")
    local disk=assert(dofile(settings.file))
    assert(disk.screensaver_document_cover==path,"asset settings persisted")
    return path
end
local function assertOriginal()
    for k,v in pairs(original) do assert(same(settings.data[k],v),"restored "..k) end
    assert(settings.data.screensaver_document_cover==nil,"restored absence")
    assert(read(original.screensaver_dir)=="a user file","never removes user files")
    assert(same(settings.data,dofile(settings.file)),"restoration persisted")
end
local function resetFaults() fail_directory_at,fail_rename_at,bad_bytes,http_error=nil,nil,nil,nil end
local ok,err=xpcall(function()
    desired("first",image)
    reject_ack=true
    assert(plugin:syncSleepScreen()); local first=managed()
    assert(reports[1].screen_width==8 and reports[1].screen_height==12,"portrait framebuffer report")
    assert(settings.data.cwngsync_sleep_screen.pending.status=="applied","failed ack queued")
    reject_ack=false; conditional=true
    local previous_fetches,previous_writes=fetches,settings_publishes
    settings.data.screensaver_type="random_image"
    assert(plugin:syncSleepScreen())
    assert(fetches==previous_fetches,"304 reuses intact bytes")
    assert(settings.data.screensaver_type=="random_image","304 respects later manual preferences")
    assert(settings_publishes==previous_writes+1,"only receipt removal persisted")
    previous_writes=settings_publishes
    assert(plugin:syncSleepScreen())
    assert(settings_publishes==previous_writes,"settled 304 performs no settings rewrite")
    settings.data.cwngsync_sleep_screen.manifest=nil
    write(settings.file,"return "..dump(settings.data))
    local previous_manifests=#manifest_requests
    assert(plugin:syncSleepScreen())
    assert(#manifest_requests==previous_manifests+2 and manifest_requests[#manifest_requests]==false,
        "304 with lost cached manifest requests fresh manifest unconditionally")
    assert(fetches==previous_fetches,"lost manifest still reuses independently verified intact asset")
    assert(settings.data.screensaver_type=="random_image","manifest recovery does not retake manually changed preferences")
    os.remove(first)
    assert(plugin:syncSleepScreen()); local recovered=managed()
    assert(fetches==previous_fetches+1,"304 with missing file fetches unconditionally")
    desired("second",replacement)
    bad_bytes=replacement:sub(1,-2)..string.char((replacement:byte(-1)+1)%256)
    assert(not plugin:syncSleepScreen()); assert(settings.data.screensaver_document_cover==recovered)
    assert(acks[#acks].revision=="second" and acks[#acks].error=="checksum_mismatch","failure receipt identifies attempted revision")
    resetFaults()
    -- Bytes have matching length/digest but cannot actually decode as a PNG.
    local corrupt="not PNG"; desired("bad-png",corrupt)
    assert(not plugin:syncSleepScreen()); assert(acks[#acks].error=="invalid_image")
    assert(settings.data.screensaver_document_cover==recovered and read(recovered))
    desired("second",replacement)
    fail_rename_at=settings_publishes+2
    assert(not plugin:syncSleepScreen())
    assert(settings.data.screensaver_document_cover==recovered and read(recovered),"rename failure preserves prior image")
    resetFaults()
    -- Install settings rename succeeds; its directory fsync fails; then
    -- rollback settings rename also fails. Restart must retain the new asset.
    local start=settings_publishes
    fail_directory_at=start+2; fail_rename_at=start+3
    assert(not plugin:syncSleepScreen())
    local disk=dofile(settings.file)
    assert(disk.screensaver_type=="document_cover" and read(disk.screensaver_document_cover),"failed rollback never deletes disk's new active image")
    settings.data=disk; resetFaults()
    assert(plugin:syncSleepScreen()); managed()
    connected=false; answer={revision="remove-1",state="remove",etag='"remove-1"'}
    assert(plugin:syncSleepScreen()==nil); managed()
    connected=true
    assert(plugin:syncSleepScreen()); assertOriginal()
    assert(plugin:syncSleepScreen()); assertOriginal()
    desired("third",image); assert(plugin:syncSleepScreen()); managed()
    http_error="HTTP 401"
    assert(not plugin:syncSleepScreen()); managed()
    connected=false
    assert(plugin:restoreSleepScreen()); assertOriginal()
    resetFaults(); connected=true
    desired("fourth",image); assert(plugin:syncSleepScreen()); managed()
    plugin.settings.username="other-reader"
    assert(plugin:recoverSleepScreen()); assertOriginal()
    assert(settings.data.cwngsync_sleep_screen==nil,"owner change clears takeover")
    plugin.settings.username="reader"
    desired("fifth",image); assert(plugin:syncSleepScreen()); managed()
    plugin.device_id="new-copied-id"
    assert(plugin:recoverSleepScreen()); assertOriginal()
    -- Explicit unsupported never takes over, but still reports its reason.
    supported=false; desired("unsupported",image)
    assert(not plugin:syncSleepScreen()); assertOriginal()
    assert(reports[#reports].supported==false)
    print("sleep screen faults: 304/lost cache, missing asset, checksum/decode, atomic rename/fsync rollback, restart, offline restore and owner scope passed")
end,debug.traceback)
os.rename=real_rename
os.execute("rm -rf "..root)
if not ok then error(err,0) end

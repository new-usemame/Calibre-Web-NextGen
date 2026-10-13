-- Exercise the production HTTP transport, including bounded chunks rather
-- than trusting Content-Length and no redirects carrying Basic credentials.
package.path="../?.lua;"..package.path
local requests, response, body, header, sent = {},200,'{"revision":"one"}',{},1
-- The HTTP boundary is isolated from KOReader's JSON library just as it is
-- from LuaSocket below. Keep this test runnable in CI's bare Lua/LuaJIT;
-- requiring a developer-machine dkjson installation would add a dependency
-- the plugin itself never needs. The fixture has only scalar object members.
package.loaded["json"]={encode=function(value)
    local keys,fields={},{}
    for key in pairs(value) do keys[#keys+1]=key end
    table.sort(keys)
    for _,key in ipairs(keys) do
        local item=value[key]
        if type(item)=="string" then
            assert(not item:find('["\\%c]'),"fixture string needs JSON escaping")
            item='"'..item..'"'
        else
            assert(type(item)=="boolean" or type(item)=="number","fixture value must be scalar")
            item=tostring(item)
        end
        fields[#fields+1]='"'..key..'":'..item
    end
    return "{"..table.concat(fields,",").."}"
end,decode=function(value)
    assert(value=='{"revision":"one"}',"unexpected JSON response fixture")
    return {revision="one"}
end}
package.loaded["ui/uimanager"]={}
package.loaded["logger"]={warn=function() end}
package.loaded["gettext"]=function(t) return t end
package.loaded["socketutil"]={set_timeout=function() end,reset_timeout=function() end}
package.loaded["mime"]={b64=function(x) return "encoded:"..x end}
package.loaded["ltn12"]={source={string=function(x) return function() local s=x; x=nil; return s end end}}
package.loaded["socket.http"]={request=function(request)
    requests[#requests+1]=request
    if request.source then request.payload=request.source() end
    for _,chunk in ipairs(type(body)=="table" and body or {body}) do
        if not request.sink(chunk) then return nil,"sink failed" end
    end
    request.sink(nil)
    return sent,response,header,"status"
end}
local Client=require("CWNGSyncClient")
local client=setmetatable({service_url="https://cwng.invalid/kosync"},{__index=Client})
local auth_body, authorized={capabilities={"sleep_screen_v1"}},true
function client:authorize() return authorized,auth_body,"authentication_failed" end
local path=os.tmpname(); os.remove(path)
local function last() return requests[#requests] end
local ok,err=xpcall(function()
    assert(client:report_sleep_screen_device("reader","secret","Kindle","device-1",
        {platform="android",plugin_version="4.1.47",supported=false}))
    assert(last().payload=='{"platform":"android","plugin_version":"4.1.47","supported":false,"screen_width":null,"screen_height":null}',
        "valid JSON contains both explicit unsupported null dimensions")
    assert(last().redirect==false and last().headers.Authorization=="Basic encoded:reader:secret")
    assert(last().headers["X-CWNG-Device-ID"]=="device-1")
    assert(last().url=="https://cwng.invalid/kosync/syncs/sleep-screen/device")
    local count=#requests
    auth_body={capabilities={}}
    local available,_,reason=client:report_sleep_screen_device("reader","secret","Kindle","device-1",{})
    assert(not available and reason=="sleep_screen_unavailable" and #requests==count,"old server no-op")
    header={etag='"manifest-1"'}
    local fetched,manifest=client:get_sleep_screen_manifest("reader","secret","Kindle","device-1",'"previous"')
    assert(fetched and manifest.revision=="one" and manifest.etag=='"manifest-1"')
    assert(last().headers["If-None-Match"]=='"previous"')
    response,body=304,{}
    local cached,state=client:get_sleep_screen_manifest("reader","secret","Kindle","device-1",'"manifest-1"')
    assert(cached and state.status==304)
    response,body,header=200,"PNG bytes",{["content-type"]="image/png",["content-length"]="9",etag='"image-1"'}
    local downloaded,result=client:download_sleep_screen_image("reader","secret","Kindle","device-1",
        "/syncs/sleep-screen/image?revision=one",path)
    assert(downloaded and result.size==9)
    local file=assert(io.open(path,"rb")); assert(file:read("*a")==body); file:close()
    response,body=304,{}
    local conditional,status=client:download_sleep_screen_image("reader","secret","Kindle","device-1",
        "/syncs/sleep-screen/image?revision=one",path,'"image-1"')
    assert(conditional and status.status==304 and not io.open(path,"rb"),"304 does not publish empty bytes")
    count=#requests
    for _,bad in ipairs({"https://foreign.invalid/image", "//foreign.invalid/image", "/syncs/sleep-screen/image?revision=one&url=bad",
        "/syncs/sleep-screen/image?revision=../secret", "/syncs/sleep-screen/image?revision=one\r\nInjected: yes"}) do
        assert(not client:download_sleep_screen_image("reader","secret","Kindle","device-1",bad,path))
    end
    assert(#requests==count,"invalid/foreign URL never receives credentials")
    response,header=200,{["content-type"]="image/png",["content-length"]="1"}
    body={string.rep("x",8*1024*1024),"x"}
    assert(not client:download_sleep_screen_image("reader","secret","Kindle","device-1",
        "/syncs/sleep-screen/image?revision=one",path))
    assert(not io.open(path,"rb"),"overlimit stream deletes only temporary target")
    body="error"; response=302; header={location="https://foreign.invalid/image"}
    assert(not client:download_sleep_screen_image("reader","secret","Kindle","device-1",
        "/syncs/sleep-screen/image?revision=one",path))
    assert(last().redirect==false and not io.open(path,"rb"),"redirect cannot leak credentials or install image")
    print("sleep screen transport: auth capability, null reports, ETag/304, URL scope, stream limit and redirect rejection passed")
end,debug.traceback)
os.remove(path)
if not ok then error(err,0) end

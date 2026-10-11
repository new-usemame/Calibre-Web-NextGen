-- Exercise every progress caller, including an offline report delivered later.
package.path = '../?.lua;./?.lua;' .. package.path
package.loaded['device'] = { model = 'Kindle' }
package.loaded['docsettings'] = {}
package.loaded['json'] = { decode = function() return {} end }
package.loaded['ui/network/manager'] = {}
package.loaded['ui/uimanager'] = { looper = nil, setInputTimeout = function() end }
package.loaded['logger'] = { info = function() end, warn = function() end, dbg = function() end }
package.loaded['gettext'] = function(s) return s end
package.loaded['socketutil'] = { set_timeout = function() end, reset_timeout = function() end }
local fake = { calls = {} }
function fake:reset_middlewares() self.enabled={} end
function fake:enable(name,args) self.enabled[#self.enabled+1]={name=name,args=args} end
function fake:update_read_status(params)
    local req={headers={}}
    for _,mw in ipairs(self.enabled) do
        local module=package.loaded['Spore.Middleware.'..mw.name]
        if module and module.call then module.call(mw.args,req) end
    end
    self.status_headers=req.headers
    return {status=200,body={}}
end
function fake:update_progress(params)
    self.calls[#self.calls+1] = params
    return { status = 200, body = {} }
end
package.loaded['Spore'] = { new_from_spec = function() return fake end }
local Client = require('CWNGSyncClient')
local AutoSync = require('cwng_auto_sync')
local function equal(a,b,why) assert(a==b, why .. ': expected ' .. tostring(b) .. ', got ' .. tostring(a)) end
local tests = {}
function tests.client_sends_epoch_seconds()
    local client = Client:new{service_spec={}, service_url='https://cwng.invalid/kosync'}
    local before = os.time()
    client:update_progress('reader','secret','digest','page',.4,'Kindle','raw',function() end)
    local stamp = fake.calls[#fake.calls].timestamp
    assert(type(stamp)=='number' and stamp >= before and stamp <= os.time(), 'live PUT carries an epoch timestamp')
    client:update_progress('reader','secret','digest','page',.4,'Kindle','raw',function() end,123456)
    equal(fake.calls[#fake.calls].timestamp,123456,'offline PUT keeps observation time')
end
function tests.queue_sends_identity_and_capture_time()
    local plugin = setmetatable({settings={username='reader',password='secret'}, device_id='raw'}, {__index=AutoSync})
    local pulled, pushed
    local client = {
        get_progress = function(_,...) pulled={...}; pulled[#pulled](true,{}) end,
        update_progress = function(_,...) pushed={...} end,
    }
    local ok, err = pcall(plugin.deliverPending,plugin,client,
        {document='digest',progress='page',percentage=.4,captured_at=123456},function() end)
    assert(ok,err)
    equal(pulled[4],'Kindle','queued pull model'); equal(pulled[5],'raw','queued pull id')
    equal(pushed[9],123456,'queued observation clock survives delivery')
end
function tests.status_only_request_is_identified()
    local client=Client:new{service_spec={},service_url='https://cwng.invalid/kosync'}
    client:update_read_status('reader','secret','Kindle','raw',1,nil,'finished',function() end)
    equal(fake.status_headers['X-CWNG-Device-ID'],'raw','status-only header id')
    equal(fake.status_headers['X-CWNG-Device-Name'],'Kindle','status-only header name')
end
local function mainMethod(name, parameters, client)
    local stream=assert(io.open('../main.lua')); local source=stream:read('*a'); stream:close()
    local start=assert(source:find('function CWNGSync:' .. name .. '(' .. parameters .. ')',1,true))
    local stop=assert(source:find('\nend\n',start,true))
    local env=setmetatable({
        Device={model='Kindle'}, logger=package.loaded['logger'],
        UIManager={getElapsedTimeSinceBoot=function() return 1000 end,show=function() end},
        NetworkMgr={willRerunWhenOnline=function() return false end},
        ensureServerConfigured=function() return true end,
        _=function(s) return s end,T=function(s) return s end,
        InfoMessage={new=function(_,x) return x end},
        require=function() return {new=function() return client end} end,
        API_CALL_DEBOUNCE_DELAY=25,
    },{__index=_G})
    return assert(load('local CWNGSync={}\n' .. source:sub(start,stop+4) .. '\nreturn CWNGSync','caller','t',env))()[name]
end
for _, method in ipairs({'getProgress','pullLibraryProgress','updateProgress'}) do
    tests[method .. '_names_the_device'] = function()
        local args
        local client={get_progress=function(_,...) args={...} end,update_progress=function(_,...) args={...} end}
        local plugin={settings={username='reader',password='secret',server='https://cwng.invalid'},
            device_id='raw',path='..',pull_timestamp=0,push_timestamp=0,
            hasCurrentDocument=function() return true end,
            getCurrentDocumentFile=function() return '/books/b.epub' end,
            getDocumentDigest=function() return 'digest' end,
            getLibraryBooksForSync=function() return {'/books/b.epub'},true,'/books' end,
            getLastProgress=function() return 'page' end,getLastPercent=function() return .4 end,
        }
        local params = method=='pullLibraryProgress' and 'ensure_networking' or
            method=='getProgress' and 'ensure_networking, interactive' or 'ensure_networking, interactive, on_suspend'
        mainMethod(method,params,client)(plugin,false,true,false)
        assert(args,'caller made no progress request')
        local offset=method=='updateProgress' and 2 or 0
        equal(args[4+offset],'Kindle',method .. ' model'); equal(args[5+offset],'raw',method .. ' id')
        equal(type(args[6+offset]),'function',method .. ' callback remains intact')
    end
end
local count,failed=0,0
for name,test in pairs(tests) do
    count=count+1; local ok,err=pcall(test)
    if not ok then failed=failed+1; print('FAIL '..name..': '..tostring(err)) end
end
assert(failed==0,failed..' of '..count..' traffic tests failed')
print(count..' share-place progress traffic tests passed')

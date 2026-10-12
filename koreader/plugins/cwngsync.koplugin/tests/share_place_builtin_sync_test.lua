-- Same-server built-in KOSync must not automatically pull shared progress.
package.path = '../?.lua;./?.lua;' .. package.path
local Guard = require('builtin_sync_guard')
local function setup(server)
    local persisted={settings={custom_server=server,auto_sync=true,username='reader'}}
    local flushes,events,stops,notices=0,0,0,0
    local seen={}
    local reader={readSetting=function(_,key) return seen[key] end,
        saveSetting=function(_,key,value) seen[key]=value end,flush=function() end}
    local store={readSetting=function(_,key) return persisted[key] end,
        saveSetting=function(_,key,value) persisted[key]=value end,
        flush=function() flushes=flushes+1 end}
    local live={settings=persisted.settings,settings_obj=store,
        periodic_push_task=function() end,periodic_push_scheduled=true,
        registerEvents=function() events=events+1 end}
    local args={server='https://cwng.invalid/base',share_place_gates=true,reader_settings=reader,
        open_settings=function() return store end,builtin=live,
        unschedule=function() stops=stops+1 end,
        notice=function() notices=notices+1 end}
    return args,persisted,function() return flushes,events,stops,notices end
end
local args,data,counts=setup('https://cwng.invalid/base/kosync/')
assert(Guard.check(args),'same server is handled')
assert(data.settings.auto_sync==false,'automatic built-in sync is disabled persistently')
assert(data.settings.username=='reader','credentials are preserved')
local f,e,s,n=counts()
assert(f==1 and e==1 and s==1 and n==1,'disk, live handlers, queued task, explanation updated')
assert(args.builtin.periodic_push_scheduled==false,'scheduled push cancelled')
assert(not Guard.check(args),'repeat start does nothing')
data.settings.auto_sync=true -- reader explicitly re-enabled it after the notice
assert(not Guard.check(args),'once means it respects a later explicit choice')
f,e,s,n=counts(); assert(f==1 and e==1 and s==1 and n==1,'no repeated notification or write')
local other,otherdata,othercounts=setup('https://other.invalid/kosync')
assert(not Guard.check(other) and otherdata.settings.auto_sync,'another sync server is untouched')
f,e,s,n=othercounts(); assert(f+e+s+n==0,'no side effects on another server')
local disk,diskdata,diskcounts=setup('https://cwng.invalid/base/kosync')
disk.builtin=nil
assert(Guard.check(disk) and not diskdata.settings.auto_sync,'disabled plugin settings handled on disk')
f,e,s,n=diskcounts(); assert(f==1 and e==0 and s==0 and n==1,'disk-only handling')
local startup,startupdata=setup('https://cwng.invalid/base/kosync')
local pulls=0
startup.builtin.getProgress=function() pulls=pulls+1 end
-- Built-in onReaderReady schedules this anonymous closure while auto_sync
-- is true. Changing auto_sync alone does not cancel its later execution.
local queued=function() startup.builtin:getProgress(true,false) end
assert(Guard.check(startup))
queued(); assert(pulls==0,'already queued automatic GET is suppressed')
startup.builtin:getProgress(true,true)
assert(pulls==1,'manual Progress sync remains available')
startup.share_place_gates=false
assert(not Guard.check(startup),'latest dormant capability stops guarding')
queued(); assert(pulls==2,'a previously installed pull guard is dormant again')
startup.share_place_gates=true
Guard.check(startup)
queued(); assert(pulls==2,'enforcement can resume without another notification')
startup.server='https://other.invalid'
assert(not Guard.check(startup),'another CWNG server does not control the old builtin endpoint')
queued(); assert(pulls==3,'the installed wrapper leaves a different endpoint alone')

local Capabilities=require('server_capabilities')
local settings={server='https://cwng.invalid'}
for _, body in ipairs({{share_place_gates=true},{share_place_gates=false},{},{share_place_gates='true'}}) do
    Capabilities.remember(settings,body)
    assert(Capabilities.gatesEnforced(settings)==(body.share_place_gates==true),'latest auth replaces the capability')
end
Capabilities.remember(settings,{share_place_gates=true})
settings.server='https://other.invalid'
assert(not Capabilities.gatesEnforced(settings),'a new server cannot inherit the old capability')

-- Execute both real auth call sites and the real guard wiring. Saving the
-- capability matters: initialization must see it before queued builtin GETs.
local function method(file, class, name, env)
    local stream=assert(io.open('../'..file)); local source=stream:read('*a'); stream:close()
    local start=assert(source:find('function '..class..':'..name..'(',1,true))
    local stop=assert(source:find('\nend\n',start,true))
    return assert(load('local '..class..'={}\n'..source:sub(start,stop+4)..'\nreturn '..class,
        name,'t',setmetatable(env,{__index=_G})))()[name]
end
local body,saved,guarded,authorized,reason
local client={authorize=function() return authorized,body,reason end}
local store={saveSetting=function(_,key,value)
    assert(key=='cwngsync'); saved=value.server_capabilities
end}
local env={ServerCapabilities=Capabilities,G_reader_settings=store,
    Device={setIgnoreInput=function() end},
    UIManager={show=function() end},InfoMessage={new=function(_,x) return x end},
    logger={warn=function() end,info=function() end},
    _=function(s) return s end,T=function(s) return s end,hostOf=function(s) return s end,
    Setup={applyPluginDefaults=function() end},newClient=function() return client end,
    ensureServerConfigured=function() return true end,
    getBodyMessage=function() return 'failed' end,
    require=function(name)
        if name=='CWNGSyncClient' then return {new=function() return client end} end
        if name=='builtin_sync_guard' then return {check=function(args) guarded=args.share_place_gates end} end
        error('unexpected require '..name)
    end,
    PluginLoader={getPluginInstance=function() return nil end},
}
local check=method('main.lua','CWNGSync','checkBuiltinProgressSync',env)
local login=method('main.lua','CWNGSync','doLogin',env)
local connect=method('cwng_setup_flow.lua','SetupFlow','connectWith',env)
for _, authenticate in ipairs({login,connect}) do
    local plugin={settings={server='https://cwng.invalid'},settings_key='cwngsync',path='..',
        checkBuiltinProgressSync=check,registerEvents=function() end,
        applyReaderDefaults=function() end,syncLibrary=function() end}
    for _, reply in ipairs({{share_place_gates=true},{share_place_gates=false},{},{share_place_gates='true'}}) do
        body=reply; authorized=true; saved=nil; guarded=nil
        if authenticate==connect then
            authenticate(plugin,{server=plugin.settings.server,username='reader',password='secret'},'test')
        else authenticate(plugin,'reader','secret') end
        assert(saved and saved.share_place_gates==(reply.share_place_gates==true),'auth capability persisted')
        assert(guarded==(reply.share_place_gates==true),'guard receives the latest auth capability')
        assert(Capabilities.gatesEnforced({server=plugin.settings.server,server_capabilities=saved})==guarded,
            'a restarted plugin gets the saved capability')
    end
    Capabilities.remember(plugin.settings,{share_place_gates=true})
    body={}; authorized=false; guarded=nil
    if authenticate==connect then
        authenticate(plugin,{server=plugin.settings.server,username='reader',password='bad'},'test')
    else authenticate(plugin,'reader','bad') end
    assert(guarded==false,'a rejected latest auth reply clears enforcement')
    Capabilities.remember(plugin.settings,{share_place_gates=true})
    body=nil; reason='timeout'; guarded=nil; saved=nil
    if authenticate==connect then
        authenticate(plugin,{server=plugin.settings.server,username='reader',password='secret'},'test')
    else authenticate(plugin,'reader','secret') end
    assert(Capabilities.gatesEnforced(plugin.settings),'a transport failure is not a new auth reply')
    assert(guarded==nil and saved==nil,'a missing reply leaves the latest persisted capability alone')
    reason=nil
end
print('11 share-place built-in sync tests passed')

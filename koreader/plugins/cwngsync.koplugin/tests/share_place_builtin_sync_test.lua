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
    local args={server='https://cwng.invalid/base',reader_settings=reader,
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
print('5 share-place built-in sync tests passed')

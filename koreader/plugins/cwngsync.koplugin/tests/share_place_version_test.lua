-- Metadata shown by Updates Manager and the running plugin agree and advance.
local env=setmetatable({require=function(name)
    if name=='gettext' then return function(text) return text end end
    if name=='ui/widget/container/widgetcontainer' then return {extend=function(_,fields) return fields end} end
    if name=='device' then return {isKindle=function() return false end,isKobo=function() return false end} end
    if name=='device_identity' then return {settle=function() return false end} end
    if name=='ui/time' then return {s=function(value) return value end} end
    return {}
end},{__index=_G})
local plugin=assert(loadfile('../main.lua','t',env))()
local metadata=assert(loadfile('../_meta.lua','t',env))()
assert(plugin.version==metadata.version,'Updates Manager and running plugin advertise the same version')
local major,minor,patch=plugin.version:match('^(%d+)%.(%d+)%.(%d+)$')
assert(major and (tonumber(major)>4 or tonumber(major)==4 and
    (tonumber(minor)>1 or tonumber(minor)==1 and tonumber(patch)>47)),
    'identity protocol change advances the version beyond 4.1.47')
print('share-place plugin version test passed')

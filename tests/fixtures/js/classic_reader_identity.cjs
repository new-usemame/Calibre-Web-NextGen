const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const root=process.argv[2];
const classic=fs.readFileSync(root+'/cps/static/js/reading/device-identity.js','utf8');
const key='cwng.webreader.installation-id.v1',header='X-CWNG-Webreader-Installation-Id';
const existing='11111111-1111-4111-8111-111111111111',minted='22222222-2222-4222-8222-222222222222';
const base={'Content-Type':'application/json','X-CSRFToken':'fixture-csrf'};
function setup(options={}){
 const storage=new Map(options.stored===undefined?[]:[[key,options.stored]]);let mintedCount=0;
 const window={localStorage:{getItem:k=>{if(options.denyGet)throw Error('denied');return storage.get(k)||null},setItem:(k,v)=>{if(options.denySet)throw Error('denied');storage.set(k,v)}},crypto:options.noCrypto?{}:{randomUUID:()=>{mintedCount++;return minted}}};
 const context=vm.createContext({window,Headers});vm.runInContext(classic,context);
 return {classic:window.webreaderDeviceHeaders,storage,count:()=>mintedCount};
}
for(const [name,options,want] of [['existing SPA UUID ignored',{stored:existing},null],['no identifier minted',{},null],['invalid stored id ignored',{stored:'bad'},null],['denied get',{denyGet:true},null],['denied set',{denySet:true},null],['crypto unavailable', {noCrypto:true},null],['existing survives crypto unavailable',{stored:existing,noCrypto:true},null]]){
 const h=setup(options);for(let i=0;i<2;i++){const result=h.classic(base);assert.equal(result.get(header),want,name);assert.equal(result.get('X-CSRFToken'),base['X-CSRFToken']);assert.equal(result.get('Content-Type'),base['Content-Type'])}
 assert.equal(h.count(),0, 'account source never mints an installation id');console.log('PASS',name,'preserves base headers');
}

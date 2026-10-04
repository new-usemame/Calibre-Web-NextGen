import assert from 'node:assert/strict';
import test from 'node:test';
import {selectedCustomColumns,customFieldsForSave,formatCustomColumnDate} from '../src/lib/customColumnDisplay.ts';
const definitions=[{id:12,name:'Difficulty',datatype:'int'},{id:13,name:'Score',datatype:'float'}];
test('guest display choices and personal labels follow the browser across compatible views',()=>{
 const values=new Map([['cwng:catalog-custom-fields-v1','[12]'],['cwng:catalog-custom-field-labels-v1','{"12":"My difficulty"}']]);
 Object.defineProperty(globalThis,'localStorage',{configurable:true,value:{getItem:(key:string)=>values.get(key)??null}});
 try {assert.deepEqual(selectedCustomColumns(definitions as any,null),[{id:12,name:'My difficulty',datatype:'int'}]);
 assert.deepEqual(selectedCustomColumns(definitions as any,{role:{anonymous:false},catalog:{custom_field_ids:[13],custom_field_labels:{'13':'My score'}}} as any),[{id:13,name:'My score',datatype:'float'}]);
 } finally {delete (globalThis as any).localStorage;}
});

test('saved stale IDs and labels are pruned to the current server-owned fields',()=>{
 assert.deepEqual(customFieldsForSave([definitions[1]] as any,[12,13],{'12':'Old label','13':'My score'}),{custom_column_ids:[13],custom_column_labels:{'13':'My score'}});
});

test('Calibre custom calendar dates do not shift with the browser timezone',()=>{
 const previous=process.env.TZ;
 try {for(const zone of ['America/Los_Angeles','Asia/Tokyo']){
  process.env.TZ=zone;
  for(const value of ['2026-01-10T00:00:00+00:00','2026-01-10T23:30:00+00:00'])
   assert.equal(formatCustomColumnDate(value,'en-US'),'1/10/2026',`${zone}: ${value}`);
  assert.equal(formatCustomColumnDate('2024-02-29','en-US'),'2/29/2024');
  assert.equal(formatCustomColumnDate('not-a-date','en-US'),'not-a-date');
  assert.equal(formatCustomColumnDate('2026-02-30','en-US'),'2026-02-30');
 }} finally {if(previous===undefined)delete process.env.TZ;else process.env.TZ=previous;}
});

test('Calibre no-date sentinel stays blank while detail can request the long calendar form',()=>{
 assert.equal(formatCustomColumnDate('0101-01-01T00:00:00+00:00','en-US'),'');
 assert.equal(formatCustomColumnDate('0099-12-31','en-US'),'');
 assert.equal(formatCustomColumnDate('2026-01-10','en-US',{year:'numeric',month:'long',day:'numeric'}),'January 10, 2026');
 assert.equal(formatCustomColumnDate('2026-01-10','de'),'10.1.2026');
 assert.equal(formatCustomColumnDate('2026-01-10','zh_Hans_CN'),'2026/1/10');
});

test('legacy invalid interface locale does not break a dated card or detail',()=>{
 for(const locale of ['C','en_US.UTF-8'])
  assert.equal(formatCustomColumnDate('2026-01-10',locale),formatCustomColumnDate('2026-01-10'));
});

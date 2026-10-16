"""Additive source-free published ownership contract; original nine unchanged."""
import importlib.util,json
from pathlib import Path
_spec=importlib.util.spec_from_file_location('frozen_native_list_v3',Path(__file__).with_name('test_round7_native_list_source_continuation.py'))
f=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(f)
def test_every_original_list_item_keeps_exact_source_page_block_and_regions(tmp_path):
 r=f.publish(tmp_path,'plain')
 proof=json.loads((tmp_path/'synthetic-source-proof.json').read_text())
 def check(item,text,expected_pages,expected_rows):
  owners=[]
  for n in item.iter():
   if f.tag(n)!='span' or not ''.join(n.itertext()).strip():continue
   if 'data-source-page' not in n.attrib:continue
   assert n.attrib.get('data-source-block'),'Printed native list text must retain its admitted source block'
   regions=json.loads(n.attrib.get('data-source-regions','null'))
   assert isinstance(regions,list) and regions and all(isinstance(b,list) and len(b)==4 and b[0]<b[2] and b[1]<b[3] for b in regions),'Printed native list text must retain positive original regions'
   owners.append((int(n.attrib['data-source-page']),n,regions))
  assert {p for p,n,b in owners}==set(expected_pages),'Every source-owned list item must carry all and only its original page identities'
  assert ' '.join(' '.join(''.join(n.itertext()).split()) for p,n,b in owners)==text,'Original item words are attributed exactly once'
  for row in expected_rows:
   b=row['bbox'];matching=[(p,n,boxes) for p,n,boxes in owners if p==row['pno'] and any(q[0]<=b[0]+.1 and q[1]<=b[1]+.1 and q[2]>=b[2]-.1 and q[3]>=b[3]-.1 for q in boxes)]
   assert len(matching)==1,'Each original printed row has one geometrically corresponding published owner'
 def rows_for(text):return [row for row in proof['rows'] if row['text']==text]
 first=next(n for n in r['lists'] if f.plain(n)==f.FIRST)
 check(first,f.FIRST,[0],rows_for(f.FIRST))
 joined=r['left']+' '+r['suffix']
 continued=next(n for n in r['lists'] if f.plain(n)==joined)
 check(continued,joined,[0,1],rows_for(r['left'])+rows_for(r['suffix']))
 third=next(n for n in r['lists'] if f.plain(n)==f.NEXT)
 check(third,f.NEXT,[1],rows_for(f.NEXT))

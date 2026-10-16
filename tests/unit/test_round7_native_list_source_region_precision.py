"""Additive per-item region precision, frozen after C41 code and before its tests."""
import importlib.util,json
from pathlib import Path
_s=importlib.util.spec_from_file_location('frozen_native_list_v3',Path(__file__).with_name('test_round7_native_list_source_continuation.py'));f=importlib.util.module_from_spec(_s);_s.loader.exec_module(f)
def test_list_item_source_regions_do_not_claim_sibling_item_or_neighbor_rows(tmp_path):
 r=f.publish(tmp_path,'plain');proof=json.loads((tmp_path/'synthetic-source-proof.json').read_text())
 expected=[(f.FIRST,[f.FIRST]),(r['left']+' '+r['suffix'],[r['left'],r['suffix']]),(f.NEXT,[f.NEXT])]
 def overlap(a,b):return min(a[2],b[2])>max(a[0],b[0]) and min(a[3],b[3])>max(a[1],b[1])
 for text,own_texts in expected:
  item=next(n for n in r['lists'] if f.plain(n)==text)
  owners=[n for n in item.iter() if f.tag(n)=='span' and n.attrib.get('data-source-page') is not None and ''.join(n.itertext()).strip()]
  assert owners,'Native item requires explicit original-source owner'
  for owner in owners:
   pno=int(owner.attrib['data-source-page']);regions=json.loads(owner.attrib.get('data-source-regions','null'))
   assert isinstance(regions,list) and regions,'Each item owner requires its own actual original row territory'
   own=[row['bbox'] for row in proof['rows'] if row['pno']==pno and row['text'] in own_texts]
   foreign=[row['bbox'] for row in proof['rows'] if row['pno']==pno and row['text'] not in own_texts]
   assert all(any(overlap(q,b) for b in own) for q in regions),'No original region may be unrelated to its attributed item words'
   assert not any(overlap(q,b) for q in regions for b in foreign),'Per-item source regions cannot claim sibling item or healthy neighbor printed rows'

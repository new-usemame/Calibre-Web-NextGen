"""SOURCE-free public marker/ordinary-disclosure evidence-sharing controls."""
import importlib.util,copy
from pathlib import Path
import pytest
P=Path(__file__).with_name('test_marker_noanchor_and_repeated_slot_v2.py')
s=importlib.util.spec_from_file_location('marker_slot_fixture',P);f=importlib.util.module_from_spec(s);s.loader.exec_module(f);m=f.m

def prepared(case):
 repeated=case=='two_markers';image,words,answer,glyphs=f.prepared(repeated)
 body=answer['blocks'][0]
 body['low_confidence']=[0,2] if repeated else [0] if case=='nonadjacent' else [1]
 # Lack of optical word anchors is not absence of original source pixels. The
 # original body/marker image remains actual synthetic ink. Ordinary uncertainty
 # then retains the whole original owning BODY territory; no target is guessed.
 if case!='tight_crop':words=[w for w in words if w['bbox'][1]>300]
 else:
  # This case needs a genuinely unsettled ordinary word beside the marker.
  # A confident exact baseline would already settle the reader's B flag;
  # a fused recognition of neighbouring ink cannot manufacture a B conflict.
  for word in words:
   if word['text']=='B':word['confidence']=40
 return image,words,answer,glyphs

def controls(docs):return [(name,a)for name,root in docs.items()for a in root.findall('.//x:a',m.h.NS)if (a.get('class')=='source-disclosure-ref'and not a.get('href','').startswith('#v4-d-unit-print-'))or(a.get('title')=='Compare this reading with original print'and a.get('id'))]
def target(docs,name,a):
 dest,ident=m.h.destination(name,a.get('href'));return dest,next(n for n in docs[dest].iter()if n.get('id')==ident)
def covers(a,b):return all(a[i]<=b[i]+.001 for i in(0,1))and all(a[i]>=b[i]-.001 for i in(2,3))
def original_returns(docs,images,refs):
 for name,a in refs:
  dest,node=target(docs,name,a)
  assert any('OEBPS/'+img.get('src')in images for img in node.findall('.//x:img',m.h.NS))
  assert any(m.h.destination(dest,b.get('href'))==(name,a.get('id'))for b in node.findall('.//x:a',m.h.NS)if b.get('href')and '#'in b.get('href'))

@pytest.mark.parametrize('case',['complete_adjacent','tight_crop','nonadjacent','two_markers'])
def test_shared_marker_context_needs_actual_image_coverage_and_adjacency_and_keeps_each_unit(case,tmp_path,monkeypatch):
 report,docs,images,observed=m.convert(prepared(case),tmp_path,monkeypatch)
 repeated=case=='two_markers';count=2 if repeated else 1
 refs,notes=m.h.assert_exact_returns(docs,count)
 assert all(a.get('href').endswith('#v4-note-0-n1')for _,a in refs)
 assert m.h.body_texts(docs)==['Ruby gleams. Ruby gleams.' if repeated else 'A B I J.']
 listed=report.get('note_marker_disclosures',[])
 assert len(listed)==count and len({c['block']for c in listed})==count,'Every marker remains a distinct explicitly counted uncertainty unit, even when imagery is shared'
 assert all(c['page']==0 and c['status']=='DEGRADED_SOURCE_CONTEXT'and c['unit_kind']=='note_marker'for c in listed)
 actual=controls(docs);ordinary=[i for i in report['images'].values()if i['kind']=='vision_disclosure']
 expected=count if case in('complete_adjacent','two_markers')else 2
 assert len(actual)==expected,'Only a directly adjacent existing disclosure with complete ACTUAL original image coverage may replace a redundant extra marker control'
 assert report['disclosure_count']==expected,'Visible controls must remain explicitly counted, not double-counted or silently suppressed'
 original_returns(docs,images,actual)
 for c in listed:
  assert c['source_reference_origin']in {a.get('id')for _,a in actual}
  name,a=next((name,a)for name,a in actual if a.get('id')==c['source_reference_origin'])
  dest,node=target(docs,name,a);assert node.get('id')==c['source_reference']
  img=next(node.iter('{http://www.w3.org/1999/xhtml}img'));record=report['images'][img.get('src')]
  assert covers(record['bbox'],c['source_bbox']),'Marker context must be covered by actual emitted image bbox, not just nominal owner/block hints'
  if case in('complete_adjacent','two_markers'):
   assert record['kind']=='vision_disclosure','Sharing must reuse the existing ordinary disclosure image rather than write a duplicate SOURCE image'
   wording=' '.join(''.join(p.itertext())for p in node.findall('.//x:p',m.h.NS)).lower()
   assert 'marker'in wording and 'uncertain'in wording,'Shared original-image target visibly discloses the additional marker uncertainty'
 if repeated:assert len({c['source_reference_origin']for c in listed})==2,'Each of these two physically separate marker slots has its own directly adjacent ordinary source control'
 inspection={k:v for k,v in report['images'].items()if v['kind']not in('inline_print_preview','flagged_unit_preview')}
 assert len(inspection)==expected,'No unreferenced duplicate source raster may remain after sharing'
 emitted={n.get('src')for root in docs.values()for n in root.iter()if n.tag.endswith('}img')}
 for name,preview in report['images'].items():
  if preview['kind']=='flagged_unit_preview':
   assert name in emitted and preview['coverage_eligible']is False
  if preview['kind']=='inline_print_preview':
   assert name in emitted and preview['coverage_eligible']is False
   assert preview['inspection_image']in inspection

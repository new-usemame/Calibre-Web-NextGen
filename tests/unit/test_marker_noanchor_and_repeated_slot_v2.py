"""Independent public SOURCE marker ambiguity controls; synthetic pixels only."""
import importlib.util
from pathlib import Path
import pytest,pymupdf
P=Path(__file__).with_name('test_source_placeholder_display_marker_v2.py')
s=importlib.util.spec_from_file_location('independent_marker_fixture',P);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)

def prepared(repeated=False):
 p=m.h.InkPage();model=[];glyphs=[]
 for y in ([55,80] if repeated else [55]):
  prefix='Ruby' if repeated else 'A B';following=' gleams.' if repeated else ' I J.'
  p.page.insert_text((24,y),prefix,fontsize=9);x=24+pymupdf.get_text_length(prefix,fontsize=9)
  p.page.insert_text((x,y-4),'7',fontsize=5);glyphs.append(dict(label='7',bbox=m.source_ink(p.page,[x-.2,y-10,x+4,y-2]),kind='bare'))
  p.page.insert_text((x+6,y),following,fontsize=9);model.append(prefix+'<n:n1>'+following)
 p.blocks.append(m.h.model_block(' '.join(model),'body','body',[23,41,282,85]))
 p.text('33 '+m.NOTE,'n1','footnote',y=330,size=7,marker='33',model=m.NOTE)
 image,words,answer=p.finish();return image,words,answer,glyphs

@pytest.mark.parametrize('repeated',[False,True],ids=['zero-lexical-slot-anchors','two-repeated-physical-slots'])
def test_unlocated_or_repeated_source_slots_cannot_silently_borrow_footer_display_label(tmp_path,monkeypatch,repeated):
 report,docs,images,observed=m.convert(prepared(repeated),tmp_path,monkeypatch)
 refs,notes=m.h.assert_exact_returns(docs,2 if repeated else 1)
 assert all(ref.get('href').endswith('#v4-note-0-n1') for _,ref in refs),'Correct known note owner remains unchanged despite uncertain body glyph display'
 expected='Ruby gleams. Ruby gleams.' if repeated else 'A B I J.'
 assert m.h.body_texts(docs)==[expected],'Every source BODY lexical word remains exactly once'
 disclosures=report.get('note_marker_disclosures',[])
 assert len(disclosures)==(2 if repeated else 1),'Each placeholder without a uniquely proved original slot requires a counted SOURCE marker uncertainty record, even when zero two-letter anchors exist'
 controls=[(name,a) for name,root in docs.items() for a in root.findall('.//x:a',m.h.NS) if a.get('class')=='source-disclosure-ref']
 assert len(controls)>=(2 if repeated else 1),'Footer/catalogue label may not silently certify body display when source slot proof is unavailable'
 for case in disclosures:
  assert case['page']==0 and case['block'].startswith('marker-body-') and case['block'].removeprefix('marker-body-').isdecimal() and case['status']=='DEGRADED_SOURCE_CONTEXT' and case['unit_kind']=='note_marker'
  name,a=next((name,a) for name,a in controls if a.get('id')==case['source_reference_origin'])
  dest,target=m.h.destination(name,a.get('href'));node=next(n for n in docs[dest].iter() if n.get('id')==target)
  assert any('OEBPS/'+img.get('src') in images for img in node.findall('.//x:img',m.h.NS))
  assert any(m.h.destination(dest,b.get('href'))==(name,a.get('id')) for b in node.findall('.//x:a',m.h.NS) if b.get('href') and '#'in b.get('href'))

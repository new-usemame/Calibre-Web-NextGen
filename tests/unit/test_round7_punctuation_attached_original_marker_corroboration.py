"""Source-free actual marker actions require punctuation-aware independent original corroboration."""
import copy,importlib.util,re
from pathlib import Path
import pymupdf,pytest
p=Path(__file__).with_name('test_source_placeholder_display_marker_v2.py')
s=importlib.util.spec_from_file_location('readonly_original_marker_fixture',p);h=importlib.util.module_from_spec(s);s.loader.exec_module(h)

def ink_fixture(punctuation='.',candidate='*',raised=True):
 p=h.h.InkPage();font=pymupdf.Font('cjk');p.page.insert_font(fontname='unicode',fontbuffer=font.buffer)
 prefix='A bright ruby'+punctuation;x=24+pymupdf.get_text_length(prefix,fontsize=9)
 p.page.insert_text((24,55),prefix,fontsize=9)
 glyph='*'if raised else '3';size=9;baseline=55
 p.page.insert_text((x,baseline),glyph,fontsize=size,fontname='unicode')
 width=font.text_length(glyph,fontsize=size)
 candidate_box=h.source_ink(p.page,[x-.2,baseline-size*1.5,x+width+.5,baseline+2])
 p.page.insert_text((x+width+3,55),' carries clear light.',fontsize=9)
 p.page.insert_text((24,69),'Every healthy word remains once.',fontsize=9)
 expected=prefix+('3'if not raised else '')+' carries clear light. Every healthy word remains once.'
 model=prefix+('3'if not raised else '')+'<n:n1> carries clear light. Every healthy word remains once.'
 p.blocks.append(h.h.model_block(model,'body','body',[23,38,282,74]))
 p.page.insert_text((24,330),'33 '+h.NOTE,fontsize=7,fontname='unicode')
 p.blocks.append(h.h.model_block(h.NOTE,'n1','footnote',[23,318,282,335],marker='33'))
 image,words,answer=p.finish()
 assert any(w['bbox'][1]<80 and w['text'].endswith(punctuation+glyph)for w in words),'Actual synthetic optical whitespace word must retain printed terminal punctuation before the marker'
 return (image,words,answer,[dict(label=candidate,bbox=candidate_box,kind='bare')]),expected

def exact_owner_and_body(docs,expected):
 refs,notes=h.h.assert_exact_returns(docs,1);assert len(refs)==1
 assert refs[0][1].get('href').endswith('#v4-note-0-n1')
 assert h.NOTE in ' '.join(''.join(n.itertext())for _,n in notes)
 assert h.h.body_texts(docs)==[expected],'Punctuation and all ordinary BODY words must remain once'
 return ''.join(refs[0][1].itertext())

@pytest.mark.parametrize('punctuation',['.',')','.)'])
def test_concordant_original_optical_punctuation_suffix_needs_no_bounded_read(punctuation,tmp_path,monkeypatch):
 prepared,expected=ink_fixture(punctuation)
 report,docs,images,_=h.convert(prepared,tmp_path,monkeypatch,marker_response={'label':'†','uncertain':False,'request_dispatched':False})
 assert exact_owner_and_body(docs,expected)=='*','Concordant original optical+raised glyph must preserve its own display, independent of a footer or unrelated bounded-reader answer'
 assert report['pages'][0].get('marker_readings',[])==[],'Already independently corroborated original glyph does not need another marker request'
 assert any(r.get('status')=='PROVED'and r.get('label')=='*'and r.get('method')=='unique_raised_original_glyph_at_exact_lexical_slot'for r in report['note_marker_repairs'])
 assert report['note_marker_disclosures']==[]

@pytest.mark.parametrize('usable',[True,False])
def test_punctuation_suffix_conflicting_character_still_verifies_or_discloses(usable,tmp_path,monkeypatch):
 prepared,expected=ink_fixture('.',candidate='3')
 report,docs,images,_=h.convert(prepared,tmp_path,monkeypatch,marker_response={'label':'*'if usable else '', 'uncertain':not usable,'request_dispatched':False})
 label=exact_owner_and_body(docs,expected)
 assert not any(r.get('status')=='PROVED'and r.get('method')=='unique_raised_original_glyph_at_exact_lexical_slot'for r in report['note_marker_repairs'])
 assert report['pages'][0].get('marker_readings',[]),'Conflicting independent optical/character identities require actual bounded verification'
 if usable:
  assert label=='*'
  assert any(r.get('status')=='PROVED'and r.get('method')=='bounded_original_slot_verification'and r.get('label')=='*'for r in report['note_marker_repairs'])
  assert report['note_marker_disclosures']==[]
 else:
  assert report['note_marker_disclosures']
  controls=[(name,a)for name,root in docs.items()for a in root.findall('.//x:a',h.h.NS)if a.get('class')=='source-disclosure-ref']
  assert controls
  for name,a in controls:
   target_file,target=h.h.destination(name,a.get('href'));nodes=[n for n in docs[target_file].iter()if n.get('id')==target];assert len(nodes)==1
   assert any('OEBPS/'+img.get('src')in images for img in nodes[0].findall('.//x:img',h.h.NS))
   assert any(h.h.destination(target_file,back.get('href'))==(name,a.get('id'))for back in nodes[0].findall('.//x:a',h.h.NS)if '#'in(back.get('href')or ''))

def test_baseline_lexical_digit_is_not_promoted_to_original_raised_marker(tmp_path,monkeypatch):
 prepared,expected=ink_fixture('.',candidate='3',raised=False)
 report,docs,images,_=h.convert(prepared,tmp_path,monkeypatch,marker_response={'label':'','uncertain':True,'request_dispatched':False})
 exact_owner_and_body(docs,expected)
 assert not any(r.get('status')=='PROVED'and r.get('method')=='unique_raised_original_glyph_at_exact_lexical_slot'for r in report['note_marker_repairs']),'Ordinary baseline lexical digit cannot supply raised-marker proof'
 assert report['note_marker_disclosures'],'Unbound placeholder preserves actual original evidence rather than guessing'

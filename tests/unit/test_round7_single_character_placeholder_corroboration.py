"""Synthetic SOURCE marker identity needs independent corroboration, not a sole character OCR answer."""
import copy,importlib.util,re
from pathlib import Path
import pytest
p=Path(__file__).with_name('test_source_placeholder_display_marker_v2.py')
s=importlib.util.spec_from_file_location('original_readonly_marker_fixture',p);h=importlib.util.module_from_spec(s);s.loader.exec_module(h)

def original_one(candidate='1',optical=True):
 prepared=list(h.fixture('1','1'));prepared[1]=copy.deepcopy(prepared[1]);prepared[3]=copy.deepcopy(prepared[3])
 assert len(prepared[3])==1 and prepared[3][0]['kind']=='bare' and prepared[3][0]['label']=='1'
 if not optical:
  words=[]
  for word in prepared[1]:
   if word['bbox'][1]<80:
    if word['text']=='1':continue
    word['text']=re.sub(r'(?<=[A-Za-z])1(?=[.,;:]*$)','',word['text'])
   words.append(word)
  prepared[1]=words
  assert not any(w['text']=='1'and w['bbox'][1]<80 for w in words)
 # Only the sole OCR character answer changes; its source rectangle and genuine
 # printed original1 pixels remain exactly those from the unmodified fixture.
 original_box=copy.deepcopy(prepared[3][0]['bbox']);prepared[3][0]['label']=candidate
 assert prepared[3][0]['bbox']==original_box
 return tuple(prepared)

def owner_and_label(docs):
 refs=h.assert_owner_and_body(docs);assert len(refs)==1
 return ''.join(refs[0][1].itertext())

def bounded_proof(report,label):
 assert any(r.get('label')==label and r.get('uncertain')is False for r in report['pages'][0].get('marker_readings',[])), 'A changed label without optical corroboration needs its actual bounded verification receipt'
 assert any(r.get('status')=='PROVED'and r.get('label')==label and r.get('method')=='bounded_original_slot_verification'for r in report['note_marker_repairs']), 'The report must distinguish bounded verification from a single uncorroborated OCR character'

@pytest.mark.parametrize('candidate',['3','1'])
def test_single_uncorroborated_character_with_unusable_verification_is_disclosed(candidate,tmp_path,monkeypatch):
 report,docs,images,_=h.convert(original_one(candidate,False),tmp_path,monkeypatch,marker_response={'label':'','uncertain':True,'request_dispatched':False})
 owner_and_label(docs)
 assert not any(r.get('status')=='PROVED'and r.get('method')=='unique_raised_original_glyph_at_exact_lexical_slot'for r in report.get('note_marker_repairs',[])), 'A sole uncorroborated character OCR result cannot certify printed marker identity'
 h.assert_visible_source_uncertainty(report,docs,images)
 assert any(r.get('status')=='DEGRADED_SOURCE_CONTEXT'and r.get('note_owner')==[0,'n1']and r.get('reason','').endswith('original_marker_verification_unusable')for r in report['note_marker_disclosures'])

@pytest.mark.parametrize('candidate',['3','1'])
def test_single_uncorroborated_character_uses_confident_bounded_original_verification(candidate,tmp_path,monkeypatch):
 report,docs,images,_=h.convert(original_one(candidate,False),tmp_path,monkeypatch,marker_response={'label':'1','uncertain':False,'request_dispatched':False})
 assert owner_and_label(docs)=='1', 'The original slot verification must determine display; neither bare OCR answer nor footer supplies missing proof'
 bounded_proof(report,'1')
 assert report['note_marker_disclosures']==[]

def test_concordant_independent_optical_and_character_observations_remain_deterministic(tmp_path,monkeypatch):
 report,docs,images,_=h.convert(original_one('1',True),tmp_path,monkeypatch,marker_response={'label':'','uncertain':True,'request_dispatched':False})
 assert owner_and_label(docs)=='1'
 assert any(r.get('status')=='PROVED'and r.get('label')=='1'and r.get('method')=='unique_raised_original_glyph_at_exact_lexical_slot'for r in report['note_marker_repairs'])
 assert report['note_marker_disclosures']==[] and report['pages'][0].get('marker_readings',[])==[]

def test_conflicting_independent_optical_evidence_still_requires_bounded_verification(tmp_path,monkeypatch):
 report,docs,images,_=h.convert(original_one('3',True),tmp_path,monkeypatch,marker_response={'label':'1','uncertain':False,'request_dispatched':False})
 assert owner_and_label(docs)=='1'
 bounded_proof(report,'1')
 assert report['note_marker_disclosures']==[]

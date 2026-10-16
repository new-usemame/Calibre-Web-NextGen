"""Synthetic original ink, not matching partial OCR answers, proves whole markers."""
import copy,importlib.util,re
from pathlib import Path
import pytest
from cps.services.reflow import vision_pipeline as vp
fixture=Path(__file__).with_name('test_source_placeholder_display_marker_v2.py')
s=importlib.util.spec_from_file_location('complete_original_marker_fixture',fixture);h=importlib.util.module_from_spec(s);s.loader.exec_module(h)

def damaged(original,candidate,partial):
 image,words,answer,glyphs=h.fixture(original,'9');words=copy.deepcopy(words);full=list(glyphs[0]['bbox']);candidatebox=list(full)
 if partial:candidatebox[0]=(full[0]+full[2])/2
 # Optical token error deliberately shares the same incomplete reading with
 # bare-character OCR. Its alleged lexical anchor includes original raised ink.
 cleaned=[];anchors=0
 for w in words:
  if w['bbox'][1]<80:
   if re.fullmatch(r'\d+|[*†‡§¶]',w['text']):continue
   if 'ruby' in w['text']:
    w['text']='ruby'+candidate;w['bbox'][2]=candidatebox[2];anchors+=1
  cleaned.append(w)
 assert anchors==1 and full[0]<candidatebox[2]
 observed=[dict(label=candidate,bbox=candidatebox,kind='bare')]
 return (image,cleaned,answer,observed),full

def run(prepared,tmp_path,monkeypatch,response):
 original=vp._placeholder_display_evidence;calls=[]
 def observe(tokens,optical,display,box,context,pno,literal=None):
  reader=context.get('marker_reader')
  if reader:
   def bounded(image):calls.append(len(image));return reader(image)
   context=dict(context,marker_reader=bounded)
  return original(tokens,optical,display,box,context,pno,literal=literal)
 monkeypatch.setattr(vp,'_placeholder_display_evidence',observe)
 report,docs,images,_=h.convert(prepared,tmp_path,monkeypatch,marker_response=response)
 refs=h.assert_owner_and_body(docs);assert len(refs)==1
 return report,docs,images,''.join(refs[0][1].itertext()),calls

@pytest.mark.parametrize('original,candidate,partial',[('13','3',True),('33','*',False)])
def test_matching_partial_or_wrong_component_readings_verify_complete_original_slot(original,candidate,partial,tmp_path,monkeypatch):
 prepared,whole=damaged(original,candidate,partial)
 report,docs,images,label,calls=run(prepared,tmp_path,monkeypatch,{'label':original,'uncertain':False,'request_dispatched':False})
 assert label==original,'Partial optical/character concordance cannot replace complete independently printed marker'
 assert len(calls)==1,'One complete bounded original-slot verification supplies missing identity; no duplicate verification'
 repairs=report.get('note_marker_repairs',[])
 assert any(r.get('status')=='PROVED'and r.get('label')==original and r.get('method')=='bounded_original_slot_verification'for r in repairs)
 readings=report['pages'][0].get('marker_readings',[]);assert len(readings)==1
 b=readings[0]['source_bbox'];assert b[0]<=whole[0] and b[1]<=whole[1] and b[2]>=whole[2] and b[3]>=whole[3],'Verification must cover whole original raised component group, including component omitted from alleged anchor'
 assert report.get('note_marker_disclosures',[])==[]

@pytest.mark.parametrize('original,candidate,partial',[('13','3',True),('33','*',False)])
def test_unusable_complete_slot_verification_discloses_without_false_proof(original,candidate,partial,tmp_path,monkeypatch):
 prepared,whole=damaged(original,candidate,partial)
 report,docs,images,label,calls=run(prepared,tmp_path,monkeypatch,{'label':'','uncertain':True,'request_dispatched':False})
 assert len(calls)==1,'Known incomplete or multicomponent marker gets only one bounded verification'
 assert not any(r.get('status')=='PROVED'for r in report.get('note_marker_repairs',[])),'No catalogue or mutually agreeing partial OCR answer may fabricate complete marker proof'
 h.assert_visible_source_uncertainty(report,docs,images)
 readings=report['pages'][0].get('marker_readings',[]);assert len(readings)==1
 b=readings[0]['source_bbox'];assert b[0]<=whole[0] and b[2]>=whole[2],'Uncertainty evidence cannot hide the omitted original marker component'

@pytest.mark.parametrize('marker',['7','*'])
def test_genuine_complete_single_glyph_concordance_remains_deterministic(marker,tmp_path,monkeypatch):
 report,docs,images,label,calls=run(h.fixture(marker,'9'),tmp_path,monkeypatch,{'label':'','uncertain':True,'request_dispatched':False})
 assert label==marker and calls==[],'Complete independent optical/character ink may remain deterministic'
 assert any(r.get('status')=='PROVED'and r.get('label')==marker and r.get('method')=='unique_raised_original_glyph_at_exact_lexical_slot'for r in report.get('note_marker_repairs',[]))
 assert report.get('note_marker_disclosures',[])==[]

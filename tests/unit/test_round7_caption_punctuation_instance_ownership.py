"""Synthetic caption punctuation boundaries prove local SOURCE instance ownership.

Synthetic text exercises converter/publication controls without requests or glyph mocks.
Punctuation may separate complete lexical atoms; letters never invent a boundary.
"""
import importlib.util,json
from pathlib import Path
import pytest
from cps.services.reflow import vision_pipeline
_HELPER=Path(__file__).with_name('test_round4_defects.py')
_spec=importlib.util.spec_from_file_location('round7_caption_boundary_source',_HELPER)
h=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(h)
pytestmark=pytest.mark.unit
BODY='The independent notebook preserves the complete authored observation.'
HEAD='Annotated diagram identifies'
SOURCE='Vale.(P.minor,Q.); Bay,Delta. Clay,Sand. East,West. Upper,Lower. Front,Back. Fine,Coarse. Deep,Shallow.'
READING='Vale. (P. minor, Q.); Bay, Delta. Clay, Sand. East, West. Upper, Lower. Front, Back. Fine, Coarse. Deep, Shallow.'
CAPTION=HEAD+' '+READING
OMITTED='This separately printed omitted body sentence retains its entire independent source wording.'


def fixture(kind='ordinary'):
 p=h.InkPage();p.text(BODY,'body',y=50,size=6);p.figure(caption=False)
 def caption_copy(y=190,head=HEAD,tail=SOURCE,split=False):
  p.page.insert_text((22,y),head,fontsize=4)
  lines=[' '.join(tail.split()[:4]),' '.join(tail.split()[4:])] if split else [tail]
  for n,line in enumerate(lines):p.page.insert_text((22,y+12+n*10),line,fontsize=4)
 caption_copy(split=kind=='adjacent')
 reading=CAPTION;hint=[20,182,160 if kind=='adjacent' else 280,194]
 if kind=='wrong_letter':reading=CAPTION.replace('Shallow.','Shallows.')
 if kind=='ambiguous':caption_copy(y=220);hint=[20,182,280,240]
 if kind=='stale':hint=[20,310,280,325]
 if kind=='plain_word':
  # Retain the real independent figure, but use a clean page for the caption ink.
  p=h.InkPage();p.text(BODY,'body',y=50,size=6);p.figure(caption=False)
  p.page.insert_text((22,190),'Measured shoreline diagram',fontsize=4)
  p.page.insert_text((22,202),'seashore',fontsize=4)
  reading='Measured shoreline diagram sea shore'
 if kind=='omitted_body':p.page.insert_text((22,270),OMITTED,fontsize=4)
 if kind=='repeated_body':p.text(SOURCE,'independent_body','body',y=280,size=4)
 if kind=='remote_body':
  p=h.InkPage();p.text(BODY,'body',y=50,size=6);p.figure(caption=False)
  p.page.insert_text((22,190),HEAD,fontsize=4)
  p.page.insert_text((22,280),SOURCE,fontsize=4)
 if kind=='adjacent':p.text(['Adjacent prose preserves its','own independently printed sentence.'],'adjacent_body',x=171,y=202,size=4)
 p.blocks.append(h.model_block(reading,'caption','caption',hint))
 return p,reading


def captions(docs):return [n for root in docs.values() for n in root.findall('.//x:p[@class="caption"]',h.NS)]
def cap_report(report):return next(b for b in report['pages'][0]['blocks'] if b['id']=='caption')
def repaired(report):return 'caption' in report['pages'][0]['pixel_bound_geometry_repairs']
def regions(docs):return [r for n in captions(docs) for s in n.findall('.//x:span',h.NS) for r in json.loads(s.get('data-source-regions','[]'))]


def test_unique_local_fused_punctuation_caption_is_owned_once_not_recovered_as_body(tmp_path,monkeypatch):
 p,reading=fixture();_,report,docs=h.convert_ink(tmp_path,monkeypatch,[p])
 assert [h.plain(n) for n in captions(docs)]==[reading]
 assert h.body_texts(docs)==[BODY],'the printed caption tail must not become a second classless BODY paragraph'
 assert repaired(report),'complete punctuation-separated SOURCE correspondence binds the caption'
 assert any(r[3]>200 for r in regions(docs)),'caption SOURCE ownership reaches the actual final printed row'
 assert not any(b['id'].startswith('recovered-') for b in report['pages'][0]['blocks'])


def test_caption_proof_does_not_suppress_genuinely_omitted_body(tmp_path,monkeypatch):
 p,reading=fixture('omitted_body');_,report,docs=h.convert_ink(tmp_path,monkeypatch,[p])
 assert [h.plain(n) for n in captions(docs)]==[reading]
 assert h.body_texts(docs)==[BODY,OMITTED]
 assert all(r[3]<225 for r in regions(docs))


def test_equal_words_in_separate_authored_body_remain_owned_by_that_instance(tmp_path,monkeypatch):
 p,reading=fixture('repeated_body');_,report,docs=h.convert_ink(tmp_path,monkeypatch,[p])
 assert [h.plain(n) for n in captions(docs)]==[reading]
 assert h.body_texts(docs)==[BODY,SOURCE],'matching spelling cannot delete or borrow another printed BODY occurrence'
 assert all(r[3]<225 for r in regions(docs))


def test_caption_row_territories_preserve_adjacent_prose(tmp_path,monkeypatch):
 p,reading=fixture('adjacent');_,report,docs=h.convert_ink(tmp_path,monkeypatch,[p])
 assert [h.plain(n) for n in captions(docs)]==[reading]
 assert h.body_texts(docs)==[BODY,'Adjacent prose preserves its own independently printed sentence.']
 assert regions(docs) and all(r[2]<169 for r in regions(docs))


@pytest.mark.parametrize('kind',['wrong_letter','ambiguous','stale','plain_word'])
def test_unproved_caption_cannot_gain_punctuation_instance_binding(tmp_path,monkeypatch,kind):
 p,_=fixture(kind);_,report,docs=h.convert_ink(tmp_path,monkeypatch,[p])
 assert not repaired(report),'changed letters, two local instances, stale hint or a plain intra-word space cannot prove caption equivalence'
 assert h.body_texts(docs).count(BODY)==1


def test_remote_unrepresented_body_cannot_be_relabelled_by_caption_matching(tmp_path,monkeypatch):
 p,_=fixture('remote_body');_,report,docs=h.convert_ink(tmp_path,monkeypatch,[p])
 assert not repaired(report),'a caption cannot relocate to a remote omitted BODY occurrence'
 assert h.body_texts(docs).count(SOURCE)==1,'the real omitted BODY row survives even when its wording resembles an unproved caption'

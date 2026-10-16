"""Damaged caption prefix cannot erase unique printed suffix ownership."""
import importlib.util,json
from pathlib import Path
import pytest
_HELPER=Path(__file__).with_name('test_round4_defects.py')
spec=importlib.util.spec_from_file_location('round7_caption_suffix_ink',_HELPER);h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
BODY='The independent notebook preserves the complete authored observation.'
PREFIX='Annotated diagram labels Workshop.'
PRINTED_PREFIX='Annotated diagram labels Warkshop.'
SOURCE='Vale.(P.minor,Q.); Bay,Delta. Clay,Sand. East,West. Upper,Lower. Front,Back. Fine,Coarse. Deep,Shallow.'
READING='Vale. (P. minor, Q.); Bay, Delta. Clay, Sand. East, West. Upper, Lower. Front, Back. Fine, Coarse. Deep, Shallow.'
CAPTION=PREFIX+' '+READING
OMITTED='This genuinely omitted prose retains every independent source word and its complete authored sentence.'


def fixture(kind):
 p=h.InkPage();p.text(BODY,'body',y=50,size=6);p.figure(caption=False)
 p.page.insert_text((22,190),PRINTED_PREFIX,fontsize=4)
 p.page.insert_text((150,198),'An unrelated artwork legend',fontsize=4)
 def suffix(y=207):
  words=SOURCE.split()
  for n,row in enumerate([words[:3],words[3:6],words[6:]]):p.page.insert_text((22,y+10*n),' '.join(row),fontsize=4)
 suffix(y=280 if kind=='remote' else 207)
 hint=[20,182,280,219]
 if kind=='ambiguous':suffix(y=247);hint[3]=258
 if kind=='omitted':p.page.insert_text((22,270),OMITTED,fontsize=4)
 if kind=='body_reserved':p.text(SOURCE,'independent_body','body',y=280,size=4)
 reading=CAPTION.replace('Shallow.','Shallows.') if kind=='altered' else CAPTION
 p.blocks.append(h.model_block(reading,'caption','caption',hint))
 return p,reading,hint


def caption_nodes(docs):return [p for root in docs.values() for p in root.findall('.//x:p[@class="caption"]',h.NS)]
def source_regions(docs):return [r for p in caption_nodes(docs) for s in p.findall('.//x:span',h.NS) for r in json.loads(s.get('data-source-regions','[]'))]


@pytest.mark.parametrize('kind',['ordinary','omitted','body_reserved'])
def test_exact_local_caption_suffix_survives_bad_prefix_without_body_duplication(tmp_path,monkeypatch,kind):
 p,reading,_=fixture(kind);_,report,docs=h.convert_ink(tmp_path,monkeypatch,[p])
 assert [h.plain(n) for n in caption_nodes(docs)]==[reading],'partial SOURCE ownership preserves model prefix without optical spelling substitution'
 expected=[BODY]+([OMITTED] if kind=='omitted' else [SOURCE] if kind=='body_reserved' else [])
 assert h.body_texts(docs)==expected,'the unique printed caption suffix cannot reappear as BODY; actual omitted/reserved BODY survives'
 assert any(r[3]>225 and r[3]<240 for r in source_regions(docs)),'SOURCE ownership reaches the exact adjacent third caption row'
 assert all(r[3]<240 for r in source_regions(docs)),'caption may not borrow later body instances'
 assert any(a.get('class')=='source-disclosure-ref' for n in caption_nodes(docs) for a in n.findall('.//x:a',h.NS)),'damaged unproved prefix retains its SOURCE uncertainty disclosure'


@pytest.mark.parametrize('kind',['ambiguous','altered','remote'])
def test_partial_suffix_proof_cannot_admit_ambiguous_changed_or_remote_instance(tmp_path,monkeypatch,kind):
 p,_,hint=fixture(kind);_,report,docs=h.convert_ink(tmp_path,monkeypatch,[p])
 assert 'caption' not in report['pages'][0]['pixel_bound_geometry_repairs'],'only unique complete caption-ending lexical correspondence can repair SOURCE geometry'
 assert all(r[3]<=hint[3] for r in source_regions(docs)),'unproved suffix cannot expand caption ownership'
 assert h.body_texts(docs).count(BODY)==1
 if kind=='remote':assert h.body_texts(docs).count(SOURCE)==1,'unrepresented remote BODY is retained, never stolen by a caption suffix'

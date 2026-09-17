# SPDX-License-Identifier: GPL-3.0-or-later
"""Source-preserving operations are legal choices, not leaked semantic answers."""
import base64
import copy
import hashlib
import json
import re
import zipfile
from dataclasses import asdict
from xml.etree import ElementTree as ET

import pymupdf
import pytest
from cps.services.reflow import assemble, build_epub, structural_ops as ops

pytestmark = pytest.mark.unit
X = '{http://www.w3.org/1999/xhtml}'


@pytest.fixture
def source(tmp_path):
    doc=pymupdf.open();page=doc.new_page(width=500,height=700)
    page.insert_text((50,65),'Learning the sky',fontsize=20)
    page.insert_text((50,105),'Attribution: First source sentence. Following ordinary prose.',fontsize=12)
    page.draw_circle((200,300),60)
    page.insert_text((130,385),'Figure 1. Original caption',fontsize=10)
    page.insert_text((50,610),'7 Uncertain original reference.',fontsize=10)
    path=tmp_path/'source.pdf';doc.save(path);doc.close();doc=pymupdf.open(path)
    elements=[assemble.Element(kind='p',pno=0,runs=[['t','Learning the sky']],bbox=(50,40,250,70)),
        assemble.Element(kind='p',pno=0,runs=[['t','Attribution: “First '],['t','source','italic'],
            ['t',' sentence.”'],['sup','7',0,'uncertain'],['t',' Following ordinary prose.']],bbox=(50,85,440,145)),
        assemble.Element(kind='h',pno=0,level=2,runs=[['t','Established heading']]),
        assemble.Element(kind='p',pno=0,runs=[['t','Immutable table row']],table_row=True),
        assemble.Element(kind='fig',pno=0,bbox=(135,235,265,365)),
        assemble.Element(kind='caption',pno=0,runs=[['t','Figure 1. Original caption','italic']],
                         bbox=(130,370,320,390),caption_uncertain=True)]
    book=assemble.Book(elements=elements,pages={0:elements},
        notes=[assemble.Note(num=7,text='Uncertain original reference.',pno=0,uncertain=True,bbox=(50,590,350,620))],
        figures=[{'pno':0,'bbox':(135,235,265,365),'found':'source','needs_ink':False}])
    yield book,doc,tmp_path
    doc.close()


def prepared(source, **kw):
    book,doc,_=source
    return ops.prepare(book,doc,0,'revision-1',{'layer':'native','gold':'must not leak'},seed=19,**kw)


def choose(p, element, kind, end=None):
    return next(c['candidate_id'] for c in p.candidates() if c['element_id']==element
                and c['kind']==kind and (end is None or c['source_range'][1]==end))


def response(p, ids):
    return {'protocol':ops.PROTOCOL,'snapshot_id':p.snapshot_id,'select':ids}


def texts(path):
    with zipfile.ZipFile(path) as z:
        bodies=[ET.fromstring(z.read(n)).find(X+'body') for n in sorted(z.namelist())
                if re.fullmatch(r'OEBPS/ch\d+\.xhtml',n)]
        return bodies


def test_uniform_choices_contain_real_pixels_and_wrong_but_legal_alternatives(source):
    p=prepared(source);view=p.model_view();c=view['candidates']
    assert len({tuple(sorted(x)) for x in c})==1
    assert not any(word in json.dumps(view) for word in ('abstention_controls','eligible','must not leak'))
    data=base64.b64decode(view['source_image']['data_url'].split(',',1)[1])
    assert data[:2]==b'\xff\xd8' and hashlib.sha256(data).hexdigest()==view['source_image']['sha256']
    assert choose(p,'e0','heading') and choose(p,'e1','heading'), 'ordinary body must also be a legal choice'
    end=len('Attribution: “First source sentence.”7')
    assert choose(p,'e1','quote',end)
    assert choose(p,'e1','quote',end+len(' Following ordinary prose.'))
    assert not any(x['element_id'] in ('e2','e3','e4','e5') for x in c), 'no-op or unsupported source structure'
    assert prepared(source).model_view()==view, 'frozen presentation must reproduce'


def test_real_epub_wrappers_keep_words_style_inventory_and_uncertain_evidence(source):
    book,doc,tmp=source;p=prepared(source);before=copy.deepcopy(asdict(book))
    quote_end=len('Attribution: “First source sentence.”7')
    ids=[choose(p,'e0','heading'),choose(p,'e1','quote',quote_end)]
    plan=p.accept(book,doc,response(p,ids))
    baseline=tmp/'baseline.epub';selected=tmp/'selected.epub'
    build_epub.build(book,str(baseline),doc=doc)
    build_epub.build(book,str(selected),doc=doc,operation_plans=[plan])
    assert asdict(book)==before, 'wrappers must not mutate source text, inventory or metadata'
    assert build_epub.validate(str(selected))==[]
    roots=texts(selected);headings=[h for r in roots for h in r.iter(X+'h2')]
    assert any(h.text=='Learning the sky' for h in headings)
    quotes=[q for r in roots for q in r.iter(X+'blockquote')];assert len(quotes)==1
    qt=''.join(quotes[0].itertext());assert 'First source sentence.' in qt and 'Following' not in qt
    assert any(e.text=='source' for e in quotes[0].iter(X+'em'))
    assert '(?)' in qt
    notes=[n for r in roots for n in r.iter(X+'aside')]
    assert any('original-p0000.xhtml#notes'==a.get('href') for n in notes for a in n.iter(X+'a'))
    assert any('original-p0000.xhtml#caption_0'==a.get('href') for r in roots for a in r.iter(X+'a'))
    words=lambda rs:re.findall(r'\w+', ' '.join(''.join(r.itertext()) for r in rs))
    assert words(roots)==words(texts(baseline)), 'meaningful token sequence changed'
    with zipfile.ZipFile(baseline) as a,zipfile.ZipFile(selected) as b:
        assert {n:a.read(n) for n in a.namelist() if n.endswith('.jpg')}=={
            n:b.read(n) for n in b.namelist() if n.endswith('.jpg')}


@pytest.mark.parametrize('attack',['unknown','stale','duplicate','overlap','prose','retarget_note','remove_evidence'])
def test_admission_rejects_invalid_operations_atomically(source,attack):
    book,doc,_=source;p=prepared(source);cid=choose(p,'e1','heading');r=response(p,[cid])
    if attack=='unknown':r['select']=['op-unknown']
    elif attack=='stale':r['snapshot_id']='stale'
    elif attack=='duplicate':r['select']=[cid,cid]
    elif attack=='overlap':r['select'].append(choose(p,'e1','quote'))
    elif attack=='prose':r['html']='<h2>invented words</h2>'
    elif attack=='retarget_note':r['note_number']=188
    else:r['remove_evidence']=True
    before=copy.deepcopy(asdict(book))
    with pytest.raises(ops.ContractError):p.accept(book,doc,r)
    assert asdict(book)==before


@pytest.mark.parametrize('metadata',['note','style','geometry'])
def test_a_cached_plan_cannot_erase_changed_source_metadata_at_build(source,metadata):
    book,doc,tmp=source;p=prepared(source);plan=p.accept(book,doc,response(p,[choose(p,'e0','heading')]))
    if metadata=='note':book.notes[0].uncertain=False
    elif metadata=='style':book.pages[0][1].runs[1][2]='changed-style'
    else:book.figures[0]['bbox']=(1,1,2,2)
    target=tmp/'stale.epub'
    with pytest.raises(ops.ContractError):build_epub.build(book,str(target),doc=doc,operation_plans=[plan])
    assert not target.exists()


def test_bounds_and_abstention_are_coverage_not_semantic_success(source):
    book,doc,tmp=source;p=prepared(source,max_candidates=2,max_context_chars=1000)
    coverage=p.model_view()['coverage']
    assert coverage['legal_choices_sent']<=2
    assert coverage['omitted_element_ids'] and coverage['unsupported_operation_kinds']
    assert 'source_ambiguous' not in coverage
    plan=p.accept(book,doc,response(p,[]));assert plan.compile(book,doc)=={}

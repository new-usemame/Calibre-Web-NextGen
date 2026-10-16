"""Paired public-converter safeguards, entirely synthetic original SOURCE."""
import importlib.util,re
from pathlib import Path
from xml.etree import ElementTree as ET
import pytest
from cps.services.reflow import assemble
P=Path(__file__).with_name('test_native_publication_source_ownership.py')
s=importlib.util.spec_from_file_location('frozen_native_publication_fixture',P);fixture=importlib.util.module_from_spec(s);s.loader.exec_module(fixture)
Synthetic,tag,words=fixture.Synthetic,fixture.tag,fixture.words

def test_reconstructing_source_rows_preserves_printed_and_authored_bold_style(tmp_path):
    f=Synthetic(tmp_path)
    try:
        a=f.row('The synthetic account retains emphasized wording',310,bold=True);b=f.row('and continues with ordinary source text.',330)
        e=f.element([a]);e.runs=[['t',a['text'],'bold']];f.element([b]);r=f.publish()
        assert sum(tag(n)=='strong' and words(n)==a['text'] for root in r.roots for n in root.iter())==1,'Exact SOURCE text correspondence cannot authorize deleting authored/native emphasis during paragraph repair'
    finally:f.close()

def test_source_authoritative_table_rows_cannot_be_flattened_as_continuous_prose(tmp_path):
    f=Synthetic(tmp_path)
    try:
        rows=[f.row('Cellalpha 17 Cellbeta 29',310),f.row('Cellgamma 31 Celldelta 43',330)]
        for row in rows:f.element([row]).table_row=True
        r=f.publish()
        assert all(sum(words(n)==row['text'] for n in r.paragraphs)==1 for row in rows),'Source table-row contract must retain separate cell-row associations, never flatten into one prose paragraph'
    finally:f.close()

def test_connected_vector_art_survives_a_separate_original_page_border(tmp_path):
    f=Synthetic(tmp_path)
    try:
        a=f.row('This paragraph introduces the complete vector graphic.',280);f.element([a]);f.art()
        b=f.row('This paragraph follows the independent complete graphic.',550);f.element([b]);f.page.draw_rect((20,20,580,780),color=(0,0,0),width=.8)
        r=f.publish();assert len(r.figures)==1,'Page-border veto must not suppress independently connected original artwork inside the page frame'
        img=next(n for n in r.figures[0].iter() if tag(n)=='img');record=r.report['images'][img.get('src')];box=record['bbox'];art=f.expected_art[0]
        assert all(box[i]<=art[i] if i<2 else box[i]>=art[i] for i in range(4))
        assert box[0]>20 and box[1]>20 and box[2]<580 and box[3]<780,'Recover the independent art, not the whole decorative frame as a primary graphic'
        fixture.image_rgb_equal_to_source(f,r,img.get('src'),box)
    finally:f.close()

def test_ambiguous_vector_context_has_exact_source_control_target_and_own_return(tmp_path):
    f=Synthetic(tmp_path)
    try:
        f.art();a=f.row('This readable body row crosses the vector territory.',380,x=105);f.element([a]);r=f.publish()
        nodes={n.get('id'):n for root in r.roots for n in root.iter() if n.get('id')}
        links=[n for root in r.roots for n in root.iter() if tag(n)=='a' and 'source-disclosure-ref' in n.get('class','')]
        assert links,'Unproved separation needs a visible source disclosure control'
        for link in links:
            target=nodes.get(link.get('href','').split('#')[-1]);assert target is not None,'Actual source-control target must resolve'
            assert any(tag(n)=='img' for n in target.iter()),'Disclosure target must retain complete original SOURCE image'
            back=[n for n in target.iter() if tag(n)=='a' and 'Return' in words(n)]
            assert any(n.get('href','').split('#')[-1]==link.get('id') for n in back),'SOURCE context needs explicit usable Return to its own originating source control'
        cases=r.report.get('degraded_figures',[])
        assert any(c.get('page')==0 and c.get('status')=='DEGRADED_SOURCE_CONTEXT' for c in cases),'Source context fallback must remain explicit in actual report, never quietly called live art'
    finally:f.close()

def test_native_printed_note_backlink_uses_printed_number_and_targets_its_own_origin(tmp_path,monkeypatch):
    f=Synthetic(tmp_path)
    try:
        value='The synthetic measurement has its own printed note 1 and continues.'
        row=f.row(value,310);e=f.element([row]);e.runs=[['t','The synthetic measurement has its own printed note '],['ref','1'],['t',' and continues.']]
        footer=f.row('1 This synthetic printed note contains its exact supporting explanation.',710)
        f.rows.remove(footer);f.expected_text.pop()
        original=assemble.Book
        def book_factory(*args,**kw):
            book=original(*args,**kw);book.notes=[assemble.Note(1,'This synthetic printed note contains its exact supporting explanation.',0,bbox=footer['bbox'])];return book
        monkeypatch.setattr(assemble,'Book',book_factory);r=f.publish()
        origins=[n for root in r.roots for n in root.iter() if tag(n)=='a' and n.get('{http://www.idpf.org/2007/ops}type')=='noteref']
        assert len(origins)==1,'Exactly one source note origin must be preserved'
        origin=origins[0];ids={n.get('id'):n for root in r.roots for n in root.iter() if n.get('id')};target=ids.get(origin.get('href','').split('#')[-1])
        assert target is not None and tag(target)=='aside','Printed note opens exact local note target'
        backlinks=[n for n in target.iter() if tag(n)=='a' and n.get('href','').split('#')[-1]==origin.get('id')]
        assert len(backlinks)==1,'Printed note must render exactly one back-link to its own origin'
        back=backlinks[0]
        assert back.get('class')=='note-return'
        assert back.get('aria-label')=='Return to note reference'
        assert ''.join(back.itertext())=='1','Back-link shows exactly the printed note number, without invented words'
        assert sum(tag(n)=='a' and n.get('class')=='note-return' for root in r.roots for n in root.iter())==1
    finally:f.close()

"""Independent synthetic native paragraph boundaries; original rows outrank cached fragments."""
import importlib.util
from pathlib import Path
import pymupdf,pytest
from cps.services.reflow import assemble
p=Path(__file__).with_name('test_native_publication_source_ownership.py')
s=importlib.util.spec_from_file_location('readonly_inline_native_fixture',p);f=importlib.util.module_from_spec(s);s.loader.exec_module(f)
Synthetic,tag,words,union=f.Synthetic,f.tag,f.words,f.union

def semantic_row(obj,y):
    prefix='The careful observer (letter ';letter='q';suffix=') keeps the same original explanation'
    x=70;gx=x+pymupdf.get_text_length(prefix,fontsize=12);sx=gx+pymupdf.get_text_length(letter,fontsize=7)
    obj.page.insert_text((x,y),prefix,fontsize=12)
    obj.page.insert_text((sx,y),suffix,fontsize=12)
    # Real raised original ink is appended AFTER baseline content in PDF order.
    obj.page.insert_text((gx,y-4),letter,fontsize=7)
    spans=[v for b in obj.page.get_text('dict')['blocks'] for ln in b.get('lines',[]) for v in ln['spans'] if y-18<v['bbox'][1]<y]
    logical=prefix+letter+suffix;r=dict(text=logical,bbox=union([v['bbox']for v in spans]),row=len(obj.rows),size=12,font='mixed-raised')
    obj.rows.append(r);obj.expected_text.append(logical)
    return r,[['t',prefix],['raised',letter],['t',suffix]]

def joined_once(result,exact):
    assert sum(tag(n)=='p'and words(n)==exact for n in result.paragraphs)==1,'The entire unique original continuous paragraph must have one published p parent'
    assert result.report['degraded_unit_count']==0,'Proved ordinary native continuity must retain live healthy prose'

@pytest.mark.parametrize('fragments',[3,4])
def test_transitive_original_adjacent_fragments_have_one_parent(tmp_path,fragments):
    obj=Synthetic(tmp_path)
    try:
        text=['The observer follows this ordinary unfinished account', '(with a parenthetical continuation that still belongs here),', 'and keeps each original row in the same paragraph', 'until the final physical row completes the explanation.'][:fragments]
        if fragments==3:text[-1]+='.'
        rows=[obj.row(t,310+i*20)for i,t in enumerate(text)]
        for r in rows:obj.element([r])
        result=obj.publish();joined_once(result,' '.join(text))
    finally:obj.close()

@pytest.mark.parametrize('incomplete',[False,True])
def test_raised_semantic_run_and_original_x_order_survive_continuation(tmp_path,incomplete):
    obj=Synthetic(tmp_path)
    try:
        a,runs=semantic_row(obj,310);b=obj.row('while the original unindented source row continues',330);c=obj.row('and ends the same ordinary paragraph.',350)
        e=obj.element([a,b]if incomplete else[a]);e.runs=runs+([['t',' '+b['text']]]if incomplete else[])
        if incomplete:e.bbox=b['bbox'];e.line_boxes=[b['bbox']]
        else:obj.element([b])
        obj.element([c]);result=obj.publish();joined_once(result,' '.join([a['text'],b['text'],c['text']]))
        raised=[n for root in result.roots for n in root.iter()if tag(n)=='sup'and words(n)=='q'];assert len(raised)==1,'The original raised semantic atom must remain raised exactly once'
        owner=[n for n in result.paragraphs if any(child is raised[0]for child in n.iter())];assert len(owner)==1 and words(owner[0]).endswith(c['text'])
    finally:obj.close()

def test_incomplete_cached_merged_extent_is_recovered_from_unique_full_original_text(tmp_path):
    obj=Synthetic(tmp_path)
    try:
        a=obj.row('This independently printed opening remains part of the',310);b=obj.row('same source paragraph although its cached rectangle is incomplete',330);c=obj.row('and its following original row finishes the explanation.',350)
        e=obj.element([a,b]);e.bbox=b['bbox'];e.line_boxes=[b['bbox']];obj.element([c]);result=obj.publish()
        joined_once(result,' '.join([a['text'],b['text'],c['text']]))
        p=next(n for n in result.paragraphs if words(n)==a['text']+' '+b['text']+' '+c['text']);regions=[n.get('data-source-regions')for n in p.iter()if n.get('data-source-regions')]
        assert regions,'Restored paragraph must keep independently bound original source regions on its existing source carrier'
        import json
        assert any(box[1]<=a['bbox'][1]+.5 for value in regions for box in json.loads(value)),'Recovered unique original opening must be covered by the independently restored source extent'
    finally:obj.close()

def test_original_rowfoot_hyphen_is_preserved_across_transitive_fragments(tmp_path):
    obj=Synthetic(tmp_path)
    try:
        rows=[obj.row(t,310+i*20)for i,t in enumerate(['The observer describes an extra-','ordinary original account that continues','through the final row of this same paragraph.'])]
        for r in rows:obj.element([r])
        result=obj.publish();joined_once(result,' '.join(r['text']for r in rows));assert 'extra-'in result.actual,'No original row-foot hyphen may be guessed away by paragraph repair'
    finally:obj.close()

@pytest.mark.parametrize('barrier',['terminal','heading','indent','gap','foreign_column'])
def test_real_original_boundary_keeps_separate_paragraphs_and_atoms(tmp_path,barrier):
    obj=Synthetic(tmp_path)
    try:
        a=obj.row('This source opening is independently complete.'if barrier=='terminal'else'This source opening is an unfinished account',310,bold=barrier=='heading');e=obj.element([a],kind='h'if barrier=='heading'else'p')
        if barrier=='heading':e.runs=[['t',a['text'],'bold']]
        y=400 if barrier=='gap'else 330;x=92 if barrier=='indent'else 250 if barrier=='foreign_column'else 70
        b=obj.row('a separately authored source paragraph begins here',y,x=x);c=obj.row('and completes its own original sentence.',y+20,x=250 if barrier=='foreign_column'else 70)
        obj.element([b,c],column=1 if barrier=='foreign_column'else 0);result=obj.publish()
        assert not any(a['text']+' '+b['text']in words(n)for n in result.paragraphs),'Printed terminal, heading, indent, vertical gap or separate leaf remains a true boundary'
        assert any(words(n)==a['text']for n in result.paragraphs)
        assert any(words(n)==b['text']+' '+c['text']for n in result.paragraphs)
    finally:obj.close()

def test_duplicate_original_full_text_does_not_relocate_incomplete_cached_owner(tmp_path):
    obj=Synthetic(tmp_path)
    try:
        a=obj.row('The repeated original opening remains unfinished',310);b=obj.row('and its original continuation stays independently bound',330)
        duplicate=[]
        for original,y in [(a,450),(b,470)]:
            obj.page.insert_text((90,y),original['text'],fontsize=12)
            spans=[q for block in obj.page.get_text('dict')['blocks']for line in block.get('lines',[])for q in line['spans']if q['text']==original['text']and abs(q['origin'][1]-y)<.01]
            assert len(spans)==1
            r=dict(text=original['text'],bbox=tuple(spans[0]['bbox']),row=len(obj.rows),size=12,font='helv');obj.rows.append(r);obj.expected_text.append(r['text']);duplicate.append(r)
        c,d=duplicate
        e=obj.element([a,b]);e.bbox=b['bbox'];e.line_boxes=[b['bbox']]
        obj.element([c,d]);result=obj.publish()
        assert not any(words(n)==(a['text']+' '+b['text']+' '+c['text']+' '+d['text'])for n in result.paragraphs),'Ambiguous complete source phrase cannot guess a different physical paragraph owner'
    finally:obj.close()


def test_direct_native_continuation_joins_four_original_fragments_transitively(tmp_path):
    from cps.services.reflow import vision_pipeline as vp,skeleton
    obj=Synthetic(tmp_path)
    try:
        rows=[obj.row(t,310+i*20)for i,t in enumerate(['The exact original account opens without termination','and continues on the second original row','with its third physical row still unfinished','until the fourth original row closes this paragraph.'])]
        elements=[obj.element([r])for r in rows]
        book=assemble.Book(elements=elements,pages={0:elements},style=skeleton.BookStyle(body_size=12,page_count=1))
        physical=[dict(text=w[4],bbox=list(w[:4]))for w in obj.page.get_text('words')]
        vp._native_inline_paragraph_ownership(obj.doc,book,0,physical)
        assert len(book.pages[0])==1 and book.pages[0][0].text==' '.join(r['text']for r in rows),'One native continuation pass must conserve and join the entire proved transitive original chain'
        assert book.pages[0][0].line_boxes==[r['bbox']for r in rows],'Original row geometry stays ordered and complete after transitive assembly'
    finally:obj.close()


def test_original_annotated_action_and_raised_atom_survive_native_chain(tmp_path):
    import copy,zipfile,re
    from types import SimpleNamespace
    from xml.etree import ElementTree as ET
    from cps.services.reflow import extract,skeleton,vision_pipeline as vp,build_epub
    obj=Synthetic(tmp_path)
    try:
        a,runs=semantic_row(obj,310);b=obj.row('while the annotated source action remains in this paragraph',330);c=obj.row('and this final physical source row completes it.',350)
        e=obj.element([a]);e.runs=runs;obj.element([b]);obj.element([c])
        rect=obj.page.search_for('keeps')[0];target=obj.rows[0]['bbox']
        obj.page.insert_link({'kind':pymupdf.LINK_GOTO,'from':rect,'page':0,'to':pymupdf.Point(target[0],(target[1]+target[3])/2)})
        original=tmp_path/'annotated-original.pdf';obj.doc.save(original);obj.doc.close();obj.doc=pymupdf.open(original);obj.page=obj.doc[0]
        raw=[extract.read_page(obj.doc,0,keep_char_boxes=True)]
        book=assemble.Book(elements=obj.elements,pages={0:obj.elements},style=skeleton.BookStyle(body_size=12,page_count=1));assemble._bind_source_navigation(book,raw)
        assert len(book.source_navigation)==1 and book.source_navigation[0]['status']=='resolved','The fixture must independently prove its original PDF action before conversion'
        before=copy.deepcopy(book);reader=f.OfflineReader(tmp_path);out=tmp_path/'annotated.epub';report=vp.convert(obj.doc,str(out),reader,native_prepared=SimpleNamespace(book=book,raw_pages=raw))
        assert reader.dispatches==0 and book==before and build_epub.validate(out)==[]
        with zipfile.ZipFile(out)as z:roots=[ET.fromstring(z.read(n))for n in z.namelist()if re.fullmatch(r'OEBPS/ch\d+\.xhtml',n)]
        rid=before.source_navigation[0]['id'];origin='pdfref_'+rid.removeprefix('pdfgoto_');exact=' '.join([a['text'],b['text'],c['text']])
        linked=[n for root in roots for n in root.iter()if tag(n)=='a'and(n.get('href')or'').split('#')[-1]==rid]
        assert len(linked)==1 and words(linked[0])=='keeps','The original annotation keeps its exact own printed label and destination once'
        targets=[n for root in roots for n in root.iter()if n.get('id')==rid];assert len(targets)==1
        returns=[n for root in roots for n in root.iter()if tag(n)=='a'and(n.get('href')or'').split('#')[-1]==origin];assert returns,'The original destination must retain its exact Return source action'
        owners=[n for root in roots for n in root.iter()if tag(n)=='p'and any(child is linked[0]for child in n.iter())]
        assert len(owners)==1 and words(owners[0])==exact,'Native continuity repair must join the entire original paragraph while conserving annotation ownership'
        assert sum(tag(n)=='sup'and words(n)=='q'for root in roots for n in root.iter())==1,'Source raised semantic run must retain its original style once'
    finally:obj.close()

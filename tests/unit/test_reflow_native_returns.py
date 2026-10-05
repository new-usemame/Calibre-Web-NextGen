"""Literal PDF coordinates, never matching numerals, authorize native routes."""
import copy
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import pytest
from cps.services.reflow import assemble, build_epub, enriched_source
from cps.services.reflow.structural_ops import ContractError
from tests.unit.test_reflow_pdf_goto import _document


def tree(html):
    return ET.fromstring('<div xmlns:epub="http://www.idpf.org/2007/ops">'+html+'</div>')


def test_native_destinations_have_individual_exact_returns():
    with _document() as doc:
        book=assemble.deterministic_book(doc)
        for link in book.source_navigation:
            if link['status']!='resolved':continue
            source=tree(build_epub.page_fragment(book,link['pno']))
            dest=tree(build_epub.page_fragment(book,link['dest_page']))
            back=dest.find('.//a[@class="pdf-return"]')
            assert back is not None, 'native destination has no authored Return'
            origin=source.find('.//*[@id="'+back.get('href')[1:]+'"]')
            assert origin is not None
            assert origin.get('id')=='pdfref_'+link['id'].removeprefix('pdfgoto_')
            assert dest.find('.//*[@id="'+link['id']+'"]') is not None


def note_fixture():
    # The authored label 15 goes to the printed marker 22, not a guessed 15/25.
    source_box=(20,30,170,42); marker_box=(20,100,29,110)
    line=SimpleNamespace(bbox=marker_box,stripped='22')
    link=dict(pno=0,xref=9,kind=1,status='source_owned',label='15',
              line_text='Read 15 here.',line_box=source_box,dest_page=1,
              dest_point=(20,102),rect=(45,30,55,42))
    raws=[SimpleNamespace(pno=0,source_links=[link],text_blocks=[]),
          SimpleNamespace(pno=1,source_links=[],text_blocks=[SimpleNamespace(lines=[line])])]
    element=assemble.Element(kind='p',pno=0,runs=[['t','Read 15 here.']],line_boxes=[source_box])
    note=assemble.Note(22,'Protected note text with hy- phen.',1,bbox=(20,100,200,130),glyph_fallback=True)
    book=assemble.Book(pages={0:[element],1:[]},notes=[note])
    return book,raws


def test_native_note_geometry_keeps_protected_tree_and_multiple_incoming_returns():
    book,raws=note_fixture()
    second=copy.deepcopy(raws[0].source_links[0]);second.update(pno=2,xref=10)
    book.pages[2]=copy.deepcopy(book.pages[0]);raws.append(SimpleNamespace(pno=2,source_links=[second],text_blocks=[]))
    before=tree(build_epub.page_fragment(book,1));notes=copy.deepcopy(book.notes)
    assemble._bind_source_navigation(book,raws)
    assert [l['status'] for l in book.source_navigation]==['resolved','resolved']
    assert all(l['dest_note']==0 for l in book.source_navigation)
    after=tree(build_epub.page_fragment(book,1))
    assert book.notes==notes
    assert [ET.tostring(n) for n in before.iter('img')]==[ET.tostring(n) for n in after.iter('img')]
    returns=after.findall('.//a[@class="pdf-return"]')
    assert {a.get('href') for a in returns}=={'#pdfref_p0000_x9','#pdfref_p0002_x10'}
    assert all(a.get('aria-label') for a in returns)
    assert not any(a.findall('.//a') for a in after.iter('a'))
    assert '15' in ''.join(tree(build_epub.page_fragment(book,0)).itertext())
    assert '25' not in ''.join(tree(build_epub.page_fragment(book,0)).itertext())


def test_ambiguous_or_interior_protected_note_destination_stays_unresolved():
    for mode in ('overlap','interior','unsupported'):
        book,raws=note_fixture()
        if mode=='overlap':book.notes.append(copy.deepcopy(book.notes[0]))
        if mode=='interior':
            raws[1].text_blocks[0].lines[0].bbox=(20,120,180,130)
            raws[0].source_links[0]['dest_point']=(20,121)
        if mode=='unsupported':raws[0].source_links[0]['status']='unsupported_action'
        assemble._bind_source_navigation(book,raws)
        assert book.source_navigation[0]['status']!='resolved'
        assert 'pdf-return' not in build_epub.page_fragment(book,1)


def test_canonical_source_rejects_changed_native_destination():
    with _document() as doc:
        book=assemble.deterministic_book(doc)
        page=enriched_source.prepare_source_page(book,2,{'layer':'native'})
        book.source_navigation[0]['dest_offset']+=1
        with pytest.raises(ContractError,match='stale'):
            page.validate(book)


def test_joined_source_lines_keep_exact_destination_ownership():
    first=(20,70,200,82);second=(20,90,200,102)
    book,raws=note_fixture()
    raws[0].source_links[0]['dest_point']=(20,91)
    lines=[SimpleNamespace(bbox=first,stripped='The passage continues'),
           SimpleNamespace(bbox=second,stripped='at this exact destination.')]
    raws[1].text_blocks=[SimpleNamespace(lines=lines)]
    book.notes=[]
    book.pages[1]=assemble._join_within_page([
        assemble.Element(kind='p',pno=1,runs=[['t',line.stripped]],
                         bbox=line.bbox,line_boxes=[line.bbox],pages=[1]) for line in lines],set())
    assert len(book.pages[1])==1
    assemble._bind_source_navigation(book,raws)
    link=book.source_navigation[0]
    assert link['status']=='resolved'
    assert link['dest_offset']==len('The passage continues ')
    target=tree(build_epub.page_fragment(book,1))
    arrival=target.find('.//*[@id="'+link['id']+'"]')
    assert arrival is not None


def test_publication_rewrites_exact_forward_and_return_pairs_across_chapters(tmp_path):
    import zipfile
    with _document() as doc:
        book=assemble.deterministic_book(doc)
        out=tmp_path/'native-returns.epub'
        build_epub.build(book,str(out),doc=doc)
    assert build_epub.validate(out)==[]
    with zipfile.ZipFile(out) as archive:
        docs={name:ET.fromstring(archive.read(name)) for name in archive.namelist()
              if name.endswith('.xhtml')}
    locations={n.get('id'):(name,n) for name,t in docs.items() for n in t.iter() if n.get('id')}
    def resolve(name,href):
        path,ident=href.split('#')
        from posixpath import dirname,join,normpath
        return normpath(join(dirname(name),path)) if path else name,ident
    returns=[]
    for name,t in docs.items():
        for a in t.iter():
            if a.get('class')!='pdf-return':continue
            target,ident=resolve(name,a.get('href'))
            assert locations[ident][0]==target
            forward_id='pdfgoto_'+ident.removeprefix('pdfref_')
            assert locations[forward_id][0]==name
            source=docs[target]
            assert any(resolve(target,n.get('href'))==(name,forward_id)
                       for n in source.iter() if '#' in n.get('href',''))
            returns.append((name,target,ident))
    assert len(returns)==2
    assert any(a!=b for a,b,_ in returns) and any(a==b for a,b,_ in returns)


def test_generated_return_is_visible_without_a_special_arrow_glyph():
    link=tree(build_epub._pdf_return('pdfgoto_p0000_x9')).find('.//a')
    assert ''.join(link.itertext())=='Return'
    assert link.get('href')=='#pdfref_p0000_x9'


def test_generated_return_does_not_turn_a_finished_sentence_into_a_continuation():
    control=build_epub._pdf_return('pdfgoto_p0000_x9')
    pages=build_epub._page_blocks({0:'<p>The source sentence is complete.'+control+'</p>',
                                  1:'<p>another distinct source paragraph begins here.</p>'})
    assert build_epub._join_page_turns(pages)==0


def test_native_return_controls_stay_outside_source_prose_and_captions():
    with _document() as doc:
        book=assemble.deterministic_book(doc)
        for pno in book.pages:
            root=tree(build_epub.page_fragment(book,pno))
            parents={child:parent for parent in root.iter() for child in parent}
            for control in root.findall('.//a[@class="pdf-return"]'):
                assert parents[control].get('class')=='source-evidence-notice'
            for node in root.findall('.//p'):
                if node.get('class') in (None,'caption'):
                    assert node.find('.//a[@class="pdf-return"]') is None


def test_native_note_returns_are_separate_from_original_note_text():
    book,raws=note_fixture();assemble._bind_source_navigation(book,raws)
    root=tree(build_epub.page_fragment(book,1))
    parents={child:parent for parent in root.iter() for child in parent}
    returns=root.findall('.//a[@class="pdf-return"]')
    assert len(returns)==1
    assert all(parents[control].get('class')=='source-evidence-notice' for control in returns)


def test_native_body_returns_follow_page_prose_without_interrupting_adjacent_passages():
    with _document() as doc:
        book=assemble.deterministic_book(doc)
        book.pages[0].append(assemble.Element(kind='p',pno=0,
            runs=[['t','The next source paragraph follows the quotation.']]))
        root=tree(build_epub.page_fragment(book,0));children=list(root)
        source=[n for n in children if n.tag=='p' and n.get('class') is None]
        assert len(source)>=2
        left,right=children.index(source[-2]),children.index(source[-1])
        assert right==left+1,'generated Returns must not separate neighboring source passages'
        returns=root.findall('.//a[@class="pdf-return"]');assert returns
        parents={child:parent for parent in root.iter() for child in parent}
        for back in returns:
            group=parents[parents[back]]
            assert group.tag=='aside' and group.get('class')=='source-native-returns'
            assert children.index(group)>right
            ident=back.get('href')[1:]
            origin=tree(build_epub.page_fragment(book,2));assert origin.find('.//*[@id="'+ident+'"]') is not None


@pytest.mark.parametrize('printed_note',[False,True])
def test_body_return_aside_keeps_continuation_page_marker_at_its_source_seam(printed_note):
    action='<aside class="source-native-returns"><p class="source-evidence-notice">'+build_epub._pdf_return('pdfgoto_p0002_x9')+'</p></aside>'
    note='<aside epub:type="footnote" id="fn_1"><p>A printed note.</p></aside>' if printed_note else ''
    pages=build_epub._page_blocks({0:'<p>The original sentence continues</p>'+action+note,
                                  1:'<p>across its page boundary.</p><p>A distinct following paragraph.</p>'})
    assert build_epub._join_page_turns(pages)==1
    root=tree(''.join(block for chapter in build_epub._chapters(pages) for block in chapter.blocks))
    joined=next(p for p in root.findall('.//p') if ''.join(p.itertext()).startswith('The original sentence'))
    marker=root.find('.//*[@id="pg_0001"]');assert marker is not None
    if printed_note:
        # The prior printed note still keeps its original page-scoped marker.
        assert joined.find('.//*[@id="source_return_0001"]') is not None
        assert joined.find('.//*[@id="pg_0001"]') is None
        nodes=list(root.iter());assert nodes.index(root.find('.//*[@id="fn_p0000_1"]'))<nodes.index(marker)
    else:
        assert joined.find('.//*[@id="pg_0001"]') is marker
        assert marker.tail.startswith('across its page boundary.')
    assert len(root.findall('.//a[@class="pdf-return"]'))==1

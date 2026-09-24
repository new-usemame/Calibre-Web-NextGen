"""PDF-authored GoTo navigation survives source extraction and EPUB splitting."""
import re
import zipfile
from xml.etree import ElementTree as ET

import pymupdf

from cps.services.reflow import assemble, build_epub, extract


def _document():
    doc = pymupdf.open()
    for _ in range(3):doc.new_page(width=450, height=650)
    doc[0].insert_text((50, 110), 'Chapter One', fontsize=20)
    doc[0].insert_text((50, 150), 'A body passage containing a source destination.', fontsize=11)
    doc[1].insert_text((50, 110), 'Chapter Two', fontsize=20)
    doc[1].insert_text((50, 150), 'A second destination in another chapter.', fontsize=11)
    doc[2].insert_text((50, 100), '1. Return to first context.', fontsize=11)
    doc[2].insert_text((50, 130), '2. Return to second context.', fontsize=11)
    doc[2].insert_text((50, 160), '3. An unannotated identical numeral.', fontsize=11)
    # Page objects are fetched only after all new_page calls; creating pages
    # invalidates earlier PyMuPDF handles.
    for y, page, point in ((100,0,(120,135)), (130,1,(120,135))):
        doc[2].insert_link({'kind':pymupdf.LINK_GOTO,
            'from':pymupdf.Rect(50,y-12,56,y+3),'page':page,
            'to':pymupdf.Point(*point)})
    doc[2].insert_link({'kind':pymupdf.LINK_URI,
        'from':pymupdf.Rect(65,155,105,165),'uri':'https://example.invalid/'})
    return pymupdf.open(stream=doc.tobytes(),filetype='pdf')


def test_two_source_gotos_resolve_across_chapters_without_invented_links(tmp_path):
    with _document() as doc:
        book=assemble.deterministic_book(doc)
        resolved=[item for item in book.source_navigation if item['status']=='resolved']
        assert len(resolved)==2
        assert all(item['dest_page'] in (0,1) for item in resolved)
        assert any(item['status']=='unsupported_action' for item in book.source_navigation)
        out=tmp_path/'gotos.epub'
        build_epub.build(book,str(out),doc=doc)
    assert build_epub.validate(str(out))==[]
    with zipfile.ZipFile(out) as z:
        docs={name:z.read(name).decode() for name in z.namelist()
              if re.fullmatch(r'OEBPS/ch\d+\.xhtml',name)}
        roots={name:ET.fromstring(xml) for name,xml in docs.items()}
        links=[(name,node.get('href')) for name,root in roots.items()
               for node in root.iter() if node.tag.endswith('}a')
               and 'pdfgoto_' in (node.get('href') or '')]
        assert len(links)==2
        for name,href in links:
            target,ident=href.split('#') if not href.startswith('#') else (name[6:],href[1:])
            target='OEBPS/'+target
            assert target in roots
            assert sum(node.get('id')==ident for node in roots[target].iter())==1
        assert not any('example.invalid' in href for _,href in links)


def test_tall_rectangles_own_only_centred_glyphs_and_competing_owners_fail_closed():
    with _document() as doc:
        raw=extract.read_page(doc,2)
        labels=[link.get('label') for link in raw.source_links if link['kind']==pymupdf.LINK_GOTO]
        assert labels==['1','2']
    with pymupdf.open() as doc:
        for _ in range(2):doc.new_page(width=450,height=650)
        doc[0].insert_text((50,100),'1. One line.',fontsize=11)
        doc[0].insert_text((50,115),'2. Another line.',fontsize=11)
        doc[1].insert_text((50,100),'Destination.',fontsize=11)
        doc[0].insert_link({'kind':pymupdf.LINK_GOTO,
            'from':pymupdf.Rect(50,86,57,122),'page':1,'to':pymupdf.Point(50,90)})
        raw=extract.read_page(doc,0)
        assert raw.source_links[0]['status']=='ambiguous_source'


def test_missing_destination_stays_diagnostic_and_keeps_source_access():
    with _document() as doc:
        raws=[extract.read_page(doc,2)]
        # A bounded conversion has no destination page. It must not silently
        # redirect its two valid PDF destinations to another note or page.
        from cps.services.reflow import skeleton
        style=skeleton.book_style(raws)
        skel=[skeleton.page_skeleton(raws[0],style)]
        book=assemble.assemble(skel,style,raws)
        assert all(link['status']=='destination_unavailable' for link in book.source_navigation
                   if link['kind']==pymupdf.LINK_GOTO)
        assert book.needs_source_evidence(2)
        assert 'original-p0002.xhtml#page' in build_epub.page_fragment(book,2)


def test_repeated_numerals_use_owned_line_context_and_two_links_can_share_target():
    # The PDF geometry identifies the source line. Different source lines may
    # legitimately use the same label, and two authored links may share a point.
    with pymupdf.open() as doc:
        for _ in range(2):doc.new_page(width=450,height=650)
        doc[0].insert_text((50,100),'A destination in the original context.',fontsize=11)
        doc[1].insert_text((50,100),'1. First return line.',fontsize=11)
        doc[1].insert_text((50,130),'1. Second return line.',fontsize=11)
        for y in (100,130):
            doc[1].insert_link({'kind':pymupdf.LINK_GOTO,
                'from':pymupdf.Rect(50,y-12,56,y+3),
                'page':0,'to':pymupdf.Point(80,88)})
        book=assemble.deterministic_book(doc)
        records=[r for r in book.source_navigation if r['status']=='resolved']
        assert len(records)==2
        assert len({r['id'] for r in records})==2
        fragment=build_epub.page_fragment(book,1)
        assert fragment.count('<a href="#pdfgoto_')==2
        target=build_epub.page_fragment(book,0)
        assert all('id="%s"' % r['id'] in target for r in records)


def test_viewport_point_above_second_line_targets_second_context():
    with pymupdf.open() as doc:
        for _ in range(2):doc.new_page(width=450,height=650)
        doc[0].insert_text((50,100),'First separate context.',fontsize=11)
        doc[0].insert_text((50,130),'Second separate context.',fontsize=11)
        doc[1].insert_text((50,100),'1. Return.',fontsize=11)
        doc[1].insert_link({'kind':pymupdf.LINK_GOTO,
            'from':pymupdf.Rect(50,88,56,103),
            'page':0,'to':pymupdf.Point(80,117)})
        book=assemble.deterministic_book(doc)
        record=next(r for r in book.source_navigation if r['kind']==pymupdf.LINK_GOTO)
        assert record['status']=='resolved'
        target=build_epub.page_fragment(book,0)
        assert target.index('First separate context.') < target.index('id="%s"' % record['id'])
        assert target.index('id="%s"' % record['id']) < target.index('Second separate context.')


def test_malformed_target_and_non_goto_actions_never_become_internal_links():
    class _Page:
        number=0
        parent=[type('Target',(),{'rect':pymupdf.Rect(0,0,450,650)})()]
        def get_links(self):
            base={'from':pymupdf.Rect(50,80,60,105),'page':0,
                  'to':pymupdf.Point(50,90)}
            return [dict(base,xref=1,kind=pymupdf.LINK_GOTO,page=9),
                    dict(base,xref=2,kind=pymupdf.LINK_GOTO,to=(float('nan'),90)),
                    dict(base,xref=3,kind=pymupdf.LINK_GOTOR),
                    dict(base,xref=4,kind=pymupdf.LINK_URI),
                    dict(base,xref=5,kind=pymupdf.LINK_LAUNCH),
                    dict(base,xref=6,kind=pymupdf.LINK_GOTO,
                         **{'from':(50,float('nan'),60,105)}),
                    dict(base,xref=0,kind=pymupdf.LINK_GOTO)]
    rows=extract._source_links(_Page(),{},1)
    assert [r['status'] for r in rows] == [
        'invalid_destination','invalid_destination',
        'unsupported_action','unsupported_action','unsupported_action',
        'invalid_source_rectangle','missing_xref']
    assert rows[-2]['rect'] is None
    assert rows[-1]['xref']==0 and rows[-1]['annotation_index']==6


def test_figure_attached_caption_keeps_its_authored_link_and_destination():
    caption=assemble.Element(kind='caption',runs=[['t','Fig. 2 .... 25']],pno=0)
    book=assemble.Book(pages={0:[assemble.Element(kind='fig',pno=0),caption],
                              1:[assemble.Element(kind='p',runs=[['t','The destination.']],pno=1)]},
        figures=[{'pno':0,'found':'embedded'}],source_navigation=[{
            'status':'resolved','pno':0,'xref':4,'id':'pdfgoto_p0000_x4',
            'source_element':1,'source_item':None,'source_offset':12,
            'source_extent':2,'dest_page':1,'dest_element':0,
            'dest_item':None,'dest_offset':0,'kind':pymupdf.LINK_GOTO}])
    source=build_epub.page_fragment(book,0)
    target=build_epub.page_fragment(book,1)
    assert '<a href="#pdfgoto_p0000_x4">25</a>' in source
    assert 'id="pdfgoto_p0000_x4"' in target

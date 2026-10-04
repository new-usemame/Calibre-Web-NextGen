"""An unindented lowercase parenthetical can continue source prose at a page turn."""
import zipfile
from xml.etree import ElementTree as ET
import pymupdf,pytest
from cps.services.reflow import extract,skeleton,assemble,enriched_source,build_epub
pytestmark=pytest.mark.unit
X='{http://www.w3.org/1999/xhtml}'


def source(tmp_path,kind='continuation'):
    path=tmp_path/(kind+'.pdf')
    with pymupdf.open() as doc:
        p=doc.new_page(width=400,height=700)
        p.insert_text((40,100),'Ordinary source body establishes its column.',fontsize=12)
        p.insert_text((40,600),'Venus is quite well off'+('.' if kind=='finished' else ''),fontsize=12)
        p.insert_text((40,670),'1 Printed note stays traceable.',fontsize=9)
        if kind.startswith('footer_'):p.draw_line((40,655),(350,655))
        p=doc.new_page(width=440,height=700)
        p.insert_text((85 if kind=='indented' else 65,70),'(ignoring its cadency). Its dignity remains.',fontsize=12)
        p.insert_text((65,220),'Ordinary source prose resumes in its column.',fontsize=12)
        doc.save(path)
    doc=pymupdf.open(path);raws=extract.read_pages(doc);style=skeleton.BookStyle(12)
    skels=[]
    for raw in raws:
        regions=[]
        for block in raw.text_blocks:
            note=block.bbox[1]>630 and kind!='footer_body'
            regions.append(skeleton.Region('note' if note else 'body',block.lines,bbox=block.bbox,number=1 if note else None))
        if kind.startswith('footer_') and raw.pno==0:
            regions.append(skeleton.Region('figure',bbox=(35,650,360,689),reason='scan_figure_side' if kind=='footer_figure' else 'scan_figure_band',needs_ink=True))
        skels.append(skeleton.PageSkeleton(raw.pno,raw.width,raw.height,regions))
    book=assemble.assemble(skels,style,raws);book.source_fingerprint=extract.document_fingerprint(doc)
    sources={p:enriched_source.prepare_source_page(book,p,{'layer':'native'}) for p in book.pages}
    return book,doc,raws,sources


@pytest.mark.parametrize('kind',['continuation','footer_image'])
def test_written_parenthetical_tail_joins_past_page_notes(tmp_path,kind):
    book,doc,raws,sources=source(tmp_path,kind)
    target=tmp_path/'parenthetical.epub'
    build_epub.build(book,str(target),doc=doc,source_pages=sources,raw_pages={r.pno:r for r in raws})
    assert build_epub.validate(str(target))==[]
    with zipfile.ZipFile(target) as z:
        roots=[ET.fromstring(z.read(n)) for n in z.namelist() if n.startswith('OEBPS/ch') and n.endswith('.xhtml')]
        paragraphs=[p for root in roots for p in root.iter(X+'p')]
        assert any('Venus is quite well off (ignoring its cadency).' in ''.join(p.itertext()) for p in paragraphs)
        assert 'Printed note stays traceable.' in ' '.join(''.join(r.itertext()) for r in roots)
        if kind=='footer_image':
            assert any(a.get('class')=='source-note-context' and a.find('.//'+X+'img') is not None for r in roots for a in r.iter(X+'aside'))
        assert all(any(n.get('id')=='pg_%04d'%page for r in roots for n in r.iter()) for page in (0,1))
    doc.close()


@pytest.mark.parametrize('kind',['finished','indented','footer_body','footer_figure'])
def test_source_boundary_or_first_line_indent_does_not_join_a_parenthetical(tmp_path,kind):
    book,doc,raws,sources=source(tmp_path,kind)
    target=tmp_path/'separate.epub'
    build_epub.build(book,str(target),doc=doc,source_pages=sources,raw_pages={r.pno:r for r in raws})
    with zipfile.ZipFile(target) as z:
        roots=[ET.fromstring(z.read(n)) for n in z.namelist() if n.startswith('OEBPS/ch') and n.endswith('.xhtml')]
        assert not any('well off (ignoring' in ''.join(p.itertext()) for r in roots for p in r.iter(X+'p'))
    doc.close()


@pytest.mark.parametrize('control',[None,'finished','indented','gap','short_tail','glyph_tail','other_column'])
def test_measured_parenthetical_fragment_keeps_same_page_paragraph(control):
    tail='The source describes the place of property'+('.' if control=='finished' else '')
    runs=[['glyph',tail]] if control=='glyph_tail' else [['t',tail]]
    a=assemble.Element('p',runs,bbox=(40,100,340,122),pages=[0],line_boxes=[(58,100,340,109),(40,113,160 if control=='short_tail' else 339,122)])
    x=58 if control=='indented' else 40;y=140 if control=='gap' else 126
    b=assemble.Element('p',[['t','(moveable possessions rather than land). The source continues.']],bbox=(x,y,330,y+9),pages=[0],line_boxes=[(x,y,330,y+9)],column=1 if control=='other_column' else 0)
    out=assemble._join_within_page([a,b],set())
    assert len(out)==(1 if control is None else 2)
    if control is None:
        assert out[0].text==tail+' (moveable possessions rather than land). The source continues.'
        assert out[0].line_boxes==[(58,100,340,109),(40,113,339,122),(40,126,330,135)]

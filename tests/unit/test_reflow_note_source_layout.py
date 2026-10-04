"""A native footnote grid retains its printed cell associations."""
from dataclasses import asdict
import pytest
from cps.services.reflow import extract,skeleton,assemble,build_epub
pytestmark=pytest.mark.unit

def line(text,x,y,size=9):
    box=(x,y,x+len(text)*4,y+size)
    return extract.Line([extract.Span(text,size,'Times-Roman',0,box)],box)

def note_region(grid=True):
    lines=[line('1 The source explains these source relationships.',40,420)]
    if grid:
        lines += [line(text,x,460+i*14) for i,row in enumerate([('Fire','Sun','Jupiter'),('Earth','Venus','Moon'),('Air','Saturn','Mercury'),('Water','Mars','Mars')]) for x,text in zip((40,105,175),row)]
    else:
        lines += [line('Ordinary footnote prose stays searchable text.',40,460+i*14) for i in range(4)]
    return skeleton.Region('note',lines=lines,bbox=(40,420,340,520),number=1)

def book_for(region):
    body=line('The body calls this note.',40,100,11)
    raw=extract.RawPage(0,400,600,[extract.Block(0,body.bbox,[body]),extract.Block(1,region.bbox,region.lines)])
    skel=skeleton.PageSkeleton(0,400,600,regions=[skeleton.Region('body',lines=[body],bbox=body.bbox),region])
    style=skeleton.BookStyle(body_size=11)
    return assemble.assemble([skel],style,[raw]),raw

def test_repeated_native_note_columns_keep_original_layout_without_changing_words():
    region=note_region();before=asdict(region)
    book,raw=book_for(region)
    assert book.conservation.ok
    note=book.notes[0]
    assert getattr(note,'source_layout',False)
    assert not note.uncertain
    html=build_epub.page_fragment(book,0)
    assert 'Original printed note layout' in html and 'class="source-glyph"' in html and 'glyph_p0000_' in html
    assert 'Fire Sun Jupiter' in note.text
    assert asdict(region)==before

@pytest.mark.parametrize('change',['ordinary','shifted','two_rows','overlapping'])
def test_unproved_note_columns_do_not_replace_native_prose(change):
    region=note_region(change!='ordinary')
    if change=='shifted':
        region.lines[7].bbox=(140,region.lines[7].bbox[1],165,region.lines[7].bbox[3])
        region.lines[10].bbox=(145,region.lines[10].bbox[1],170,region.lines[10].bbox[3])
    if change=='two_rows':region.lines=region.lines[:7]
    if change=='overlapping':
        for line in region.lines[1::3]:line.bbox=(line.bbox[0],line.bbox[1],130,line.bbox[3])
    book,raw=book_for(region)
    assert book.conservation.ok
    assert not getattr(book.notes[0],'source_layout',False)
    assert 'class="source-glyph"' not in build_epub.page_fragment(book,0)

"""Visual title boundaries survive arbitrary PDF block segmentation."""
import pytest
from cps.services.reflow import extract, skeleton, assemble

pytestmark=pytest.mark.unit

def line(text,x,y,size=12,font='Times-Bold',width=180):
    box=(x,y,x+width,y+size)
    return extract.Line([extract.Span(text,size,font,20 if 'Bold' in font else 4,box,y+size)],box)

def source():
    first=line('A complete source title:',160,100)
    second=line('continued on another line',160,112)
    drop=line('A',50,160,size=36,font='Times',width=25)
    body=line('body paragraph keeps its exact source words.',80,164,size=10,font='Times',width=300)
    blocks=[extract.Block(0,first.bbox,[first]),extract.Block(1,(50,112,340,196),[second,drop]),extract.Block(2,body.bbox,[body])]
    return extract.RawPage(0,500,700,blocks=blocks),first,second,drop,body

def test_two_line_title_split_across_heterogeneous_blocks_is_one_complete_heading():
    raw,first,second,drop,body=source()
    style=skeleton.BookStyle(body_size=10)
    sk=skeleton.page_skeleton(raw,style)
    headings=[r for r in sk.regions if r.kind=='heading']
    assert len(headings)==1
    assert headings[0].lines==[first,second]
    assert headings[0].bbox==(160,100,340,124)
    emitted=[ln for r in sk.regions for ln in r.lines]
    assert sorted(id(l) for l in emitted)==sorted(id(l) for l in [first,second,drop,body])
    assert next(r for r in sk.regions if drop in r.lines).kind=='body'

def test_old_partial_book_cannot_admit_a_fragment_of_visual_title():
    from cps.services.reflow.heading_evidence import heading_evidence
    raw,first,second,drop,body=source()
    e=assemble.Element('p',runs=[['t',first.text]],bbox=first.bbox,line_boxes=[first.bbox])
    book=assemble.Book(elements=[e],pages={0:[e]},style=skeleton.BookStyle(body_size=10))
    proof=heading_evidence(book,0,raw,'native')[0]
    assert not proof['supported']
    assert proof['reason']=='incomplete_visual_source_unit'

def test_numbered_caption_precedes_heading_classification_and_does_not_enter_navigation(tmp_path):
    from cps.services.reflow import build_epub
    import zipfile
    caption=line('Table 7: Source relationships',100,100)
    raw=extract.RawPage(0,500,700,blocks=[extract.Block(0,caption.bbox,[caption])])
    style=skeleton.BookStyle(body_size=10)
    sk=skeleton.page_skeleton(raw,style)
    assert [(r.kind,r.lines) for r in sk.regions]==[('caption',[caption])]
    book=assemble.assemble([sk],style,[raw])
    target=tmp_path/'caption.epub';build_epub.build(book,str(target))
    with zipfile.ZipFile(target) as z:
        nav=''.join(z.read(n).decode() for n in z.namelist() if n.endswith(('nav.xhtml','.ncx')))
        assert caption.text not in nav

@pytest.mark.parametrize('layout',['separate','columns','inline'])
def test_distinct_display_and_prose_units_do_not_merge(layout):
    from cps.services.reflow.heading_units import native_units
    a=line('First source unit',100,100)
    b=line('Second source unit',100 if layout!='columns' else 340,150 if layout=='separate' else 112)
    if layout=='inline':b=line('ordinary continuation',100,112,size=10,font='Times')
    assert native_units([a.to_dict(),b.to_dict()])==[[0],[1]]

def test_same_size_centered_title_uses_complete_geometry_without_invented_font_contrast():
    from cps.services.reflow.heading_units import native_units
    a=line('The complete title',170,100,font='Times',width=160)
    b=line('its second line',190,112,font='Times',width=120)
    assert native_units([a.to_dict(),b.to_dict()])==[[0,1]]


def test_title_split_keeps_dropcap_before_body_with_raised_marker():
    raw,first,second,drop,body=source()
    marker=line('1',120,158,size=6,font='Times',width=3)
    raw.blocks[-1].lines.append(marker)
    raw.blocks[-1].bbox=(80,158,380,174)
    style=skeleton.BookStyle(body_size=10)
    sk=skeleton.page_skeleton(raw,style)
    book=assemble.assemble([sk],style,[raw])
    assert [e.text for e in book.pages[0]][:2]==[
        'A complete source title: continued on another line',
        'A body paragraph keeps its exact source words. 1']


def test_final_builder_rechecks_complete_units_even_for_previously_admitted_plan(tmp_path,monkeypatch):
    import pymupdf
    from cps.services.reflow import heading_units,structural_ops as ops,build_epub
    raw,first,second,drop,body=source()
    e=assemble.Element('p',runs=[['t',first.text]],bbox=first.bbox,line_boxes=[first.bbox])
    book=assemble.Book(elements=[e],pages={0:[e]},style=skeleton.BookStyle(body_size=10))
    doc=pymupdf.open();doc.new_page(width=500,height=700)
    source_path=tmp_path/'source.pdf';doc.save(source_path);doc.close();doc=pymupdf.open(source_path)
    # Reproduce the old missing-boundary admission, then restore the real guard.
    with monkeypatch.context() as m:
        m.setattr(heading_units,'native_units',lambda lines:[[i] for i in range(len(lines))])
        prepared=ops.prepare(book,doc,0,'test',{'layer':'native'},raw_page=raw)
        cid=next(c['candidate_id'] for c in prepared.candidates() if c['kind']=='heading')
        plan=prepared.accept(book,doc,{'protocol':ops.PROTOCOL,'snapshot_id':prepared.snapshot_id,'select':[cid]})
    target=tmp_path/'partial.epub'
    with pytest.raises(ops.ContractError):
        build_epub.build(book,str(target),doc=doc,operation_plans=[plan])
    assert not target.exists()
    doc.close()


@pytest.mark.parametrize('initial,word,gap,joined',[('Q','uartz',.5,True),('A','young',3.,False),('É','lan',.5,True)])
def test_native_dropcap_uses_measured_space_not_grammar(initial,word,gap,joined):
    import pymupdf
    doc=pymupdf.open();page=doc.new_page(width=500,height=700)
    font=pymupdf.Font('tiro')
    end=50+font.text_length(initial,fontsize=36)
    page.insert_text((50,180),initial,fontname='tiro',fontsize=36)
    page.insert_text((end+gap,156),word+' people preserve the original wording.',fontname='tiro',fontsize=12)
    for y in (220,235,250):page.insert_text((50,y),'Ordinary body reference has real printed spaces.',fontname='tiro',fontsize=12)
    raw=extract.read_page(doc,0);style=skeleton.BookStyle(body_size=12)
    sk=skeleton.page_skeleton(raw,style,pixel_probe=extract.ScanPixelProbe(doc,0))
    book=assemble.assemble([sk],style,[raw])
    expected=initial+('' if joined else ' ')+word
    assert book.pages[0][0].text.startswith(expected),book.pages[0][0].text
    repairs=[r for r in book.repairs if r.kind=='source_initial_join']
    assert bool(repairs) is joined
    assert book.conservation.ok,book.conservation.to_dict()
    doc.close()

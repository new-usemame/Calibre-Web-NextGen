"""Sparse ruled source tables retain their printed associations as one image."""
import pymupdf,pytest
from cps.services.reflow import extract,skeleton,assemble
pytestmark=pytest.mark.unit


def source(tmp_path,kind='table'):
    path=tmp_path/(kind+'.pdf')
    with pymupdf.open() as doc:
        page=doc.new_page(width=500,height=700)
        page.insert_text((50,80),'Ordinary prose before the source table supplies the body column.',fontsize=12)
        for col,values in zip((50,190,310),(['Alpha','Beta','Gamma'],['Mars','Venus','Saturn'],['Aversion','Square','Trine'])):
            for row,value in enumerate(values if kind!='single_row' else values[:1]):
                page.insert_text((col,150+16*row),value,fontsize=10)
        if kind=='multiline':page.insert_text((310,198),'Second line',fontsize=10)
        if kind=='unmapped_cell':page.insert_text((120,198),'Unmapped continuation',fontsize=10)
        page.insert_text((50,240),'Ordinary prose resumes after the source table with its own boundary.',fontsize=12)
        if kind!='no_rules':
            page.draw_line((45,130),(420,130))
            page.draw_line((45,205 if kind!='crossed_boundary' else 173),(420,205 if kind!='crossed_boundary' else 173))
        doc.save(path)
    doc=pymupdf.open(path);raw=extract.read_page(doc,0)
    probe=extract.ScanPixelProbe(doc,0,mask=[l.bbox for b in raw.text_blocks for l in b.lines])
    return doc,raw,probe


@pytest.mark.parametrize('kind',['table','multiline'])
def test_sparse_ruled_table_keeps_all_columns_and_rows_in_one_source_crop(tmp_path,kind):
    doc,raw,probe=source(tmp_path,kind)
    style=skeleton.book_style([raw]);skel=skeleton.page_skeleton(raw,style,pixel_probe=probe)
    tables=[r for r in skel.regions if r.kind=='figure' and r.reason=='source_ruled_table']
    assert len(tables)==1,'two horizontal rules and aligned columns must preserve the whole source table'
    box=tables[0].bbox
    assert box[0]<45 and box[2]>420 and box[1]<130 and box[3]>205
    words=[r for r in skel.regions if r.kind=='artwork' and r.reason=='source_ruled_table']
    assert len(words)==1 and all(v in words[0].text for v in ['Alpha','Gamma','Mars','Saturn','Aversion','Trine'])
    if kind=='multiline':assert 'Second line' in words[0].text
    assert not any('Alpha' in r.text for r in skel.regions if r.kind=='body')
    book=assemble.assemble([skel],style,[raw]);assert book.conservation.ok
    assert all(v in ' '.join(e.text for e in book.pages[0]) for v in ['Ordinary prose before','Ordinary prose resumes'])
    doc.close()


@pytest.mark.parametrize('kind',['no_rules','single_row','crossed_boundary','unmapped_cell'])
def test_unsupported_sparse_layout_is_not_claimed_as_a_complete_source_table(tmp_path,kind):
    doc,raw,probe=source(tmp_path,kind)
    skel=skeleton.page_skeleton(raw,skeleton.book_style([raw]),pixel_probe=probe)
    assert not any(r.kind=='figure' and r.reason=='source_ruled_table' for r in skel.regions)
    doc.close()

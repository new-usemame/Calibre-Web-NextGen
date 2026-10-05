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
        if kind in ('grid','ink_crossing'):
            for x in (45,180,300,420):page.draw_line((x,130),(x,205))
            for y in (154,170):page.draw_line((45,y),(420,y))
            if kind=='ink_crossing':page.draw_line((45,179),(420,179))
        doc.save(path)
    doc=pymupdf.open(path);raw=extract.read_page(doc,0)
    probe=extract.ScanPixelProbe(doc,0,mask=[l.bbox for b in raw.text_blocks for l in b.lines])
    return doc,raw,probe


@pytest.mark.parametrize('kind',['table','multiline','grid'])
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


@pytest.mark.parametrize('kind',['no_rules','single_row','crossed_boundary','unmapped_cell','ink_crossing'])
def test_unsupported_sparse_layout_is_not_claimed_as_a_complete_source_table(tmp_path,kind):
    doc,raw,probe=source(tmp_path,kind)
    skel=skeleton.page_skeleton(raw,skeleton.book_style([raw]),pixel_probe=probe)
    assert not any(r.kind=='figure' and r.reason=='source_ruled_table' for r in skel.regions)
    doc.close()


def captioned_scan_grid(tmp_path,damage=None):
    from PIL import Image
    import io
    path=tmp_path/'captioned-grid.pdf'
    with pymupdf.open() as original:
        page=original.new_page(width=842,height=595)
        page.insert_text((107,145),'The following grid introduces the printed conditions.',fontsize=10)
        for x in (107,245,385) if damage!='no_middle' else (107,385):
            if damage=='broken_outer' and x==107:
                page.draw_line((x,174),(x,250));page.draw_line((x,290),(x,391))
            else:page.draw_line((x,174),(x,391))
        for y in (174,201,228,255,282,309,336,363,391) if damage!='no_rows' else (174,391):
            if damage!='no_bottom' or y!=391:page.draw_line((107,y),(385,y))
        rows=[]
        for row,y in enumerate((190,217,244,271,298,325,352,380)):
            left='Left condition %d'%row;right='Right condition %d'%row
            page.insert_text((111,y),left,fontsize=9);page.insert_text((250,y),right,fontsize=9)
            box=(111,y-10,380,y+2)
            spans=[extract.Span(left+' '+right,9,'ocr',0,box,uncertain=row in (0,2,4,6))]
            rows.append(extract.Line(spans,box))
        page.insert_text((176,408),'Figure 3: Printed conditions',fontsize=9)
        page.insert_text((107,440),'The following source paragraph resumes below the grid.',fontsize=10)
        if damage=='crossing':rows.append(extract.Line([extract.Span('crossing source prose',9,'ocr',0,(90,260,420,272))],(90,260,420,272)))
        blocks=[extract.Block(0,(107,130,385,147),[extract.Line([extract.Span('The following grid introduces the printed conditions.',10,'ocr',0,(107,130,385,147))],(107,130,385,147))])]
        blocks += [extract.Block(i+1,line.bbox,[line]) for i,line in enumerate(rows)]
        cap=(176,397,318,410);blocks.append(extract.Block(20,cap,[extract.Line([extract.Span('Figure 3: Printed conditions',9,'ocr',0,cap)],cap)]))
        box=(107,427,385,442);blocks.append(extract.Block(21,box,[extract.Line([extract.Span('The following source paragraph resumes below the grid.',10,'ocr',0,box)],box)]))
        # Small scan skew defeats straight device-row counting; source words
        # still have the OCR engine's merged full-width row boxes.
        pix=page.get_pixmap(matrix=pymupdf.Matrix(2,2),alpha=False)
        im=Image.frombytes('RGB',(pix.width,pix.height),pix.samples)
        skew=.04 if damage=='excess_skew' else .009
        im=im.transform(im.size,Image.Transform.AFFINE,(1,-skew,skew*im.height/2,skew,1,-skew*im.width/2),fillcolor='white')
        data=io.BytesIO();im.save(data,format='PNG')
    with pymupdf.open() as scan:
        page=scan.new_page(width=842,height=595);page.insert_image(page.rect,stream=data.getvalue());scan.save(path)
    doc=pymupdf.open(path);raw=extract.RawPage(0,842,595,blocks,images=[] if damage=='non_scan' else [extract.Image((0,0,842,595),1.0)])
    return doc,raw,extract.ScanPixelProbe(doc,0,mask=[l.bbox for b in raw.text_blocks for l in b.lines])


@pytest.mark.parametrize('damage',[None,'no_middle','no_rows','no_bottom','broken_outer','excess_skew','crossing','non_scan'])
def test_captioned_scan_grid_keeps_merged_ocr_cells_and_rules_in_one_complete_crop(tmp_path,damage):
    doc,raw,probe=captioned_scan_grid(tmp_path,damage)
    style=skeleton.book_style([raw]);skel=skeleton.page_skeleton(raw,style,pixel_probe=probe)
    complete=[r for r in skel.regions if r.kind=='figure' and r.bbox[0]<=107 and r.bbox[1]<=174 and r.bbox[2]>=385 and r.bbox[3]>=391]
    if damage is None:
        assert len(complete)==1,'one source crop must retain the entire skewed grid instead of flattened cell fragments'
        book=assemble.assemble([skel],style,[raw]);assert book.conservation.ok
        assert all('Left condition %d'%row in ' '.join(a['text'] for a in book.artwork) for row in range(8))
        assert not any('Left condition' in element.text for element in book.pages[0] if element.kind=='p')
        assert sum('Figure 3: Printed conditions' in element.text for element in book.pages[0])==1
    else:
        assert not complete,'unsupported or crossing source territory must not be consumed as a complete table'
    doc.close()


def test_closed_scan_grid_does_not_consume_a_partly_overlapping_source_asset(tmp_path):
    from cps.services.reflow import scan_grids
    doc,raw,probe=captioned_scan_grid(tmp_path)
    kept=[(block,block.lines) for block in raw.text_blocks]
    crossing=skeleton.Region(kind='figure',bbox=(90,170,200,220))
    remainder,figures,artwork,notes=scan_grids.regions(raw,kept,probe,[crossing],[])
    assert not artwork and figures==[crossing]
    assert sum(len(lines) for _,lines in remainder)==sum(len(block.lines) for block in raw.text_blocks)
    doc.close()

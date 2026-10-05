"""A source title touching a native embedded figure stays in its printed pixels."""
import copy
from dataclasses import asdict
import pytest
from cps.services.reflow import assemble, extract, skeleton, build_epub
from tests.unit.test_reflow_ruled_notes import line, block
pytestmark = pytest.mark.unit


@pytest.mark.parametrize('damage', [None, 'gap', 'offset', 'ordinary', 'sentence',
    'two_images', 'competing_text', 'uncertain', 'ocr', 'no_probe', 'no_image', 'opening', 'mixed_sizes',
    'encoding_unresolved','transcription_uncertain','punctuation_uncertain','line_uncertain',
    'no_title_ink','no_figure_ink','wrong_page','wrong_frame','below_caption'])
def test_native_touching_figure_title_has_one_source_owner(damage, tmp_path):
    doc=extract.pymupdf.open();page=doc.new_page(width=500,height=700)
    title=line('The celestial sphere of the observer',160,378,11.5)
    title.bbox=(160,378,340,390);title.spans[0].bbox=title.bbox
    if damage=='mixed_sizes':
        title.spans=[extract.Span('T',11.5,'Times-Roman',0,(160,378,167,390)),
            extract.Span('he celestial sphere of the observer',8.6,'Times-Roman',0,(167,378,340,390))]
    prior=line('The preceding source paragraph reaches the variation is',40,345,8.6)
    body=block(0,[prior]);raw=extract.RawPage(0,500,700,
        blocks=[body,block(1,[title])],images=[extract.Image((180,390.5,320,530),.056)])
    if damage!='no_title_ink':page.insert_text((160,387),'The celestial sphere of the observer',fontsize=11)
    if damage!='no_figure_ink':page.draw_rect((180,391,320,530),color=(0,0,0))
    if damage in ('encoding_unresolved','transcription_uncertain','punctuation_uncertain'):
        setattr(title.spans[0],damage,True)
    if damage=='line_uncertain':title.transcription_uncertain=True
    if damage=='wrong_frame':raw.width=510
    if damage=='below_caption':
        raw.blocks.append(block(2,[line('Figure 1. The printed sphere',190,534,7)]))
        page.insert_text((190,540),'Figure 1. The printed sphere',fontsize=7)
    if damage=='gap':raw.images[0].bbox=(180,405,320,545)
    if damage=='offset':raw.images[0].bbox=(250,390.5,390,530)
    if damage=='ordinary':title.spans[0].size=8.6
    if damage=='sentence':title.spans[0].text+='.'
    if damage=='two_images':raw.images.append(copy.deepcopy(raw.images[0]))
    if damage=='competing_text':raw.blocks.append(block(2,[line('Other source words',175,389,8.6)]))
    if damage=='uncertain':title.spans[0].uncertain=True
    if damage=='ocr':title.spans[0].font='ocr'
    if damage=='no_image':raw.images=[]
    if damage=='opening':raw.blocks.remove(body)
    before=asdict(raw);style=skeleton.BookStyle(body_size=8.6)
    probe=None if damage=='no_probe' else extract.ScanPixelProbe(doc,0)
    if damage=='wrong_page':probe._pno=1
    skel=skeleton.page_skeleton(raw,style,pixel_probe=probe)
    book=assemble.assemble([skel],style,[raw])
    assert before==asdict(raw) and book.conservation.ok
    figures=[f for f in book.figures if f.get('found')=='source_figure_title']
    if damage in (None,'mixed_sizes','below_caption'):
        assert len(figures)==1
        assert figures[0]['bbox'][1]<=378 and figures[0]['bbox'][3]>=530
        assert not any(title.stripped in e.text for e in book.elements if e.kind!='fig')
        assert sum(title.stripped in r.text for r in skel.regions if r.kind=='artwork')==1
        html=build_epub.page_fragment(book,0)
        assert 'original-p0000.xhtml#figure_' in html
        assert not any(e.kind=='h' and title.stripped in e.text for e in book.elements)
        owners=book.source_inventory[0]['ownership']
        assert any(o['representation']=='protected_image' for o in owners)
        import io,zipfile
        from PIL import Image,ImageChops
        target=tmp_path/'title.epub';build_epub.build(book,str(target),doc=doc)
        with zipfile.ZipFile(target) as z:
            names=[n for n in z.namelist() if n.rsplit('.',1)[0].endswith('images/fig_p0000_0')
                   and n.rsplit('.',1)[-1] in ('jpg','png')]
            assert len(names)==1
            data=z.read(names[0])
        im=Image.open(io.BytesIO(data)).convert('RGB')
        # This fails if assembly/build extracts only the original image bytes.
        band=im.crop((0,0,im.width,max(1,int(im.height*.065))))
        assert ImageChops.difference(band,Image.new('RGB',band.size,'white')).getbbox()
    else:
        assert not figures, 'unproved title/figure ownership must abstain'
    doc.close()

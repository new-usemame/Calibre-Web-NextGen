"""Original passage details must not cut a neighboring printed row in half."""
import io,json,zipfile
import pymupdf,pytest
from PIL import Image,ImageDraw
from cps.services.reflow import assemble,build_epub,enriched_source
pytestmark=pytest.mark.unit


@pytest.mark.parametrize('role',['passage','caption'])
def test_written_original_passage_keeps_complete_neighbor_ink(tmp_path,role):
    source=Image.new('1',(1200,1600),1);draw=ImageDraw.Draw(source)
    draw.rectangle((60,120,600,160),fill=0)
    # Native paragraph box plus the old fixed pad cuts this neighboring row.
    draw.rectangle((60,210,600,245),fill=0)
    stream=io.BytesIO();source.save(stream,format='PNG');path=tmp_path/'source.pdf'
    with pymupdf.open() as d:
        p=d.new_page(width=300,height=400);p.insert_image(p.rect,stream=stream.getvalue());d.save(path)
    with pymupdf.open(path) as d:
        el=assemble.Element('p' if role=='passage' else 'caption',runs=[['t','Printed passage.']],pno=0,bbox=(14,14,156,51),punctuation_uncertain=role=='passage',caption_uncertain=role=='caption')
        book=assemble.Book(pages={0:[el]},elements=[el]);canonical=enriched_source.prepare_source_page(book,0,{'layer':'native'})
        target=tmp_path/'detail.epub';build_epub.build(book,str(target),doc=d,source_pages={0:canonical})
        assert build_epub.validate(target)==[]
        with zipfile.ZipFile(target) as z:
            side=json.loads(z.read('META-INF/reflow.json'))
            detail=next(e for page in side['source_evidence'] for e in page['details'] if e['id']==('text_0' if role=='passage' else 'caption_orphan'))
            pixels=Image.open(io.BytesIO(z.read('OEBPS/'+detail['src']))).convert('L')
            assert sum(v==0 for v in pixels.tobytes())==541*(41+36),'the source row must survive whole, without invented pixels'
            assert pixels.crop((0,pixels.height-2,pixels.width,pixels.height)).getextrema()==(255,255)
            assert el.text=='Printed passage.'


@pytest.mark.parametrize('kind',['clear','long_ink','vector'])
def test_detail_completion_respects_clear_edges_and_source_proof(tmp_path,kind):
    from cps.services.reflow.source_display import SourceDisplay
    image=Image.new('1',(200,120),1);draw=ImageDraw.Draw(image)
    draw.rectangle((40,40,80,95 if kind=='long_ink' else 49),fill=0)
    stream=io.BytesIO();image.save(stream,format='PNG');path=tmp_path/(kind+'.pdf')
    with pymupdf.open() as d:
        p=d.new_page(width=200,height=120)
        if kind=='vector':p.draw_rect((40,40,80,49),fill=(0,0,0))
        else:p.insert_image(p.rect,stream=stream.getvalue())
        d.save(path)
    with pymupdf.open(path) as d:
        display=SourceDisplay(d,0);rect,proof=display.complete_detail_rect((30,30,90,55))
        if kind=='long_ink':assert rect==display.rect and proof['status']=='full_page_context'
        else:
            assert rect==pymupdf.Rect(30,30,90,55)
            assert proof['status']==('withheld' if kind=='vector' else 'already_clear')

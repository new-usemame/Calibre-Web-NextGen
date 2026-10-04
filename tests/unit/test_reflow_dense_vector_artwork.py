"""A dense traced native chart remains a reading asset, not just a caption."""
import io,zipfile
import pymupdf
from PIL import Image
import pytest
from cps.services.reflow import extract,pipeline,build_epub
pytestmark=pytest.mark.unit

def test_dense_native_vector_chart_survives_extraction_and_publication(tmp_path):
    source=tmp_path/'dense-chart.pdf';doc=pymupdf.open();page=doc.new_page(width=400,height=600)
    shape=page.new_shape()
    for i in range(2700):
        shape.draw_circle((200,170),70+(i%30)/10)
        shape.finish(color=(0,0,0),width=.3)
    shape.commit()
    page.insert_text((140,265),'Diagram 1 - source chart',fontsize=11)
    page.insert_textbox((40,320,360,540), 'This ordinary paragraph explains the original diagram. '*20,fontsize=11)
    doc.save(source);doc.close()
    with extract.open_document(str(source)) as doc:
        raw=extract.read_page(doc,0)
        assert raw.drawings>2500
        result=pipeline.run(doc,recovery_opts={'mode':'off'})
        assert result.book.conservation.ok
        figures=[f for f in result.book.figures if f['pno']==0]
        assert len(figures)==1
        assert 120<figures[0]['bbox'][0]<140 and 240<figures[0]['bbox'][2]<280
        target=tmp_path/'dense-chart.epub';build_epub.build(result.book,str(target),doc=doc,raw_pages={0:raw})
    assert build_epub.validate(target)==[]
    with zipfile.ZipFile(target) as z:
        names=[n for n in z.namelist() if '/images/fig_p0000_' in n]
        assert len(names)==1
        im=Image.open(io.BytesIO(z.read(names[0]))).convert('L')
        assert sum(v<100 for v in im.getdata())>1000
        html=''.join(z.read(n).decode() for n in z.namelist() if '/ch' in n and n.endswith('.xhtml'))
        assert 'Diagram 1' in html and '<figure>' in html

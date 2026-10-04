"""Repeated source disagreement must not become confident elsewhere in a book."""
import copy,io,zipfile
from dataclasses import replace
from unittest.mock import patch
from xml.etree import ElementTree as ET
import pymupdf,pytest
from PIL import Image
from cps.services.reflow import extract,source,pipeline,build_epub,enriched_source
pytestmark=pytest.mark.unit
X='{http://www.w3.org/1999/xhtml}'


def retained(tmp_path,kind='majority'):
    path=tmp_path/(kind+'.pdf')
    prose='Ordinary source prose establishes a readable native paragraph with complete words and sentences. '*3
    with pymupdf.open() as doc:
        for page in range(3):
            with pymupdf.open() as printed:
                p=printed.new_page(width=300,height=300);p.insert_text((40,100),'The child was born here.',fontsize=12)
                assert p.insert_textbox((40,115,270,280),prose,fontsize=9)>=0
                pix=p.get_pixmap(matrix=pymupdf.Matrix(4,4),colorspace=pymupdf.csGRAY)
                im=Image.frombytes('L',(pix.width,pix.height),pix.samples).convert('1',dither=Image.Dither.NONE)
                data=io.BytesIO();im.save(data,format='PNG')
            p=doc.new_page(width=300,height=300);p.insert_image(p.rect,stream=data.getvalue())
            p.insert_text((40,100),'The child was bom here.',fontsize=12,render_mode=3 if kind!='visible' else 0)
            assert p.insert_textbox((40,115,270,280),prose,fontsize=9,render_mode=3 if kind!='visible' else 0)>=0
        doc.save(path)
    doc=pymupdf.open(path);pages=extract.read_pages(doc)
    recovery=source.Recovery(pages=pages)
    for raw in pages:
        spans=[]
        for b in raw.text_blocks:
            for line in b.lines:
                if 'bom' not in line.text:continue
                native=line.spans[0];before,after=native.text.split('bom')
                font=pymupdf.Font('helv');left=native.bbox[0]+font.text_length(before,fontsize=12);right=left+font.text_length('bom',fontsize=12)
                disputed=raw.pno<(1 if kind=='minority' else 2)
                line.spans=[replace(native,text=before,bbox=(native.bbox[0],native.bbox[1],left,native.bbox[3])),
                    replace(native,text='bom',bbox=(left,native.bbox[1],right,native.bbox[3]),transcription_uncertain=disputed),
                    replace(native,text=after,bbox=(right,native.bbox[1],native.bbox[2],native.bbox[3]))]
        recovery.provenance[raw.pno]=source.PageRecovery(raw.pno,layer='native',verification={'version':'native-transcript-agreement-2'})
    return doc,recovery


def test_actual_pipeline_keeps_all_repeated_disputed_native_words_as_pixels(tmp_path):
    doc,recovery=retained(tmp_path)
    before=[' '.join(b.text for b in raw.text_blocks) for raw in recovery.pages]
    with patch.object(source,'recover',return_value=recovery):result=pipeline.run(doc,client=None)
    assert result.book.conservation.ok
    assert [' '.join(b.text for b in raw.text_blocks) for raw in result.raw_pages]==before
    sources={p:enriched_source.prepare_recovery_page(result.book,p,result.recovery) for p in result.book.pages}
    path=tmp_path/'consistent.epub';build_epub.build(result.book,str(path),doc=doc,source_pages=sources,raw_pages={r.pno:r for r in result.raw_pages})
    with zipfile.ZipFile(path) as z:
        roots=[ET.fromstring(z.read(n)) for n in z.namelist() if n.startswith('OEBPS/ch') and n.endswith('.xhtml')]
        images=[a for r in roots for a in r.iter(X+'a') if a.get('class')=='source-glyph']
        assert any(a.get('href','').startswith('original-p0002.xhtml') for a in images),'majority disputed source token must also preserve its third printed occurrence'
    doc.close()


@pytest.mark.parametrize('kind',['minority','visible','different_font','ocr','old_proof'])
def test_one_disagreement_or_visible_native_font_does_not_propagate(tmp_path,kind):
    doc,recovery=retained(tmp_path,kind)
    if kind=='different_font':
        for b in recovery.pages[2].text_blocks:
            for line in b.lines:
                for span in line.spans:span.font='Other source font'
    elif kind=='ocr':recovery.provenance[2].layer='ocr'
    elif kind=='old_proof':recovery.provenance[2].verification={}
    with patch.object(source,'recover',return_value=recovery):result=pipeline.run(doc,client=None)
    assert result.book.conservation.ok
    assert not any(s.transcription_uncertain for b in result.raw_pages[2].text_blocks for l in b.lines for s in l.spans)
    doc.close()

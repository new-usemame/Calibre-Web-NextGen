"""Authored synthetic OCR receipts and blank procedural source geometry.

The integration controls use invented common words, with no source transcript.
"""
import io

import pymupdf
import pytest
from PIL import Image

from cps.services.reflow import local_reading, ocr, unit_recovery, vision_pipeline, vision_pixels
from cps.services.reflow.source_display import SourceDisplay

pytestmark = pytest.mark.unit
TEXT = 'The narrow bridge crosses the quiet stream.'


def engine(monkeypatch, clockwise=90):
    calls = []
    monkeypatch.setattr(ocr, '_engine', lambda language: ('synthetic-engine', 'synthetic-version', 'synthetic-data'))
    def invoke(args, directory, **kwargs):
        calls.append((args, kwargs))
        picture = Image.open(io.BytesIO((directory/'page.png').read_bytes()))
        assert picture.width * picture.height <= 8_000_000
        if 'osd' in args:
            assert kwargs == dict(timeout=30, should_stop=None)
            assert args[-4:] == ['-l', 'osd', '--psm', '0']
            return 0, f'Rotate: {clockwise}\nOrientation confidence: 22.5\n', ''
        assert kwargs == dict(timeout=120, should_stop=None, watched=(directory/'result.tsv',))
        rows = ['level\tblock_num\tpar_num\tline_num\tleft\ttop\twidth\theight\tconf\ttext']
        for index, text in enumerate(TEXT.split()):
            rows.append(f'5\t1\t1\t1\t{20+index*36}\t40\t30\t20\t96\t{text}')
        (directory/'result.tsv').write_text('\n'.join(rows)+'\n')
        return 0, '', ''
    monkeypatch.setattr(ocr, '_invoke', invoke)
    return calls


@pytest.mark.parametrize('clockwise', [0, 90, 180, 270])
def test_local_orientation_receipt_is_bounded_cached_and_restores_source_space(tmp_path, monkeypatch, clockwise):
    calls = engine(monkeypatch, clockwise)
    doc = pymupdf.open()
    doc.new_page(width=300, height=400)
    try:
        display = SourceDisplay(doc, 0)
        reading = local_reading.read_oriented(display, tmp_path, 'Synthetic unavailable reading')
        upright = local_reading.reading_display(display, reading)
        assert upright.angle == clockwise
        assert tuple(upright.rect) == ((0, 0, 400, 300) if clockwise % 180 else (0, 0, 300, 400))
        assert reading['answer']['blocks'][0]['low_confidence'] == list(range(len(TEXT.split())))
        assert all(w['confidence'] == 96 for u in reading['local_ocr_units'] for w in u['words'])
        again = local_reading.read_oriented(display, tmp_path, 'Synthetic unavailable reading')
        assert again['local_ocr_orientation']['cached']
        assert again['local_ocr_evidence']['cached']
        assert len(calls) == (3 if clockwise % 180 else 2), 'One orientation plus one transcript per source tile; receipts are reused'
        assert not list(tmp_path.glob('local-osd-*'))
        assert not list(tmp_path.glob('local-ocr-*'))
        for unit in reading['local_ocr_units']:
            for word in unit['words']:
                source = upright.source_rect(word['bbox'])
                assert not source.is_empty and source == source & display.rect
    finally:
        doc.close()


@pytest.mark.parametrize('change', ['missing', 'adapter', 'engine', 'source', 'angle', 'confidence', 'configuration', 'renderer', 'missing_angle', 'boolean_angle', 'boolean_score', 'image_hash'])
def test_checkpoint_orientation_rejects_obsolete_or_changed_source_receipts(tmp_path, monkeypatch, change):
    engine(monkeypatch)
    doc = pymupdf.open()
    doc.new_page(width=300, height=400)
    try:
        reading = local_reading.read_oriented(SourceDisplay(doc, 0), tmp_path, 'Synthetic unavailable reading')
        if change == 'missing':
            del reading['local_ocr_orientation']
        elif change == 'adapter':
            reading['local_ocr_orientation']['identity']['adapter'] = 'synthetic-obsolete-adapter'
        elif change == 'engine':
            monkeypatch.setattr(ocr, '_engine', lambda language: ('synthetic-engine', 'synthetic-new-version', 'synthetic-data'))
        elif change == 'angle':
            reading['local_ocr_orientation']['identity']['source_orientation'] = 180
        elif change == 'renderer':
            reading['local_ocr_orientation']['identity']['renderer'] = 'synthetic-other-renderer'
        elif change == 'missing_angle':
            del reading['local_ocr_orientation']['identity']['source_orientation']
        elif change == 'boolean_angle':
            reading['local_ocr_orientation']['clockwise'] = True
        elif change == 'boolean_score':
            reading['local_ocr_orientation']['confidence'] = True
        elif change == 'image_hash':
            reading['local_ocr_orientation']['identity']['image'] = 'synthetic-malformed-digest'
        elif change == 'configuration':
            reading['local_ocr_orientation']['identity']['max_pixels'] = 1
        elif change == 'confidence':
            reading['local_ocr_orientation']['confidence'] = 1
        else:
            doc[0].insert_text((20, 30), 'A new synthetic source mark.')
        with pytest.raises(unit_recovery.SourceIntegrityError):
            local_reading.reading_display(SourceDisplay(doc, 0), reading)
    finally:
        doc.close()


def test_prepared_checkpoint_restores_upright_independent_guard_without_repeating_ocr(tmp_path, monkeypatch):
    calls = engine(monkeypatch)
    doc = pymupdf.open()
    doc.new_page(width=300, height=400)
    class Reader:
        cache = tmp_path/'reader'
        key = None
        def read_unit(self, *args, **kwargs):
            return dict(degraded_source=True, failure='Synthetic unavailable reading')
    reader = Reader()
    seen = []
    def optical(display, cache):
        seen.append(display.angle)
        return [dict(text=text, bbox=[10+index*18, 20, 25+index*18, 30])
                for index, text in enumerate(TEXT.split())]
    monkeypatch.setattr(vision_pixels, 'words', optical)
    controller = unit_recovery.Controller(reader, [0], tmp_path)
    try:
        with unit_recovery.activated(controller):
            first, _ = vision_pipeline._prepare_scanned_page(doc, 0, reader, controller, None, None)
            second, _ = vision_pipeline._prepare_scanned_page(doc, 0, reader, controller, None, None)
        assert seen == [0, 90], 'Independent guard is re-read in the upright space and checkpointed'
        assert first['display'].angle == second['display'].angle == 90
        assert first['reading']['answer'] == second['reading']['answer']
        assert first['optical'] == second['optical']
        assert len(calls) == 3, 'One orientation and the two upright source-tile transcripts are checkpointed'
        assert second['reading']['answer']['blocks'][0]['low_confidence'] == list(range(len(TEXT.split())))
        assert controller.images[0]['sha256'], 'Primary image identity remains separately retained'
    finally:
        doc.close()


def test_rotated_optional_detail_frame_uses_proved_prepared_pixels(tmp_path, monkeypatch):
    import hashlib
    engine(monkeypatch)
    path = tmp_path/'synthetic.pdf'
    doc = pymupdf.open()
    doc.new_page(width=300, height=400)
    doc.save(path)
    doc.close()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    frame = dict(original_pdf=str(path), original_pdf_sha256=digest,
        prepared_pdf_sha256=digest, original_page_index0=0, prepared_page_index0=0)
    with pymupdf.open(path) as doc:
        display = SourceDisplay(doc, 0, dict(original_frame=frame))
        reading = local_reading.read_oriented(display, tmp_path/'cache', 'Synthetic unavailable reading')
        upright = local_reading.reading_display(display, reading)
        assert upright.angle == 90
        assert 'original_frame' not in upright.provenance
        assert reading['local_ocr_evidence']['detail_source'] == 'proved_prepared_pixels'
        assert upright.roi_pixmap([10, 10, 30, 30], scale=2).width == 40
        path.write_bytes(path.read_bytes()+b'\n% synthetic changed source identity\n')
        with pytest.raises(unit_recovery.SourceIntegrityError):
            local_reading.reading_display(display, reading)


from collections import Counter
import io
import re
import shutil
import zipfile
from xml.etree import ElementTree as ET
import pymupdf
import pytest
from PIL import Image
from cps.services.reflow import vision_pipeline as vp, vision_reading as vr, build_epub
from cps.services.reflow.ledger import Ledger

ORIENTATION_LINES = [
    'Fresh bread rests beside a clean basket.',
    'The small garden has a clear quiet path.',
    'A gentle wind moves through the green trees.',
    'The bright window brings warm light into the room.',
    'We carry clean water to the young plants.',
    'The open gate leads toward a wide green field.',
    'A little bird rests on the wooden fence.',
    'The clean white cloth covers a round table.',
    'We place the empty basket beside the window.',
    'The soft grass grows along the quiet path.',
    'A small bridge crosses the clear narrow stream.',
    'The warm bread waits beside the clean water.',
    'We walk through the garden before the rain.',
    'The gentle rain falls across the green field.',
    'A quiet room offers time for a clear thought.',
    'We close the wooden gate and carry the basket.',
]
NS={'x':'http://www.w3.org/1999/xhtml'}

def _orientation_tokens(text):
    return re.findall(r'[A-Za-z]+', text.casefold())

def _orientation_raster_fixture(angle, spread):
    width,height=460*(2 if spread else 1),650
    source=pymupdf.open();p=source.new_page(width=width,height=height)
    for leaf in range(2 if spread else 1):
        lines=ORIENTATION_LINES if leaf==0 else [s.replace('Fresh','Soft').replace('bread','cloth').replace('garden','window')for s in ORIENTATION_LINES]
        for n,line in enumerate(lines):p.insert_text((30+460*leaf,65+28*n),line,fontsize=12)
    image=Image.open(io.BytesIO(p.get_pixmap(matrix=pymupdf.Matrix(2,2)).tobytes('png')))
    image=image.rotate(angle,expand=True);data=io.BytesIO();image.save(data,format='PNG')
    doc=pymupdf.open();page=doc.new_page(width=image.width/2,height=image.height/2);page.insert_image(page.rect,stream=data.getvalue())
    anchor=next(w[:4] for w in p.get_text('words') if w[4]=='Fresh')
    source.close()
    expected=' '.join(ORIENTATION_LINES)
    if spread:expected+=' '+' '.join(s.replace('Fresh','Soft').replace('bread','cloth').replace('garden','window')for s in ORIENTATION_LINES)
    return doc,expected,width,height,anchor

@pytest.mark.skipif(shutil.which('tesseract') is None, reason='requires local OCR engine')
@pytest.mark.parametrize('angle,spread',[(0,False),(90,False),(180,False),(270,False),(90,True),(270,True)])
def test_scan_orientation_preserves_reading_geometry_and_uncertainty(tmp_path,angle,spread):
    doc,expected,width,height,anchor=_orientation_raster_fixture(angle,spread)
    reader=vr.Reader(None,tmp_path/'cache/readings',Ledger(tmp_path/'ledger.jsonl',0))
    out=tmp_path/'reading.epub'
    try:report=vp.convert(doc,str(out),reader)
    finally:doc.close()
    assert build_epub.validate(out)==[]
    assert reader.ledger.spent()==reader.ledger.pending_usd()==0
    assert not list(reader.cache.glob('*.json'))
    with zipfile.ZipFile(out)as z:
        documents={n:ET.fromstring(z.read(n))for n in z.namelist()if n.startswith('OEBPS/ch')and n.endswith('.xhtml')}
        owned=[s for root in documents.values()for s in root.findall('.//x:span',NS)if s.get('data-source-page')=='0']
        actual=' '.join(''.join(s.itertext())for s in owned)
        gold=Counter(_orientation_tokens(expected));got=Counter(_orientation_tokens(actual));retained=sum((gold&got).values())
        assert retained/sum(gold.values())>=.95, (retained,sum(gold.values()),actual)
        assert report['pages'][0]['local_ocr']
        original=next(w for u in report['pages'][0]['local_ocr_units'] for w in u['words'] if w['text']=='Fresh')
        box=original['bbox'];center=((box[0]+box[2])/2,(box[1]+box[3])/2)
        assert anchor[0]-5<=center[0]<=anchor[2]+5 and anchor[1]-5<=center[1]<=anchor[3]+5, 'Uncertainty coordinates own the printed word in the upright source frame'
        uncertain=[s for parent in owned for s in parent.findall('.//x:span',NS)if s.get('class')=='uncertain']
        marked=Counter(_orientation_tokens(' '.join(''.join(s.itertext())for s in uncertain)))
        assert got==marked,'Every local lexical word remains explicitly unverified'
        assert any(a.get('class')=='source-disclosure-ref'for root in documents.values()for a in root.findall('.//x:a',NS))
        source=Image.open(io.BytesIO(z.read('OEBPS/images/v4-local-ocr-source-0.jpg')))
        assert abs(source.width/source.height-width/height)<.03,'Original-page disclosure uses the same upright reading frame'
        if spread:
            assert actual.casefold().find('fresh')<actual.casefold().find('soft'),'Left leaf precedes right leaf after orientation'
        for root in documents.values():
            assert not any(a.get('class')=='noteref'for a in root.findall('.//x:a',NS))

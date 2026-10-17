"""Synthetic display resources exercise bounds without recognition or network."""
import io
import zipfile
import pymupdf
import pytest
from PIL import Image
from cps.services.reflow import build_epub


def test_identical_region_resources_share_one_archive_member(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=240, height=320)
    page.insert_text((20, 40), 'A clear printed sample.', fontsize=12)
    data = page.get_pixmap().tobytes('jpg')
    package = build_epub._Package(tmp_path/'synthetic.epub')
    try:
        package.image('images/first.jpg', data, deduplicate=True)
        package.image('images/second.jpg', data, deduplicate=True)
        assert len(package.images) == 1
        assert package.aliases['images/second.jpg'] == 'images/first.jpg'
        assert package._zf.namelist().count('OEBPS/images/first.jpg') == 1
        assert 'OEBPS/images/second.jpg' not in package._zf.namelist()
        assert build_epub._image_aliases('<img src="images/second.jpg"/>', package.aliases) == '<img src="images/first.jpg"/>'
    finally:
        package.abandon()
        doc.close()


def test_book_gate_rejects_old_repeated_full_frame_size():
    from cps.services.reflow.image_budget import check, OutputTooLarge
    with pytest.raises(OutputTooLarge):
        check(10*1024*1024, 947*1024*1024)
    assert check(10*1024*1024, 150*1024*1024)['passed']
    assert check(80*1024*1024, 240*1024*1024)['passed']
    with pytest.raises(OutputTooLarge):
        check(80*1024*1024, 240*1024*1024+1)


def test_inspection_details_reuse_one_page_render_and_keep_small_type(tmp_path, monkeypatch):
    from cps.services.reflow.image_budget import EvidenceRaster, EVIDENCE_PIXELS
    from cps.services.reflow.source_display import SourceDisplay
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=800)
    page.insert_text((30, 60), 'Small type remains visible.', fontsize=6)
    display = SourceDisplay(doc, 0)
    calls = []
    actual = display.roi_pixmap
    def render(*args, **kwargs):
        calls.append(kwargs)
        result = actual(*args, **kwargs)
        assert result.width*result.height <= EVIDENCE_PIXELS
        return result
    monkeypatch.setattr(display, 'roi_pixmap', render)
    raster = EvidenceRaster()
    first = raster.jpeg(display, (20, 40, 240, 90))
    second = raster.jpeg(display, (20, 40, 240, 90))
    full = raster.jpeg(display, display.rect)
    assert first == second and len(calls) == 1
    with Image.open(io.BytesIO(first)) as detail:
        assert detail.width >= 660 and detail.height >= 150
        assert detail.convert('L').getextrema()[0] < 80
    with Image.open(io.BytesIO(full)) as preview:
        assert preview.size == (1200, 1600)
    doc.close()


def test_final_archive_gate_precedes_publication_and_removes_candidate(tmp_path, monkeypatch):
    from cps.services.reflow import image_budget
    monkeypatch.setattr(image_budget, 'MIN_BOOK_BYTES', 128)
    target = tmp_path/'too-large.epub'
    package = build_epub._Package(target)
    parts = dict(sidecar={}, opf='', nav='', ncx='', documents={})
    with pytest.raises(image_budget.OutputTooLarge):
        package.finish(parts)
    assert not target.exists() and not target.with_suffix('.epub.tmp').exists()


def test_reused_source_name_rejects_changed_bytes_after_deduplication(tmp_path):
    from cps.services.reflow import unit_recovery
    package = unit_recovery.package(tmp_path/'source.epub')
    image = Image.new('RGB', (20, 20), 'white')
    stream = io.BytesIO(); image.save(stream, format='JPEG')
    data = stream.getvalue()
    try:
        package.source_image('images/a.jpg', data)
        package.source_image('images/b.jpg', data)
        assert package.aliases['images/b.jpg'] == 'images/a.jpg'
        with pytest.raises(unit_recovery.SourceIntegrityError):
            package.source_image('images/b.jpg', data+b'changed')
        assert 'images/b.jpg' not in package.images
    finally:
        package.abandon()


def test_cached_original_frame_rechecks_source_identity(tmp_path):
    import hashlib
    from cps.services.reflow import image_budget, unit_recovery
    from cps.services.reflow.source_display import SourceDisplay
    original, prepared = tmp_path/'original.pdf', tmp_path/'prepared.pdf'
    doc = pymupdf.open(); page = doc.new_page(width=240, height=320)
    page.insert_text((20, 50), 'Original source identity is checked.')
    doc.save(original); doc.close(); prepared.write_bytes(original.read_bytes())
    doc = pymupdf.open(prepared)
    digest = hashlib.sha256(original.read_bytes()).hexdigest()
    display = SourceDisplay(doc, 0, dict(original_frame=dict(original_pdf=str(original),
        original_pdf_sha256=digest, prepared_pdf_sha256=digest,
        original_page_index0=0, prepared_page_index0=0)))
    raster = image_budget.EvidenceRaster()
    try:
        raster.jpeg(display, display.rect)
        original.write_bytes(original.read_bytes()+b'changed')
        with pytest.raises(unit_recovery.SourceIntegrityError):
            raster.jpeg(display, (20, 20, 100, 100))
    finally:
        doc.close()


def test_large_page_detail_retains_requested_sampling():
    from cps.services.reflow.image_budget import EvidenceRaster
    from cps.services.reflow.source_display import SourceDisplay
    doc = pymupdf.open(); page = doc.new_page(width=1200, height=1600)
    page.insert_text((30, 60), 'Fine printed detail.', fontsize=4)
    try:
        data = EvidenceRaster().jpeg(SourceDisplay(doc, 0), (20, 40, 220, 90))
        with Image.open(io.BytesIO(data)) as image:
            assert image.size == (600, 150)
            assert image.convert('L').getextrema()[0] < 80
    finally:
        doc.close()


def test_repaired_archive_budget_rejection_removes_all_candidates(tmp_path, monkeypatch):
    from cps.services.reflow import image_budget, unit_recovery
    class Controller:
        events = []
        def decorate_report(self, report):
            pass
    monkeypatch.setattr(unit_recovery, 'current', lambda: Controller())
    monkeypatch.setattr(image_budget, 'MIN_BOOK_BYTES', 1)
    def broken_finalization(*args, **kwargs):
        raise ValueError('Synthetic packaging failure')
    monkeypatch.setattr(build_epub._Package, 'finish', broken_finalization)
    target = tmp_path/'repair.epub'
    package = unit_recovery.package(target)
    try:
        with pytest.raises(image_budget.OutputTooLarge):
            package.finish(dict(sidecar={}, documents={}, opf='<root/>',
                                nav='<root/>', ncx='<root/>'))
        assert not list(tmp_path.glob('repair.epub*'))
    finally:
        package.abandon()


def test_whole_page_disclosures_share_bytes_without_merging_distinct_crop_owners(tmp_path):
    from cps.services.reflow.source_display import SourceDisplay
    doc=pymupdf.open();page=doc.new_page(width=240,height=320)
    page.insert_text((20,40),'Synthetic whole page context.')
    package=build_epub._Package(tmp_path/'context.epub')
    try:
        display=SourceDisplay(doc,0)
        package.disclosure_image('images/page-a.jpg',display,display.rect)
        package.disclosure_image('images/page-b.jpg',display,display.rect)
        assert package.aliases['images/page-b.jpg']=='images/page-a.jpg'
        crop=pymupdf.Rect(20,20,220,60)
        package.disclosure_image('images/region-a.jpg',display,crop)
        package.disclosure_image('images/region-b.jpg',display,crop)
        assert len(package.images)==3
        assert 'images/region-a.jpg' not in package.aliases
        assert 'images/region-b.jpg' not in package.aliases
    finally:
        package.abandon();doc.close()


def test_gray_figure_frame_uses_lossless_single_channel_storage():
    source=Image.new('L',(160,120),'white')
    for y in range(10,110):
        for x in range(0,80):
            source.putpixel((x,y),(x*17+y*13)%256)
    stream=io.BytesIO();source.convert('RGB').save(stream,format='JPEG',quality=85)
    encoded=stream.getvalue()
    with Image.open(io.BytesIO(encoded)) as decoded:
        original=decoded.convert('RGB').copy()
    framed=build_epub._figure_crop_margin(encoded)
    with Image.open(io.BytesIO(framed)) as result:
        assert result.mode=='L'
        assert result.convert('RGB').crop((8,8,168,128)).tobytes()==original.tobytes()
        assert result.size==(176,136)


def test_deduplicated_image_alias_cannot_be_rendered_again(tmp_path):
    package = build_epub._Package(tmp_path/'aliases.epub')
    stream = io.BytesIO()
    Image.new('RGB', (20, 20), 'white').save(stream, format='JPEG')
    data = stream.getvalue()
    try:
        package.image('images/a.jpg', data, deduplicate=True)
        package.image('images/b.jpg', data, deduplicate=True)
        assert package.aliases['images/b.jpg'] == 'images/a.jpg'
        with pytest.raises(ValueError, match='rendered twice'):
            package.image('images/b.jpg', data+b'changed')
        assert 'images/b.jpg' not in package.images
    finally:
        package.abandon()


def _procedural_bilevel_page():
    """Draw every source bit locally; no captured page, font or OCR input."""
    from PIL import ImageDraw
    pixels=Image.new('1',(1200,1500),1)
    draw=ImageDraw.Draw(pixels)
    # Small repeated glyph-like marks include one-pixel strokes. Exact sample
    # comparison below proves these stay legible without asking a recognizer.
    for y in range(30,1450,26):
        for x in range(25,1150,17):
            draw.rectangle((x,y,x+2,y+12),fill=0)
            draw.line((x,y,x+9,y),fill=0,width=2)
            draw.line((x,y+6,x+7,y+6),fill=0,width=1)
            if (x//17+y//26)%3==0:
                draw.rectangle((x+7,y+1,x+9,y+12),fill=0)
    # Distinct outer-corner marks prove the whole page was retained.
    draw.rectangle((0,0,8,8),fill=0)
    draw.line((1190,1499,1199,1490),fill=0,width=1)
    stream=io.BytesIO();pixels.save(stream,format='PNG',optimize=True)
    doc=pymupdf.open();page=doc.new_page(width=400,height=500)
    page.insert_image(page.rect,stream=stream.getvalue())
    return doc,pixels


def test_bilevel_source_contexts_share_one_exact_small_original(tmp_path):
    import zlib
    from cps.services.reflow import unit_source,unit_recovery
    from cps.services.reflow.source_display import SourceDisplay
    doc,pixels=_procedural_bilevel_page()
    package=unit_recovery.package(tmp_path/'bitonal-context.epub')
    try:
        display=SourceDisplay(doc,0)
        original_jpeg=display.roi_jpeg(display.rect,scale=2,quality=90)
        images={}
        first=unit_source.source_unit(display,0,'first','body',package,images,
            'Synthetic unresolved region')[2]
        second=unit_source.source_unit(display,0,'second','footnote',package,images,
            'Another synthetic unresolved region')[2]
        assert first['source_image']==second['source_image']
        assert len(package.images)==1
        href=first['source_image']
        encoded=package._zf.read('OEBPS/'+href)
        with Image.open(io.BytesIO(encoded)) as actual:
            assert actual.format=='PNG'
            assert actual.size==pixels.size
            assert actual.convert('L').tobytes()==pixels.convert('L').tobytes()
        # Compare packaged bytes, not only the encoders' uncompressed lengths.
        actual_bytes=package._zf.getinfo('OEBPS/'+href).compress_size
        legacy_bytes=len(zlib.compress(original_jpeg,6))
        assert actual_bytes*8 < legacy_bytes
        assert actual_bytes < 16*1024
    finally:
        package.abandon();doc.close()


def test_bilevel_original_and_enlarged_detail_preserve_every_source_pixel(tmp_path):
    import zlib
    from cps.services.reflow.image_budget import EVIDENCE_PIXELS
    from cps.services.reflow.source_display import SourceDisplay
    doc,pixels=_procedural_bilevel_page()
    package=build_epub._Package(tmp_path/'bitonal-details.epub')
    try:
        display=SourceDisplay(doc,0);detail=pymupdf.Rect(20,20,180,80)
        old_whole=display.roi_jpeg(display.rect,scale=2,quality=90)
        old_detail=display.roi_jpeg(detail,scale=3,quality=90)
        package.disclosure_image('images/full.jpg',display,display.rect)
        package.disclosure_image('images/detail.jpg',display,detail)
        assert len(package.images)==2
        expected={'images/full.jpg':pixels,'images/detail.jpg':pixels.crop((60,60,540,240))}
        total=0
        for requested,source in expected.items():
            href=package.aliases.get(requested,requested)
            encoded=package._zf.read('OEBPS/'+href)
            with Image.open(io.BytesIO(encoded)) as actual:
                assert actual.format=='PNG'
                assert actual.size==source.size
                assert actual.width*actual.height <= EVIDENCE_PIXELS
                assert actual.convert('L').tobytes()==source.convert('L').tobytes()
            total+=package._zf.getinfo('OEBPS/'+href).compress_size
        legacy_total=sum(len(zlib.compress(data,6)) for data in (old_whole,old_detail))
        assert total*8 < legacy_total
        assert total < 20*1024
    finally:
        package.abandon();doc.close()


def test_native_evidence_retains_at_most_one_frame_across_retained_page_displays(tmp_path):
    import gc
    import weakref
    from cps.services.reflow import unit_recovery,unit_source
    from cps.services.reflow.source_display import SourceDisplay
    source,_=_procedural_bilevel_page();doc=pymupdf.open()
    for _ in range(8):doc.insert_pdf(source)
    # Conversion keeps every page display alive until cross-page assembly.
    # Image evidence must still release the preceding page's native samples.
    scans={n:dict(display=SourceDisplay(doc,n)) for n in range(len(doc))}
    package=unit_recovery.package(tmp_path/'retained-pages.epub')
    references=[];records={}
    try:
        for n,scan in scans.items():
            display=scan['display']
            package.disclosure_image('images/context-%d.jpg'%n,display,display.rect)
            unit_source.source_unit(display,n,'body','body',package,records,
                'Synthetic source context')
            native=display.__dict__.get('_bitonal')
            if native is not None:references.append(weakref.ref(native))
            del native
            gc.collect()
            assert sum(reference() is not None for reference in references) <= 1
    finally:
        package.abandon();source.close();doc.close()

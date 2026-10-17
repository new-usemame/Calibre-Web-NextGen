"""A procedural source-image EPUB is checked by the optional external validator."""
import io
from pathlib import PurePosixPath
import posixpath
import shutil
import subprocess
from xml.etree import ElementTree as ET
import zipfile

from PIL import Image
import pytest

from cps.services.reflow import vision_pipeline, vision_pixels
from tests.unit.test_reflow_image_budget import _procedural_bilevel_page
from tests.unit.test_reflow_vision_pilot import SyntheticReader

pytestmark = pytest.mark.unit
EPUBCHECK = shutil.which('epubcheck')


@pytest.mark.skipif(EPUBCHECK is None, reason='optional epubcheck executable is unavailable')
def test_source_image_epub_passes_epubcheck_with_matching_encoded_resource_names(tmp_path, monkeypatch):
    monkeypatch.setattr(vision_pipeline, 'clean_native', lambda *args, **kwargs: False)
    monkeypatch.setattr(vision_pixels, 'words', lambda *args, **kwargs: [])
    # This reader has no answer. The procedural bilevel page must therefore be
    # retained as a disclosed source image, without recognition or network I/O.
    reader = SyntheticReader()
    destination = tmp_path / 'synthetic-source.epub'
    doc, original = _procedural_bilevel_page()
    try:
        report = vision_pipeline.convert(doc, str(destination), reader,
            metadata={'title': 'Synthetic source resource', 'authors': ['Synthetic fixture']})
    finally:
        doc.close()
    assert report['pages'][0]['source'] == 'original_source'
    checked = subprocess.run([EPUBCHECK, '--failonwarnings', str(destination)], capture_output=True,
                             text=True, timeout=120)
    diagnostics = checked.stdout + checked.stderr
    assert checked.returncode == 0, diagnostics
    assert 'PKG-022' not in diagnostics, diagnostics
    with zipfile.ZipFile(destination) as archive:
        manifest = ET.fromstring(archive.read('OEBPS/content.opf'))
        resources = [item for item in manifest.findall('.//{http://www.idpf.org/2007/opf}item')
                     if item.get('media-type', '').startswith('image/')]
        assert resources
        found_png = False
        for item in resources:
            member = posixpath.normpath(posixpath.join('OEBPS', item.attrib['href']))
            with Image.open(io.BytesIO(archive.read(member))) as image:
                extension, mime = {'PNG': ('.png', 'image/png'),
                                   'JPEG': ('.jpg', 'image/jpeg')}[image.format]
                assert PurePosixPath(member).suffix == extension
                assert item.attrib['media-type'] == mime
                found_png |= image.format == 'PNG'
        assert found_png, 'the lossless source page must exercise PNG resource naming'

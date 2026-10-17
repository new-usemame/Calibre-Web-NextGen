"""Procedural image bytes retain their real format through source packaging."""
import io
import zipfile
from pathlib import PurePosixPath
import pytest
from PIL import Image
from cps.services.reflow import build_epub, unit_recovery


def encoded(fmt):
    output=io.BytesIO()
    Image.new('RGB',(12,12),'white').save(output,format=fmt)
    return output.getvalue()


@pytest.mark.parametrize('fmt,requested,extension,mime',[
    ('PNG','images/source.jpg','.png','image/png'),
    ('JPEG','images/source.png','.jpg','image/jpeg'),
    ('PNG','images/source.jpeg','.png','image/png'),
])
def test_original_resources_use_encoded_format_and_keep_alias_ownership(tmp_path,fmt,requested,extension,mime):
    package=unit_recovery.package(tmp_path/'source.epub')
    try:
        data=encoded(fmt)
        package.source_image(requested,data)
        actual=package.aliases.get(requested,requested)
        assert PurePosixPath(actual).suffix==extension
        assert package.media_types[actual]==mime
        assert package._zf.read('OEBPS/'+actual)==data
        assert build_epub._image_aliases('<img src="'+requested+'"/>',package.aliases)=='<img src="'+actual+'"/>'
        package.source_image(requested,data)
        assert len(package.images)==1
        with pytest.raises(unit_recovery.SourceIntegrityError):
            package.source_image(requested,data+b'changed')
        package.source_image('images/duplicate.jpg',data)
        assert len(package.images)==1
        assert package.aliases['images/duplicate.jpg']==actual
    finally:
        package.abandon()


@pytest.mark.parametrize('fmt,requested,extension',[
    ('PNG','images/source.jpeg','.png'),
    ('JPEG','images/source.png','.jpg'),
])
def test_ordinary_resources_use_the_same_format_rule(tmp_path,fmt,requested,extension):
    package=build_epub._Package(tmp_path/'source.epub')
    try:
        package.image(requested,encoded(fmt))
        actual=package.aliases.get(requested,requested)
        assert PurePosixPath(actual).suffix==extension
        assert actual in package.images
    finally:
        package.abandon()

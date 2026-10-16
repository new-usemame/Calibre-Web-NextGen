"""SOURCE visibility and no invented selected-note binding under public recovery."""
import io,posixpath,zipfile
from xml.etree import ElementTree as ET
from PIL import Image
NS={'x':'http://www.w3.org/1999/xhtml'}
def source_visible(path,report,expected_pages):
    units=report.get('degraded_units',[])
    assert units,'public recovery must report affected SOURCE unit'
    assert set(map(int,report['page_homes']))==set(expected_pages)
    with zipfile.ZipFile(path) as z:
        roots={n:ET.fromstring(z.read(n)) for n in z.namelist() if n.endswith('.xhtml')}
        shown={posixpath.normpath(posixpath.join(posixpath.dirname(n),img.get('src'))) for n,root in roots.items() for img in root.findall('.//x:img',NS)}
        for unit in units:
            assert unit.get('stage') and unit.get('reason')
            src=unit['source_image'];member=src if src.startswith('OEBPS/') else 'OEBPS/'+src
            assert member in z.namelist() and member in shown,'SOURCE unit must actually display original image'
            assert Image.open(io.BytesIO(z.read(member))).convert('L').getextrema()[0]<100,'SOURCE unit must preserve original ink'
        controls=[a for root in roots.values() for a in root.findall('.//x:a',NS) if 'source-disclosure-ref' in (a.get('class') or '').split()]
        for unit in units:
            if unit.get('unit_kind')=='footnote':
                targets=[a for root in roots.values() for a in root.findall('.//x:aside',NS) if a.get('id')==unit['source_reference'] and 'vision-disclosure' in (a.get('class') or '').split()]
                assert len(targets)==1 and targets[0].find('.//x:img',NS) is not None,'SOURCE note must visibly disclose its actual retained image without inventing an origin'
                assert ''.join(targets[0].itertext()).strip(),'SOURCE note must display an explicit reason'
            else:
                assert controls,'affected non-note unit requires a visible source control'

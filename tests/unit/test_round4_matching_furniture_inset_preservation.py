"""Original marginal folio ink bounds artwork without erasing authored labels."""
import importlib.util
import io
from pathlib import Path
import zipfile
import pytest
from PIL import Image
_spec=importlib.util.spec_from_file_location('folio_boundary_helpers',Path(__file__).with_name('test_round4_defects.py'))
h=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(h)

@pytest.mark.parametrize('kind',['diagram_number','nonnumeric_inset'])
def test_source_folio_crop_boundary(tmp_path,monkeypatch,kind):
    page=h.InkPage()
    rect=page.figure()
    if kind in ('diagram_number','nonnumeric_inset'):
        text='314' if kind=='diagram_number' else 'NORTH'
        page.page.insert_text((65,120),text,fontsize=8,color=(1,0,0))
        # A separately labelled inset is still authored figure ink: its hint
        # intersects the original figure seed and is not a marginal folio.
        page.blocks.insert(0,h.model_block(text,'inset','folio' if kind=='diagram_number' else 'running_head',[64,110,98,124]))
    else:
        role='heading' if kind=='authored_heading' else 'folio'
        text='314' if role=='folio' else 'A Northern Journey'
        y=382 if kind=='bottom_folio' else 26
        size=14 if role=='heading' else 8
        page.text(text,'margin',role,x=60,y=y,size=size,color=(1,0,0))
        page.blocks[-1]['bbox']=h.model_block(text,'unused',role,[59,y-size-3,185 if role=='heading' else 80,y+3])['bbox']
    page.text('A clear ordinary paragraph remains after the complete illustration.','body',y=235)
    original=page.finish
    def missing_folio_optical():
        image,words,answer=original()
        if kind in ('top_folio','bottom_folio'):
            words=[w for w in words if w['text']!='314']
        return image,words,answer
    page.finish=missing_folio_optical
    epub,result,docs=h.convert_ink(tmp_path,monkeypatch,[page])
    assets=[(src,r) for src,r in result['images'].items() if r['kind']=='figure']
    assert len(assets)==1
    src,entry=assets[0];box=entry['bbox']
    assert box[0]<=rect[0] and box[1]<=rect[1] and box[2]>=rect[2] and box[3]>=rect[3], 'every authored diagram corner survives'
    with zipfile.ZipFile(epub) as z:
        im=Image.open(io.BytesIO(z.read('OEBPS/'+src))).convert('RGB')
        red=sum(r>140 and g<100 and b<100 for r,g,b in im.getdata())
    if kind in ('diagram_number','nonnumeric_inset'):
        assert red>20, 'authored numeric or nonnumeric inset ink inside the figure seed survives'
    else:
        assert red==0, 'separately printed marginal ink cannot occur in the published illustration'
        if kind=='authored_heading':
            assert any('A Northern Journey' in ''.join(n.itertext()) for n in h.body_nodes(docs) if n.tag.endswith('}h2')), 'authored heading remains live'
    assert 'A clear ordinary paragraph remains after the complete illustration.' in ' '.join(h.body_texts(docs))

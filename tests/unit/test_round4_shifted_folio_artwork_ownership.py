"""A shifted marginal folio hint cannot borrow authored optical artwork ink."""
import importlib.util
from pathlib import Path
import io
import zipfile
from PIL import Image
import pytest
from cps.services.reflow import vision_pipeline
_helper=Path(__file__).parents[1]/'test_round4_defects.py'
if not _helper.exists():_helper=Path(vision_pipeline.__file__).parents[3]/'tests/unit/test_round4_defects.py'
_spec=importlib.util.spec_from_file_location('shifted_folio_helpers',_helper)
h=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(h)

@pytest.mark.parametrize('case',['far_x_inside_seed','close_x_inside_seed','far_x_outside_seed'])
def test_shifted_folio_hint_preserves_matching_numeric_artwork(tmp_path,monkeypatch,case):
    inside_raw_seed=case!='far_x_outside_seed'
    page=h.InkPage()
    rect=[35,20,260,120]
    page.page.draw_rect(rect,color=(0,0,0),width=2)
    page.page.draw_line((35,20),(260,120),color=(0,0,0),width=2)
    page.page.insert_text((65 if inside_raw_seed else 235,34),'314',fontsize=8,color=(1,0,0))
    # The false shifted hint lies outside the RAW artwork seed. Its matching
    # optical label belongs to the artwork: either within its RAW seed or in
    # its connected right-hand extent. Far-x matching cannot prove ownership.
    hint=[64,5,80,18] if case=='close_x_inside_seed' else [5,20,18,36]
    page.blocks.append(h.model_block('314','margin','folio',hint))
    seed=rect if inside_raw_seed else [35,20,180,120]
    page.blocks.append(h.model_block('A synthetic diagram','art','figure',seed))
    page.text('The complete chart has an authored numbered key.','cap','caption',y=145,size=7)
    page.text('An ordinary paragraph keeps its source wording after the illustration.','body',y=180)
    epub,result,docs=h.convert_ink(tmp_path,monkeypatch,[page])
    assets=[(src,r) for src,r in result['images'].items() if r['kind']=='figure']
    assert len(assets)==1
    src,entry=assets[0];box=entry['bbox']
    assert box[0]<=rect[0] and box[1]<=rect[1] and box[2]>=rect[2] and box[3]>=rect[3], 'all authored chart corners survive the shifted furniture hint'
    with zipfile.ZipFile(epub) as archive:
        image=Image.open(io.BytesIO(archive.read('OEBPS/'+src))).convert('RGB')
        red=sum(r>140 and g<100 and b<100 for r,g,b in image.getdata())
    assert red>20, 'a matching optical number inside RAW artwork cannot be borrowed as a folio'

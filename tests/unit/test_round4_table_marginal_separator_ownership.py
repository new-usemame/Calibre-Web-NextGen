"""A marginal source rule bounds a table only when detached from authored art."""
import importlib.util
from pathlib import Path
import io
import zipfile
import pytest
from PIL import Image
from cps.services.reflow import vision_pipeline
_helper=Path(__file__).parents[1]/'test_round4_defects.py'
if not _helper.exists():_helper=Path(vision_pipeline.__file__).parents[3]/'tests/unit/test_round4_defects.py'
_spec=importlib.util.spec_from_file_location('table_separator_helpers',_helper)
h=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(h)

@pytest.mark.parametrize('case',['detached_marginal_rule','connected_authored_frame','rule_inside_raw_seed'])
def test_source_marginal_separator_owns_only_detached_table_exterior(tmp_path,monkeypatch,case):
    page=h.InkPage()
    page.text('A MARGINAL TITLE','header','running_head',x=35,y=22,size=8)
    actual=[35,75,260,300]
    if case=='connected_authored_frame':actual=[35,38,260,300]
    if case=='rule_inside_raw_seed':actual=[20,38,280,300]
    page.page.draw_rect(actual,color=(0,0,0),width=1)
    page.page.draw_line((140,75),(140,300),color=(0,0,0),width=1)
    for y in [110,175,240]:page.page.draw_line((35,y),(260,y),color=(0,0,0),width=.7)
    if case=='detached_marginal_rule':
        page.page.draw_line((20,38),(280,38),color=(1,0,0),width=.7)
    else:
        # This horizontal ink belongs to the authored table. One guard gives
        # it a connected vertical branch; another includes it in the RAW seed.
        page.page.draw_line((actual[0],38),(actual[2],38),color=(1,0,0),width=.7)
    seed=[35,75,260,300] if case=='connected_authored_frame' else actual
    page.blocks.append(h.model_block('A large ruled source table','table','table',seed))
    page.text('A complete source table caption.','caption','caption',y=325,size=7)
    page.text('Ordinary source prose remains after the complete table.','body',y=355,size=9)
    epub,result,docs=h.convert_ink(tmp_path,monkeypatch,[page])
    tables=[(src,r) for src,r in result['images'].items() if r['kind']=='table'];assert len(tables)==1
    src,entry=tables[0];box=entry['bbox']
    assert box[0]<=actual[0] and box[1]<=actual[1] and box[2]>=actual[2] and box[3]>=actual[3], 'all authored frame and ruled rows survive'
    with zipfile.ZipFile(epub) as archive:
        image=Image.open(io.BytesIO(archive.read('OEBPS/'+src))).convert('RGB')
        red=sum(r>140 and g<100 and b<100 for r,g,b in image.getdata())
    if case=='detached_marginal_rule':
        assert box[1]>=actual[1]-5, 'a physically separate running-header rule cannot enlarge a large table crop'
        assert red==0,'detached marginal rule ink is outside the published table'
    else:
        assert red>20,'connected authored frame or RAW-seed-owned rule must never be erased as a separator'
    assert 'Ordinary source prose remains after the complete table.' in h.body_texts(docs)

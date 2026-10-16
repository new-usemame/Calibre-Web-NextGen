"""A source-proven page folio cannot enlarge an illustration or table."""
import importlib.util
from pathlib import Path
import pytest
_spec=importlib.util.spec_from_file_location('folio_helpers',Path(__file__).with_name('test_round4_defects.py'))
h=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(h)

@pytest.mark.parametrize('kind',['no_ocr_figure','mislocated_table','authored_diagram_number'])
def test_source_folio_barrier_preserves_numeric_diagram_ink(tmp_path,monkeypatch,kind):
    page=h.InkPage()
    rect=page.figure(caption=False)
    if kind=='authored_diagram_number':
        page.page.insert_text((180,155),'314',fontsize=8)
    else:
        folio=page.text('314','folio','folio',x=144,y=382,size=8)
        if kind=='mislocated_table':
            page.blocks[0]['role']='table'
            folio['bbox']=[140/300*1000,388/400*1000,160/300*1000,399/400*1000]
        else:
            original_finish=page.finish
            def empty_optical():
                image,_,reading=original_finish()
                return image,[],reading
            page.finish=empty_optical
    _,result,docs=h.convert_ink(tmp_path,monkeypatch,[page])
    assets=[v for v in result['images'].values() if v['kind'] in ('figure','table')]
    assert len(assets)==1
    box=assets[0]['bbox']
    assert box[0]<=rect[0] and box[1]<=rect[1] and box[2]>=rect[2] and box[3]>=rect[3], 'all authored diagram ink remains'
    if kind!='authored_diagram_number':
        assert box[3]<370, 'the printed page folio below the artwork stays outside the packaged image'

"""RAW source seeds own connected artwork and nearby labels, not exterior ink."""
import importlib.util
from pathlib import Path
import io
import zipfile
import pytest
from PIL import Image
from cps.services.reflow import vision_pipeline
_helper=Path(__file__).parents[1]/'test_round4_defects.py'
if not _helper.exists():_helper=Path(vision_pipeline.__file__).parents[3]/'tests/unit/test_round4_defects.py'
_spec=importlib.util.spec_from_file_location('anchored_components_helpers',_helper)
h=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(h)

@pytest.mark.parametrize('case',['exterior_rule','exterior_word','exterior_flecks','connected_extension','near_label'])
def test_source_anchored_figure_preserves_artwork_without_disconnected_exterior_ink(tmp_path,monkeypatch,case):
    page=h.InkPage()
    actual=[90,90,190,165]
    page.page.draw_rect(actual,color=(0,0,0),width=1)
    page.page.draw_line((90,90),(190,165),color=(0,0,0),width=1)
    for rect in [[90,90,94,94],[186,90,190,94],[90,161,94,165],[186,161,190,165]]:
        page.page.draw_rect(rect,color=(0,0,1),fill=(0,0,1),width=.5)
    seed=[110,110,150,140] if case=='connected_extension' else actual
    page.blocks.append(h.model_block('A source diagram','art','figure',seed))
    if case=='exterior_rule':
        # A long header separator is a separate component above the small icon,
        # even when its horizontal extent approaches the artwork's column.
        page.page.draw_line((20,40),(185,40),color=(1,0,0),width=.5)
    elif case=='exterior_word':page.page.insert_text((20,40),'EXTERIOR',fontsize=8,color=(1,0,0))
    elif case=='exterior_flecks':
        for rect in [[100,35,102,37],[150,330,152,332]]:
            page.page.draw_rect(rect,color=(1,0,0),fill=(1,0,0),width=.2)
    elif case=='near_label':
        # Authored lettering can be disconnected from the diagram's outlines
        # and extend beyond its RAW seed. Blank space alone does not erase it.
        page.page.insert_text((197,135),'NORTH',fontsize=8,color=(0,.6,0))
    if case!='exterior_flecks':page.text('A complete source caption.','cap','caption',y=190,size=7)
    page.text('The complete ordinary paragraph remains independently live.','body',y=365 if case=='exterior_flecks' else 240)
    epub,result,docs=h.convert_ink(tmp_path,monkeypatch,[page])
    assets=[(src,r) for src,r in result['images'].items() if r['kind']=='figure']
    assert len(assets)==1
    src,entry=assets[0];box=entry['bbox']
    assert box[0]<=actual[0] and box[1]<=actual[1] and box[2]>=actual[2] and box[3]>=actual[3],'all connected authored artwork survives an undersized seed'
    with zipfile.ZipFile(epub) as archive:
        image=Image.open(io.BytesIO(archive.read('OEBPS/'+src))).convert('RGB')
        colors=list(image.getdata())
    red=sum(r>140 and g<100 and b<100 for r,g,b in colors)
    blue=sum(b>140 and r<100 and g<100 for r,g,b in colors)
    assert blue>30,'every source corner retains original authored ink'
    if case.startswith('exterior_'):
        assert box[1]>=actual[1]-5 and box[3]<=actual[3]+5, 'disconnected exterior ink cannot enlarge the authored vertical extent'
        assert red==0,'disconnected exterior rule, header word or scan flecks cannot expand source artwork'
    if case=='near_label':
        green=sum(g>90 and r<90 and b<90 for r,g,b in colors)
        assert green>20,'genuine disconnected nearby authored labels survive beyond the RAW seed'
    assert 'The complete ordinary paragraph remains independently live.' in h.body_texts(docs)

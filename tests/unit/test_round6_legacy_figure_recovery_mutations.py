"""Helper failure is recovered; final-output image removal is still rejected."""
import importlib.util
from pathlib import Path
import re
import zipfile
from xml.etree import ElementTree as ET
import pytest
from cps.services.reflow import vision_pipeline, build_epub
pytestmark=pytest.mark.unit


@pytest.mark.parametrize('case',['art_below','art_other_column'])
def test_revised_legacy_contract_detects_missing_context_object(tmp_path,monkeypatch,case):
    root=Path(vision_pipeline.__file__).parents[3]/'tests/unit'
    spec=importlib.util.spec_from_file_location('test_round2_assembly',root/'test_round2_assembly.py')
    assembly=importlib.util.module_from_spec(spec)
    import sys
    sys.modules[spec.name]=assembly;spec.loader.exec_module(assembly)
    spec=importlib.util.spec_from_file_location('round6_legacy_contract_mutant',root/'test_round2_floating_sentence.py')
    legacy=importlib.util.module_from_spec(spec);spec.loader.exec_module(legacy)
    original_context=vision_pipeline._figure_source_context
    original_convert=vision_pipeline.convert

    class RecoveredPublication(Exception):
        pass

    def missing(*args,**kwargs):
        figure,context,src,target=original_context(*args,**kwargs)
        return figure,re.sub(r'<img\b[^>]*>','',context),src,target

    def recovered(*args,**kwargs):
        report=original_convert(*args,**kwargs)
        path=Path(args[1])
        assert build_epub.validate(str(path))==[]
        assert report['degraded_units'],'Unusable helper output must be disclosed in the report'
        with zipfile.ZipFile(path) as archive:
            docs=[ET.fromstring(archive.read(n)) for n in archive.namelist() if n.endswith('.xhtml')]
            targets={unit['source_reference'] for unit in report['degraded_units']}
            sources=[e for doc in docs for e in doc.iter() if e.get('id') in targets]
            assert sources and any(e.findall('.//x:img',assembly.NS) for e in sources),'Recovery must retain actual original SOURCE pixels'
            assert all(e.find('.//x:p',assembly.NS) is not None or e.find('.//x:figcaption',assembly.NS) is not None for e in sources),'Every recovered SOURCE unit needs visible disclosure'
            assert any(''.join(a.itertext())=='Return' for e in sources for a in e.findall('.//x:a',assembly.NS)),'Recovered SOURCE must retain its working control'
        raise RecoveredPublication

    with monkeypatch.context() as patch:
        patch.setattr(vision_pipeline,'_figure_source_context',missing)
        patch.setattr(vision_pipeline,'convert',recovered)
        with pytest.raises(RecoveredPublication):
            legacy.test_source_bound_ordinary_sentence_can_continue_around_separate_owned_artwork(tmp_path,patch,case)

    removed=[]
    def missing_after_publication(*args,**kwargs):
        report=original_convert(*args,**kwargs)
        path=Path(args[1])
        with zipfile.ZipFile(path) as archive:
            entries=[(info,archive.read(info.filename)) for info in archive.infolist()]
        with zipfile.ZipFile(path,'w') as archive:
            for info,data in entries:
                if info.filename.endswith('.xhtml'):
                    text=data.decode('utf-8')
                    def erase(match):
                        value,count=re.subn(r'<img\b[^>]*>','',match.group(0))
                        removed.extend([info.filename]*count)
                        return value
                    text=re.sub(r'<aside\b[^>]*\bclass="[^"]*vision-disclosure[^"]*"[^>]*>.*?</aside>',erase,text,flags=re.S)
                    data=text.encode('utf-8')
                archive.writestr(info,data)
        assert removed,'Final-output mutation must remove a real source-context image'
        return report
    monkeypatch.setattr(vision_pipeline,'convert',missing_after_publication)
    with pytest.raises(AssertionError,match='source context must actually display'):
        legacy.test_source_bound_ordinary_sentence_can_continue_around_separate_owned_artwork(tmp_path,monkeypatch,case)

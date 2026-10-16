"""Real optical aborts and exact-fragment packaging failures stay contained."""
import copy,hashlib,importlib.util,json,subprocess
from pathlib import Path
from types import SimpleNamespace
import pytest
from cps.services.reflow import vision_pixels,build_epub
_spec=importlib.util.spec_from_file_location('r7_optical_support',Path(__file__).with_name('round7_recovery_test_support.py'))
s=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(s)


@pytest.mark.parametrize('failure',['missing_engine','engine_failure','timeout','malformed_tsv','corrupt_cache'])
def test_original_optical_abort_is_source_backed_only_on_its_page(tmp_path,monkeypatch,failure):
    actual=vision_pixels.words;doc,reader,prepared=s.fixture(tmp_path,monkeypatch);reached=[]
    def optical(display,cache):
        if display.pno!=1:return copy.deepcopy(prepared[display.pno][1])
        if failure=='corrupt_cache':
            pix=display.pixmap(scale=3,max_pixels=8_000_000);identity=hashlib.sha256(pix.tobytes('png')).hexdigest()
            root=Path(cache);root.mkdir(parents=True,exist_ok=True)
            (root/('literal-tsv-v2-'+identity+'.json')).write_text('{invalid json')
        try:return actual(display,cache)
        except (RuntimeError,ValueError,subprocess.TimeoutExpired) as error:
            reached.append((failure,type(error).__name__,str(error)));raise
    monkeypatch.setattr(vision_pixels,'words',optical)
    monkeypatch.setattr(vision_pixels.shutil,'which',lambda engine:None if failure=='missing_engine' else 'synthetic-locked-engine')
    def engine(args,**kwargs):
        assert args[0]=='synthetic-locked-engine', 'no OCR process is dispatched'
        if failure=='timeout':raise subprocess.TimeoutExpired(args,120)
        text='level\ttext\tleft\ttop\twidth\theight\n5\tmark\tbad\t1\t3\t4\n' if failure=='malformed_tsv' else ''
        return SimpleNamespace(returncode=1 if failure=='engine_failure' else 0,stdout=text.encode(),stderr=b'')
    monkeypatch.setattr(vision_pixels.subprocess,'run',engine)
    _,report,_=s.publish(doc,reader,tmp_path)
    assert len(reached)==1 and report['degraded_units'], 'exact failing original optical helper must be reached'
    assert reader.calls==[0,2], 'failed optical evidence does not justify reading/re-reading that source'
    assert report['reread_pages']==[]


@pytest.mark.parametrize('failure',['xml_envelope','partial_archive'])
def test_package_semantic_failure_preserves_exact_live_fragments_and_assets(tmp_path,monkeypatch,failure):
    doc,reader,_=s.fixture(tmp_path,monkeypatch);reached=[]
    if failure=='xml_envelope':
        def broken(*args,**kwargs):
            reached.append('build_epub._document');raise ValueError('synthetic XML envelope failure')
        monkeypatch.setattr(build_epub,'_document',broken)
    else:
        def broken(package,parts):
            reached.append('build_epub._Package.finish')
            # Some ZIP entries exist before the failure; they cannot become a
            # duplicated member set or erase admitted reading documents.
            package._zf.writestr('OEBPS/partial-uncommitted.xhtml','<discarded/>')
            raise ValueError('synthetic semantic finalization failure')
        monkeypatch.setattr(build_epub._Package,'finish',broken)
    path,report,docs=s.publish(doc,reader,tmp_path)
    assert reached and report.get('degraded_units',[])==[], 'exact deterministic package repair cannot degrade healthy SOURCE pages'
    assert s.h.body_texts(docs).count(s.TEXTS[1])==1
    assert any(e['transition']=='DETERMINISTIC_SOURCE_REPAIR' for e in report['unit_recovery_events'])
    with __import__('zipfile').ZipFile(path) as archive:
        names=archive.namelist();assert len(names)==len(set(names))
        assert 'OEBPS/partial-uncommitted.xhtml' not in names
    assert reader.calls==[0,1,2]

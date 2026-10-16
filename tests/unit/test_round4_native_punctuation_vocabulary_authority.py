"""Physical compound evidence includes surrounding punctuation, never substrings."""
from pathlib import Path
import importlib.util
import pytest,pymupdf
from cps.services.reflow import assemble,vision_pipeline
HELPER=Path(__file__).parent/'test_round4_native_page_wrap_vocabulary.py'
if not HELPER.exists():HELPER=Path(__file__).parent/'measurer/test_round4_native_page_wrap_vocabulary.py'
s=importlib.util.spec_from_file_location('immutable_native_punctuation_fixture',HELPER)
h=importlib.util.module_from_spec(s);s.loader.exec_module(h)

@pytest.mark.parametrize('printed',['deep-seated,','(deep-seated)'])
def test_punctuation_delimited_physical_compound_overrides_source_closed_spelling(tmp_path,monkeypatch,printed):
    old_insert=pymupdf.Page.insert_text;old_book=assemble.Book
    def text(value):return value.replace('deep-seated root has',printed+' root has')
    def insert(page,point,value,*args,**kwargs):return old_insert(page,point,text(value),*args,**kwargs)
    def prepared(*args,**kwargs):
        # The prepared native text exactly matches the authored physical PDF.
        for rows in kwargs['pages'].values():
            for element in rows:
                for run in element.runs:
                    if run[0]=='t':run[1]=text(run[1])
        return old_book(*args,**kwargs)
    monkeypatch.setattr(pymupdf.Page,'insert_text',insert)
    monkeypatch.setattr(assemble,'Book',prepared)
    h.fixture(tmp_path,'compound_over_whole')


def test_native_physical_tokens_never_earn_partial_or_multiword_evidence():
    class Page:
        def get_text(self,kind):
            assert kind=='words'
            return [(0,0,1,1,v,0,0,n) for n,v in enumerate(['nodeep-seatedsuffix','deep-seated-root','deep seated','pine shine','traveller\u2019s'])]
    words,compounds=vision_pipeline._native_wrap_vocabulary([Page()],[0])
    assert 'deep-seated' not in compounds and 'deepseated' not in words
    assert 'sunshine' not in words and 'pine' not in words and 'shine' not in words
    assert 'nodeep-seatedsuffix' in compounds and 'traveller\u2019s' in words


def test_incomplete_physical_hyphen_tokens_cannot_earn_closed_or_compound_evidence():
    doc=pymupdf.open()
    try:
        page=doc.new_page(width=431.97,height=647.97)
        page.insert_text((54,105),'sunshine- sun-shine-',fontsize=11)
        assert vision_pipeline.clean_native(page)
        assert [w[4] for w in page.get_text('words')]==['sunshine-','sun-shine-']
        words,compounds=vision_pipeline._native_wrap_vocabulary(doc,[0])
        assert words==set() and compounds==set()
    finally:doc.close()

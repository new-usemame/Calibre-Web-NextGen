"""Cached vocabulary cannot grant or veto selected physical-native wrap proof."""
from pathlib import Path
import importlib.util
from cps.services.reflow import assemble
HELPER=Path(__file__).parent/'test_round4_native_page_wrap_vocabulary.py'
if not HELPER.exists():HELPER=Path(__file__).parent/'measurer/test_round4_native_page_wrap_vocabulary.py'
spec=importlib.util.spec_from_file_location('immutable_native_navigation_fixture',HELPER)
h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)

def test_unproven_cached_whole_word_cannot_delete_printed_native_hyphen(tmp_path,monkeypatch):
    original=assemble.Book
    def prepared_book(*args,**kwargs):
        kwargs['page_wrap_words']=('deepseated',)
        kwargs['page_wrap_compounds']=()
        return original(*args,**kwargs)
    monkeypatch.setattr(assemble,'Book',prepared_book)
    # Physical selected native source prints only deep-/seated across pages;
    # the supplied cached Book alone asserts an unseen whole-word spelling.
    h.fixture(tmp_path,'unknown')

def test_unproven_cached_compound_cannot_veto_source_native_whole_word(tmp_path,monkeypatch):
    original=assemble.Book
    def prepared_book(*args,**kwargs):
        kwargs['page_wrap_words']=()
        kwargs['page_wrap_compounds']=('sun-shine',)
        return original(*args,**kwargs)
    monkeypatch.setattr(assemble,'Book',prepared_book)
    # Physical selected source itself prints sunshine elsewhere and no same-line
    # sun-shine compound. A cache-only compound cannot override that evidence.
    h.fixture(tmp_path,'known_whole')

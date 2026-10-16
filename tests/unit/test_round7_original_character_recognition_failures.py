"""Locked character-engine rejections retain SOURCE-owned reference context."""
import copy,pytest
from types import SimpleNamespace
from cps.services.reflow import vision_pixels
from tests.unit.round7_recovery_test_support import h
from tests.unit.round7_public_note_contract_support import source_visible
BODY='The amber reference remains in its complete source.* Every violet word survives.'
NOTE='The finite footnote preserves its original printed attribution.'

@pytest.mark.parametrize('failure',['missing_engine','engine_failure'])
def test_original_character_recognition_abort_is_scoped_to_owned_body(tmp_path,monkeypatch,failure):
    page=h.InkPage();page.text(BODY,'body',y=65);page.text('* '+NOTE,'note','footnote',y=335,size=7,marker='*',model=NOTE)
    actual=vision_pixels.raised_markers;reached=[]
    def characters(*args,**kwargs):
        try:return actual(*args,**kwargs)
        except RuntimeError as error:reached.append(str(error));raise
    monkeypatch.setattr(vision_pixels,'raised_markers',characters)
    monkeypatch.setattr(vision_pixels.shutil,'which',lambda name:None if failure=='missing_engine' else 'synthetic-locked-engine')
    def engine(args,**kwargs):
        assert args[0]=='synthetic-locked-engine' and 'makebox' in args
        return SimpleNamespace(returncode=1,stdout=b'',stderr=b'')
    monkeypatch.setattr(vision_pixels.subprocess,'run',engine)
    path,report,docs=h.convert_ink(tmp_path,monkeypatch,[page])
    assert reached==[('Printed note references require local pixel recognition' if failure=='missing_engine' else 'Printed note character recognition failed')]
    assert {(u['page'],u.get('block')) for u in report['degraded_units']}=={(0,'body')}
    assert sum(NOTE in ''.join(note.itertext()) for _,note in h.refs_and_notes(docs)[1])==1
    assert h.refs_and_notes(docs)[0]==[]
    source_visible(path,report,{0})

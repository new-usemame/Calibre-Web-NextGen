"""SOURCE-free repeated words cannot make a broad BODY hint own a printed note.

The complete model paragraph continues beyond its stale local rectangle.
Identical words in the nearby note are separate physical source instances.
Tight coherent BODY evidence remains protected from a misplaced note hint.
"""
import importlib.util
from pathlib import Path
import pytest
from cps.services.reflow import vision_pipeline

_helper=Path(__file__).with_name('test_round4_defects.py')
if not _helper.exists():
    _helper=Path(vision_pipeline.__file__).parents[3]/'tests/unit/test_round4_defects.py'
_s=importlib.util.spec_from_file_location('round5_footer_core_ink',_helper)
h=importlib.util.module_from_spec(_s);_s.loader.exec_module(h)
pytestmark=pytest.mark.unit


def stream(docs):
    return ' '.join(h.plain(n) for root in docs.values() for b in root.findall('x:body',h.NS)
                    for n in b if n.tag.endswith(('}p','}div','}h2')) and n.get('class')!='caption')


@pytest.mark.parametrize('fused_label',[False,True])
def test_footer_note_core_matches_never_become_body_instances(tmp_path,monkeypatch,fused_label):
    p=h.InkPage()
    shared='the original marks of wear'
    foreign='Observers compare several independently measured values across distant stations.'
    p.text(foreign,'separate',y=45,size=5)
    upper='The careful workers study ordinary specimens beside calibrated laboratory instruments'
    lower=['while '+shared+' remain evident in these samples.',foreign]
    model=upper+' '+' '.join(lower)
    p.text(upper,'wrapped',y=270,size=4.5,model=model)
    p.blocks[-1]['bbox']=h.model_block('', 'unused','body',[20,260,282,325])['bbox']
    note='A separate source citation records '+shared+' for archival readers.'
    # Numeric opening belongs to this same printed note; optical fusion is
    # source recognition only, leaving the original source pixels unchanged.
    p.page.insert_text((22,314),'24 '+note,fontsize=4.5)
    p.blocks.append(h.model_block(note,'note','footnote',[20,304,282,319],marker='24'))
    for i,line in enumerate(lower):p.page.insert_text((22,352+12*i),line,fontsize=4.5)
    if fused_label:
        finish=p.finish
        def finish_with_fused_opening():
            image,words,answer=finish()
            first=next(w for w in words if w['text']=='24')
            following=next(w for w in words if w['text']=='A' and w['bbox'][1]>300)
            first['text']='24A';first['bbox'][2]=following['bbox'][2];words.remove(following)
            return image,words,answer
        p.finish=finish_with_fused_opening
    _,_,docs=h.convert_ink(tmp_path,monkeypatch,[p])
    body=stream(docs);_,notes=h.refs_and_notes(docs)
    texts=[' '.join(''.join(n.itertext()).split()) for _,n in notes]
    assert 'for archival readers.' not in body,'note-only ending must not enter BODY optical fallback'
    assert body.count(shared)==1,'the actual BODY occurrence stays once; separate footer words never enlarge its optical stream'
    assert '24A' not in body and '24 ' not in body,'a source note opening label never enters BODY through a rounding halo'
    assert any(note in t for t in texts),'complete printed note text remains in its own aside'
    assert sum(note in t for t in texts)==1
    assert foreign in body and upper in body and 'remain evident in these samples.' in body


def test_displaced_note_hint_cannot_steal_tight_coherent_body(tmp_path,monkeypatch):
    p=h.InkPage()
    body='Careful workers compare original records with independently measured coastal observations.'
    p.text(body,'body',y=280,size=5)
    note='Careful workers compare original records with independently measured coastal observations.'
    p.page.insert_text((22,325),'7 '+note,fontsize=5)
    p.blocks.append(h.model_block(note,'note','footnote',[20,269,282,285],marker='7'))
    _,_,docs=h.convert_ink(tmp_path,monkeypatch,[p])
    assert stream(docs).count(body)==1,'a bad note hint cannot override a tightly corroborated BODY paragraph'
    _,notes=h.refs_and_notes(docs)
    assert any(note in ''.join(n.itertext()) for _,n in notes)


def test_complete_two_column_note_instances_and_genuine_body_both_survive(tmp_path,monkeypatch):
    path=Path(__file__).with_name('test_round5_column_note_source_ownership_calibrated.py')
    if not path.exists():path=Path(vision_pipeline.__file__).parents[3]/'tests/unit/test_round5_column_note_source_ownership_calibrated.py'
    spec=importlib.util.spec_from_file_location('round5_existing_column_positive',path)
    notes_fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(notes_fixture)
    p,bodies,left,right=notes_fixture.fixture()
    _,_,docs=h.convert_ink(tmp_path,monkeypatch,[p])
    body=stream(docs)
    assert 'vol.' not in body and 'Its continuation preserves' not in body
    assert all(body.count(text.rstrip('*†'))==1 for text in bodies),'genuine BODY survives note-row ownership'
    refs,notes=h.assert_exact_returns(docs,2)
    texts=[' '.join(''.join(n.itertext()).split()) for _,n in notes]
    assert sum(left in t for t in texts)==1 and sum(right in t for t in texts)==1
    assert all(not(left in t and right in t) for t in texts),'source columns keep their distinct note owners'

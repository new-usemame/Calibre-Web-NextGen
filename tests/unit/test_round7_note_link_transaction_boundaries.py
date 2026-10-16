"""Public recovery replays after owned late note changes and global markup mutations."""
import pytest
from cps.services.reflow import vision_pipeline as vp,build_epub
from tests.unit.round7_recovery_test_support import h
FIRST='The amber notebook retains its exact first live paragraph.'
LAST='The violet notebook retains its exact later live paragraph.'
GOOD='A healthy footnote remains complete beside its separate sibling.'
BAD='The disputed footnote retains its actual silver original pixels.'

@pytest.mark.parametrize('stage',['orphan_continuation','late_backlinks'])
def test_owned_late_note_failure_keeps_other_note_and_same_page_body(tmp_path,monkeypatch,stage):
    page=h.InkPage();page.text(FIRST,'first',y=55);page.text(LAST,'last',y=160)
    page.text(GOOD,'good','footnote',y=280,size=7,marker='1')
    page.text(BAD,'bad','footnote',y=340,size=7,marker='2')
    reached=[]
    if stage=='orphan_continuation':
        original=vp._note_ownership
        def owners(scans):
            owned,catalogue,labels=original(scans)
            if any(b['id']=='bad' for b in scans[0]['answer']['blocks']):
                owned[(0,'bad')]=(99,'absent');reached.append('actual orphan continuation branch')
            return owned,catalogue,labels
        monkeypatch.setattr(vp,'_note_ownership',owners)
    else:
        class FailingAside(str):
            def replace(self,old,new,*args):
                if old=='</aside>':
                    reached.append('actual backlink replace after earlier note changed');raise ValueError('synthetic owned backlink assembly failure')
                return super().replace(old,new,*args)
        original=vp.compile_page
        def compiler(*args,**kwargs):
            page,report=original(*args,**kwargs)
            page['asides']=[FailingAside(x) if BAD in x else x for x in page['asides']]
            return page,report
        monkeypatch.setattr(vp,'compile_page',compiler)
    path,report,docs=h.convert_ink(tmp_path,monkeypatch,[page])
    assert reached
    assert h.body_texts(docs)==[FIRST,LAST]
    assert sum(GOOD in ''.join(n.itertext()) for _,n in h.refs_and_notes(docs)[1])==1
    assert {(u['page'],u.get('block')) for u in report['degraded_units']}=={(0,'bad')}
    from tests.unit.round7_public_note_contract_support import source_visible
    source_visible(path,report,{0})

@pytest.mark.parametrize('stage',['chapter_partial_mutation','link_partial_mutation'])
def test_global_markup_helper_failure_reconstructs_exact_admitted_fragments(tmp_path,monkeypatch,stage):
    page=h.InkPage();page.text(FIRST,'first',y=55);page.text(LAST,'last',y=160)
    page.text(GOOD,'good','footnote',y=280,size=7,marker='1')
    reached=[]
    if stage=='chapter_partial_mutation':
        original=build_epub._chapters
        def broken(pages):
            result=original(pages);result[0].blocks.pop();reached.append('chapter partial changed');raise ValueError('synthetic chapter grouping failure after removal')
        monkeypatch.setattr(build_epub,'_chapters',broken)
    else:
        original=build_epub._bind_links
        def broken(chapters):
            original(chapters);chapters[0].blocks[0]='<p>Uncommitted replacement.</p>';reached.append('actual binder partial changed');raise ValueError('synthetic link mutation failure')
        monkeypatch.setattr(build_epub,'_bind_links',broken)
    _,report,docs=h.convert_ink(tmp_path,monkeypatch,[page])
    assert reached and report['degraded_units']==[]
    assert h.body_texts(docs)==[FIRST,LAST]
    assert sum(GOOD in ''.join(n.itertext()) for _,n in h.refs_and_notes(docs)[1])==1
    assert all('Uncommitted replacement' not in ''.join(root.itertext()) for root in docs.values())

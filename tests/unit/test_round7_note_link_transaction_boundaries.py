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


def test_native_unit_source_replacement_keeps_its_proved_note_origin(tmp_path,monkeypatch):
    """Synthetic admitted reference survives when its paragraph becomes ink."""
    import hashlib
    from types import SimpleNamespace
    from cps.services.reflow import unit_source
    original=('<p>Independently written body text.'
              '<a class="noteref" epub:type="noteref" id="fnref_p0000_1" '
              'href="#fn_p0000_1"><sup>1</sup></a></p>')
    note=('<aside class="footnote" epub:type="footnote" id="fn_p0000_1"><p>'
          '<a class="note-return" href="#fnref_p0000_1" aria-label="Return to note reference">1</a>'
          ' The synthetic supporting note.</p></aside>')
    def retained(*args,**kwargs):
        return ('<div class="source-disclosure"><p>View original source page</p></div>',
                ['<aside id="source-pixels"><img src="images/source.jpg" alt="Original page"/></aside>'],
                dict(blocks=[dict(id='native-0',role='body')]))
    monkeypatch.setattr(unit_source,'source_unit',retained)
    digest=hashlib.sha256(original.encode()).hexdigest()
    page=dict(pno=0,body=[original],asides=[note])
    record=dict(id='native-0',role='body',native_source=True,fragment_sha256=digest)
    build_epub.degrade_native_units(page,dict(blocks=[record]),
        {'native-0':dict(fragment_sha256=digest,stage='native_pixel_adjudication',reason='Synthetic unresolved word')},
        SimpleNamespace(),SimpleNamespace(),{})
    from cps.services.reflow import unit_recovery
    chapter=build_epub.Chapter(index=1,title='Synthetic page',blocks=page['body']+page['asides'])
    unit_recovery.bind_links([chapter])
    markup=''.join(chapter.blocks)
    assert markup.count('id="fnref_p0000_1"')==1
    assert original[original.index('<a '):original.index('</a>')+4] in markup
    assert note in markup
    assert 'Independently written body text.' not in markup
    assert 'images/source.jpg' in markup


@pytest.mark.parametrize('direction',['origin','note'])
def test_unproved_note_link_discloses_locally_beside_an_existing_page_fallback(tmp_path,monkeypatch,direction):
    """Printed synthetic notes remain live; only an unproved action gets ink."""
    import re
    from cps.services.reflow import reading_flow,unit_recovery
    first=h.InkPage();first.reference(number=1)
    first.text('1 '+GOOD,'good','footnote',y=280,size=7,marker='1',model=GOOD)
    middle=h.InkPage();middle.text('The middle page has separately retained original pixels.','middle',y=70)
    last=h.InkPage();last.text(LAST,'last',y=70)
    original_compile=vp.compile_page
    def compile_owned(*args,**kwargs):
        if args[3]==1:
            raise unit_recovery.UnitFailure([dict(page=1,block=None)],'synthetic_page_failure',ValueError('Retain this synthetic page'))
        return original_compile(*args,**kwargs)
    monkeypatch.setattr(vp,'compile_page',compile_owned)
    original_finish=reading_flow.finish;reached=[]
    def unproved(chapters,book):
        result=original_finish(chapters,book)
        markup=''.join(b for c in chapters for b in c.blocks)
        origin=re.search(r'<a\b[^>]*class="noteref"[^>]*>',markup)
        if origin is None and reached:return result
        assert origin,'The printed synthetic marker must first be admitted'
        attribute='id' if direction=='origin' else 'href'
        target=re.search(attribute+r'="([^"]+)"',origin[0])[1].lstrip('#')
        for c in chapters:c.blocks=[b.replace('id="'+target+'"','') for b in c.blocks]
        reached.append(target)
        return result
    monkeypatch.setattr(reading_flow,'finish',unproved)
    path,report,docs=h.convert_ink(tmp_path,monkeypatch,[first,middle,last])
    assert reached
    assert LAST in h.body_texts(docs)
    assert sum(GOOD in ''.join(n.itertext()) for _,n in h.refs_and_notes(docs)[1])==1
    assert {n for n,r in report['pages'].items() if r.get('source')=='original_source'}=={1}
    assert any('The clear crystal' in text for text in h.body_texts(docs))
    cases=report['note_link_disclosures']
    assert len(cases)==1 and cases[0]['page']==0
    assert cases[0]['missing_target']==reached[0]
    assert cases[0]['source_image'] in report['images']
    assert all(u['page'] in (0,1) for u in report['degraded_units'])
    assert not any(e['stage']=='link_reference' for e in report['unit_recovery_events'])
    assert build_epub.validate(path)==[]
    links=[a for root in docs.values() for a in root.iter() if a.tag.endswith('}a')]
    disclosed=[a for a in links if a.get('aria-label')=='Note association unresolved; view original source page']
    assert len(disclosed)==1
    assert ''.join(disclosed[0].itertext()).strip()==('Return' if direction=='origin' else '1')


@pytest.mark.parametrize('page_first',[False,True])
def test_same_failure_cannot_reject_a_page_fallback_it_has_not_rendered(tmp_path,page_first):
    """Synthetic overlapping note ownership cannot fail its own new fallback."""
    from types import SimpleNamespace
    from cps.services.reflow import unit_recovery as ur
    c=ur.Controller(SimpleNamespace(key=None),[0,1],tmp_path/'staging')
    c.degraded[(0,'body')]=dict(page=0,block='body',reason='Earlier synthetic unit failure')
    units=[dict(page=0,block='body'),dict(page=0,block=None)]
    if page_first:units.reverse()
    failure=ur.UnitFailure(units,'link_reference',ValueError('Synthetic unproved note association'))
    c.recover(failure)
    assert set(c.degraded)=={(0,None)}
    assert c.events[-1]['transition']=='VISIBLE_SOURCE_DEGRADATION'
    assert c.source_pages()=={0}
    # A later attempt really using this page fallback must still be fatal if
    # its original source cannot be used. No original-source gate is weakened.
    with pytest.raises(ur.SourceIntegrityError,match='Original source fallback itself is unusable'):
        c.recover(failure)

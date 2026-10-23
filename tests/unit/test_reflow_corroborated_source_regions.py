"""Synthetic words and coordinates for source-ownership placement controls."""
from types import SimpleNamespace

import pymupdf
import pytest

from cps.services.reflow import vision_pipeline as vp

pytestmark = pytest.mark.unit


def word(text, x, y, width=28):
    return dict(text=text, bbox=[x, y, x+width, y+8])


def bind(text, role, hint, optical):
    block = dict(id='sample', text=text, role=role, bbox=hint,
                 marker='7' if role == 'footnote' else '', low_confidence=[],
                 continues_previous=False, continues_next=False)
    regions = {}
    display = SimpleNamespace(rect=pymupdf.Rect(0, 0, 500, 720), pno=0)
    answer, _ = vp.bind_geometry(dict(blocks=[block]), optical, display, regions, {})
    assert answer['blocks'][0]['text'] == text
    return regions.get('sample')


def test_displaced_note_hint_cannot_be_published_as_source_ownership():
    text = 'The narrow bridge crosses a quiet stream beside the mill.'
    optical = [word(t, 40+i*35, 580) for i, t in enumerate(text.split())]
    regions = bind(text, 'footnote', [70, 900, 900, 925], optical)
    assert regions is not None
    assert min(r[1] for r in regions) == 580
    assert max(r[3] for r in regions) == 588


def test_short_accent_variant_uses_printed_row_instead_of_following_row():
    optical = [word('Cafeteria.', 40, 205, 45), word('See', 90, 205, 18),
               word('Kitchen.', 113, 205, 40), word('A', 40, 218),
               word('different', 75, 218), word('entry.', 110, 218)]
    regions = bind('Caféteria. See Kitchen.', 'body', [75, 304, 310, 322], optical)
    assert regions is not None
    assert regions == [[40, 205, 153, 213]]


def test_distant_common_word_cannot_stretch_source_region_over_other_entries():
    optical = [word('on', 40, 172), word('other', 75, 172), word('details', 110, 172)]
    optical += [word(t, 40+i*36, 375) for i, t in enumerate(
        'on cafe equipment 12-14, 17, 19'.split())]
    regions = bind('on café equipment 12–14, 17, 19', 'body',
                   [70, 550, 650, 580], optical)
    assert regions == [[40, 375, 248, 383]]


def test_equal_repeated_readings_do_not_identify_one_source_occurrence():
    from cps.services.reflow.source_geometry import unique_anchors
    text = 'The clear narrow bridge crosses a quiet stream.'
    optical = [word(t, 40+i*35, y) for y in (100, 300)
               for i, t in enumerate(text.split())]
    assert unique_anchors(optical, range(len(optical)), text.split()) == []


def test_compact_unique_pixel_proof_overrides_more_broad_fuzzy_anchors(monkeypatch):
    text = 'The bright bridge remains open beside seven mills.'
    optical = [word('clear' if t=='bright' else t, 40+i*35, 200)
               for i, t in enumerate(text.split())]
    optical += [word('The', 40, 400)]
    # A permissive matcher can count more tokens by borrowing a remote copy.
    # A coherent exact occurrence must dominate that unrelated physical union.
    original = vp._geometry_anchors
    def broad(*args, **kwargs):
        if args[0] is not optical:return original(*args, **kwargs)
        return [(len(optical)-1, 0)]+[(i, i) for i in range(1, len(text.split()))]
    monkeypatch.setattr(vp, '_geometry_anchors', broad)
    regions = bind(text, 'body', [70, 260, 650, 570], optical)
    assert regions == [[40, 200, 313, 208]]


def test_native_sequence_can_locate_wrapped_numeric_ink_without_certifying_digits(monkeypatch):
    text='near mills 121 232 343 454 565–66 676–77 787–88'
    native=[word(t,40+i*42,200,38) for i,t in enumerate(
        'near mills 121 232 343 454 565-66 676-'.split())]
    native += [word('77',60,211,20),word('787-88',85,211,38)]
    optical=[dict(w) for w in native]
    for i,t in [(3,'$32'),(4,'$43'),(5,'$54'),(6,'65-66'),(7,'$76-')]:
        optical[i]=dict(optical[i],text=t)
    monkeypatch.setattr(vp,'layer_words',lambda *args:native)
    block=dict(id='wrapped',text=text,role='body',bbox=[70,300,650,330],marker='',
               low_confidence=[],continues_previous=False,continues_next=False)
    regions={};display=SimpleNamespace(rect=pymupdf.Rect(0,0,500,720),pno=0,doc=object())
    bound,_=vp.bind_geometry({'blocks':[block]},optical,display,regions,{})
    assert bound['blocks'][0]['text']==text
    assert min(r[1] for r in regions['wrapped'])==200
    assert max(r[3] for r in regions['wrapped'])==219
    assert all(regions['_instances'][vp._source_instance(w)]=='wrapped' for w in optical)


def test_joint_views_place_one_interior_variant_but_do_not_correct_it(monkeypatch):
    text='The bright renovation 12–13'
    native=[word(t,40+i*50,200,46) for i,t in enumerate('The bright renovations 12-13'.split())]
    optical=[dict(w) for w in native]
    monkeypatch.setattr(vp,'layer_words',lambda *args:native)
    block=dict(id='variant',text=text,role='body',bbox=[70,300,650,330],marker='',
               low_confidence=[],continues_previous=False,continues_next=False)
    regions={};display=SimpleNamespace(rect=pymupdf.Rect(0,0,500,720),pno=0,doc=object())
    bound,_=vp.bind_geometry({'blocks':[block]},optical,display,regions,{})
    assert bound['blocks'][0]['text']==text
    assert regions['variant']==[[40,200,236,208]]


def test_joint_placement_needs_visible_ink_and_unique_physical_occurrence():
    from cps.services.reflow.source_geometry import native_pixel_anchors
    native=[word(t,40+i*50,200,46) for i,t in enumerate('The bright renovations 12-13'.split())]
    candidate='The bright renovation 12–13'.split()
    assert native_pixel_anchors(native,[],[],candidate)==[]
    repeated=native+[dict(w,bbox=[w['bbox'][0],400,w['bbox'][2],408]) for w in native]
    assert native_pixel_anchors(repeated,repeated,range(len(repeated)),candidate)==[]


def test_compact_occurrence_wins_over_shared_anchors_with_extra_source_words():
    from cps.services.reflow.source_geometry import unique_anchors
    optical=[word(t,40+i*35,200)for i,t in enumerate('near other mills 121 232 343'.split())]
    optical += [word(t,40+i*35,212)for i,t in enumerate('near Brindle 454 565 676 787 898'.split())]
    candidate='near Brindlc 454 565 676 787 898'.split()
    proof=unique_anchors(optical,range(len(optical)),candidate)
    assert proof
    assert all(optical[i]['bbox'][1]==212 for i,j in proof)


def test_native_wrapped_placement_ignores_an_interleaved_other_column():
    from cps.services.reflow.source_geometry import native_pixel_anchors
    native=[word(t,100+i*35,200,30)for i,t in enumerate('near mills 121 232 343 454 565-66 676-'.split())]
    native += [word('77',120,211,20),word('787-88',145,211,30),word('Elsewhere',0,207,28)]
    optical=[dict(w)for w in native]
    for i,t in [(3,'$32'),(4,'$43'),(5,'$54'),(6,'65-66'),(7,'$76-')]:optical[i]=dict(optical[i],text=t)
    proof=native_pixel_anchors(native,optical,range(len(optical)),
        'near mills 121 232 343 454 565–66 676–77 787–88'.split())
    assert {j for i,j in proof}==set(range(9))
    assert all(i!=10 for i,j in proof)


def test_running_head_cannot_relocate_to_matching_body_words():
    text='The clear sample remains beside Brindle.'
    optical=[word('D4MAGED',40,12),word('HEADER',90,12)]
    optical.extend(word(t,40+i*55,200)for i,t in enumerate(text.split()))
    header=dict(id='head',text=text,role='running_head',bbox=[0,0,1000,70],marker='',
                low_confidence=[],continues_previous=False,continues_next=False)
    regions={};display=SimpleNamespace(rect=pymupdf.Rect(0,0,500,720),pno=0)
    answer,_=vp.bind_geometry({'blocks':[header]},optical,display,regions,{})
    assert answer['blocks'][0]['bbox']==header['bbox']
    assert not any(vp.owned_words([w],r)for w in optical if w['bbox'][1]==200
                   for r in regions.get('head',[]))


@pytest.mark.parametrize('side',['first','last'])
def test_compact_proof_keeps_a_locally_proved_divided_word_atom(side):
    source=('dow beside the quiet window after a careful journey today.' if side=='first'
            else 'Careful travelers pause near the quiet window before the mea-')
    model=source.replace('dow','meadow',1) if side=='first' else source[:-4]+'meadow'
    optical=[word(t,30+i*35,200)for i,t in enumerate(source.split())]
    regions=bind(model,'body',[40,260,950,330],optical)
    assert regions==[[30,200,373,208]],'compact exact matches retain their independently proved partial edge atom'


def test_complete_caption_seed_proof_survives_a_partial_local_hint(monkeypatch):
    monkeypatch.setattr(vp,'_reserve_object_instances',lambda *args:None)
    text='The clear sample retains eight ordinary printed words.'
    optical=[word(t,40+i*35,200)for i,t in enumerate(text.split())]
    art=dict(id='art',text='A synthetic illustration',role='figure',bbox=[60,250,800,330],marker='',
        low_confidence=[],continues_previous=False,continues_next=False)
    cap=dict(art,id='cap',text=text,role='caption',bbox=[60,270,420,300])
    regions={};display=SimpleNamespace(rect=pymupdf.Rect(0,0,500,720),pno=0)
    vp.bind_geometry({'blocks':[art,cap]},optical,display,regions,{})
    assert regions['cap']==[[40,200,313,208]],'partial hint proof cannot replace the complete adjacent-object source instance'


def test_partial_row_rounding_does_not_steal_the_following_owned_row():
    optical=[dict(text='small',bbox=[385,94,428,108]),dict(text='tools',bbox=[430,94,473,108])]
    optical += [dict(text=t,bbox=[20+i*45,103,63+i*45,115])for i,t in enumerate('before each careful journey through distant villages and familiar gardens beside'.split())]
    block=dict(id='row',text='small tools before each careful journey through distant villages and familiar gardens beside',role='body',
        bbox=[0,0,1000,1000],marker='',low_confidence=[],continues_previous=False,continues_next=False)
    regions={'row':[[385,94,473,108],[20,103,513,115]]}
    actual=vp._owned_block_words(block,{'blocks':[block]},optical,SimpleNamespace(rect=pymupdf.Rect(0,0,500,720)),regions,ordered=True)
    assert [w['text']for w in actual]==block['text'].split()


def test_complete_nearby_caption_rows_outrank_a_partial_local_hint(monkeypatch):
    monkeypatch.setattr(vp,'_reserve_object_instances',lambda *args:None)
    rows=['The clear sample stays beside the quiet window while careful workers record measurements.',
          'Another careful worker records eight ordinary printed details.']
    optical=[word(t,40+i*35,200+row*14)for row,text in enumerate(rows)
             for i,t in enumerate(text.split())]
    cap=dict(id='cap',text=' '.join(rows),role='caption',bbox=[60,270,950,285],marker='',
             low_confidence=[],continues_previous=False,continues_next=False)
    regions={};display=SimpleNamespace(rect=pymupdf.Rect(0,0,500,720),pno=0)
    vp.bind_geometry({'blocks':[cap]},optical,display,regions,{})
    assert all(any(vp.owned_words([w],r)for r in regions.get('cap',[]))for w in optical)


def test_exact_hint_prefix_survives_a_proved_divided_word_across_art(monkeypatch):
    monkeypatch.setattr(vp,'_reserve_object_instances',lambda *args:None)
    upper='The careful observers recorded a well-'
    lower=['preserved sample remains beside the quiet window while workers',
           'record several independent measurements before the evening rain.']
    optical=[word(t,40+35*i,70)for i,t in enumerate(upper.split())]
    for row,text in enumerate(lower):
        optical += [word(t,40+35*i,250+12*row)for i,t in enumerate(text.split())]
    block=dict(id='wrapped',role='body',text=upper+' '.join(lower),bbox=[75,90,600,115],
        marker='',low_confidence=[],continues_previous=False,continues_next=False)
    art=dict(id='art',role='figure',text='',bbox=[75,160,850,320],marker='',
        low_confidence=[],continues_previous=False,continues_next=False)
    display=SimpleNamespace(rect=pymupdf.Rect(0,0,500,720),pno=0)
    regions={}
    vp.bind_geometry({'blocks':[block,art]},optical,display,regions,{})
    owned=regions.get('_instances',{})
    assert all(owned.get(vp._source_instance(w))=='wrapped'for w in optical)
    assert min(r[1]for r in regions['wrapped'])==70
    assert len(regions['wrapped'])>=2,'an illustration gap must remain separate source territories'


def test_complete_wide_hint_cannot_borrow_a_common_tail_from_another_column():
    lines = ['Careful workers keep every sample beside the window',
             'before recording the final result in the ledger.']
    optical = [word(t, 30+i*23, 350+12*row, 20)
               for row, line in enumerate(lines) for i, t in enumerate(line.split())]
    # These ordinary words are real ink, but belong to another paragraph.
    optical += [word(t, 300+i*23, 350+12*row, 20)
                for row, line in enumerate(['the other passage', 'it continues here', 'is clearly separate'])
                for i, t in enumerate(line.split())]
    text = ' '.join(lines)+' the it is'
    regions = bind(text, 'body', [40, 470, 940, 540], optical)
    assert regions == [[30, 350, 211, 370]]
    assert not any(vp.owned_words([w], r) for w in optical if w['bbox'][0]>=300
                   for r in regions), 'equal common words do not prove paragraph continuity'


def test_sparse_remote_tail_cannot_complete_an_unplaced_long_continuation(monkeypatch):
    from cps.services.reflow import source_geometry
    # Exercise the fallback when curvature/recognition prevents a unique
    # compact-row proof. A few real common words remain in the other column.
    monkeypatch.setattr(source_geometry, 'unique_anchors', lambda *args, **kwargs: [])
    lines = ['Careful workers record each sample',
             'beside the open window while',
             'others prepare the quiet room',
             'for another measured observation before',
             'closing this detailed daily ledger.']
    optical = [word(t, 30+i*23, 350+12*row, 20)
               for row, line in enumerate(lines) for i, t in enumerate(line.split())]
    optical += [word(t, 300+i*23, 350+12*row, 20)
                for row, line in enumerate(['the other passage', 'it continues here', 'is clearly separate'])
                for i, t in enumerate(line.split())]
    text = ' '.join(lines)+' the account continues beyond this record and it is separate'
    regions = bind(text, 'body', [40, 470, 940, 600], optical)
    assert regions == [[30, 350, 142, 406]]
    assert not any(vp.owned_words([w], r) for w in optical if w['bbox'][0]>=300
                   for r in regions)


def test_complete_wrapped_rows_keep_their_broad_row_across_source_art(monkeypatch):
    from cps.services.reflow import source_geometry
    monkeypatch.setattr(vp,'_reserve_object_instances',lambda *args:None)
    lines = ['Careful workers cross the broad room before recording observations.',
             'Narrow rows remain beside the illustration.',
             'Later words complete the same paragraph.']
    optical = [word(t, x+27*i, y, 24) for x,y,line in
               [(25,70,lines[0]),(150,250,lines[1]),(150,265,lines[2])]
               for i,t in enumerate(line.split())]
    all_pairs=list(enumerate(range(len(optical))))
    narrow=all_pairs[len(lines[0].split()):]
    monkeypatch.setattr(source_geometry,'unique_anchors',lambda *args,**kwargs:narrow)
    monkeypatch.setattr(vp,'_geometry_anchors',lambda *args,**kwargs:all_pairs)
    block=dict(id='wrapped',role='body',text=' '.join(lines),bbox=[20,80,950,400],
               marker='',low_confidence=[],continues_previous=False,continues_next=False)
    art=dict(block,id='art',role='figure',text='',bbox=[50,160,280,300])
    regions={};display=SimpleNamespace(rect=pymupdf.Rect(0,0,500,720),pno=0)
    vp.bind_geometry({'blocks':[block,art]},optical,display,regions,{})
    assert all(regions['_instances'].get(vp._source_instance(w))=='wrapped' for w in optical)
    assert len(regions['wrapped'])>=2,'original artwork gap keeps distinct territories'


def test_raised_terminal_symbol_stays_after_its_own_baseline_words():
    optical=[dict(text='*',bbox=[110.7,40.2,111.9,43.1])]
    optical += [dict(text=t,bbox=b)for t,b in
                [('The',[22,41.7,35,45.9]),('quiet',[37,41.7,57,45.9]),
                 ('bridge.',[59,41.7,109.1,45.9])]]
    block=dict(id='body',role='body',text='The quiet bridge. *',marker='',
               bbox=[0,0,1000,1000],low_confidence=[],continues_previous=False,
               continues_next=False)
    regions={'body':[[110.7,40.2,111.9,43.1],[22,41.7,109.1,45.9]]}
    display=SimpleNamespace(rect=pymupdf.Rect(0,0,300,400))
    actual=vp._owned_block_words(block,{'blocks':[block]},optical,display,regions,ordered=True)
    assert [w['text']for w in actual]==['The','quiet','bridge.','*']

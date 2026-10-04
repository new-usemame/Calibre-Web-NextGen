"""Assembly preserves occurrence ownership before canonical projection exists."""
import copy
from dataclasses import asdict

import pytest
from cps.services.reflow import assemble, extract, skeleton, native_codec

pytestmark = pytest.mark.unit


def line(text, y, **flags):
    return extract.Line([extract.Span(text, 12, 'serif', 0, (10,y,160,y+12),
        char_boxes=((0,1,10.123456,y,14,y+12),), **flags)], (10,y,160,y+12))


def book_for(lines, regions):
    raw = extract.RawPage(0, 300, 400, [extract.Block(7,(0,0,300,400),lines)])
    skel = skeleton.PageSkeleton(0,300,400,regions)
    book = assemble.assemble([skel],skeleton.BookStyle(12),[raw])
    return book, raw


def test_equal_words_keep_distinct_furniture_occurrence_without_changing_output():
    head, body = line('The beginning',10), line('The beginning',90)
    book, raw = book_for([head,body],[skeleton.Region('furniture',[head],bbox=head.bbox),
                                    skeleton.Region('body',[body],bbox=body.bbox)])
    inventory = getattr(book,'source_inventory',{}).get(0)
    assert inventory is not None, 'assembly discarded page-bound furniture ownership'
    assert [r['id'] for r in inventory['lines']] == ['l0','l1']
    assert [r['line_ids'] for r in inventory['regions']] == [['l0'],['l1']]
    assert [r['representation'] for r in inventory['ownership']] == ['text','text']
    assert book.furniture == ['The beginning']
    assert [e.text for e in book.pages[0]] == ['The beginning']
    from cps.services.reflow import source_inventory as si
    si.validate(inventory,raw)
    broken=copy.deepcopy(inventory)
    broken['regions'][0]['line_ids']=['l1']
    with pytest.raises(ValueError): si.validate(broken,raw)


def test_image_owned_transcript_caption_and_empty_asset_are_accounted_separately():
    hidden, caption = line('Plausible but unverified transcript',20), line('Figure 1',220)
    box=(0,0,250,200)
    book, raw=book_for([hidden,caption],[
        skeleton.Region('artwork',[hidden],bbox=box,reason='unverified_scan_layout'),
        skeleton.Region('figure',bbox=box,reason='unverified_scan_layout'),
        skeleton.Region('caption',[caption],bbox=caption.bbox),
        skeleton.Region('figure',bbox=(260,250,290,280))])
    inventory=getattr(book,'source_inventory',{}).get(0)
    assert inventory is not None, 'protected source ownership was not captured'
    assert [r['representation'] for r in inventory['ownership']] == ['protected_image','text']
    assert inventory['assets'][0]['covered_line_ids']==['l0']
    assert inventory['assets'][1]['covered_line_ids']==[]
    assert len(inventory['assets'])==2
    from cps.services.reflow import source_inventory as si
    si.validate(inventory,raw)
    # Competing identical crops must never release their transcript as prose.
    skel=skeleton.PageSkeleton(0,300,400,[skeleton.Region('artwork',[hidden],bbox=box),
        skeleton.Region('figure',bbox=box),skeleton.Region('figure',bbox=box)])
    uncertain=si.capture(si.catalog(raw),skel)
    assert uncertain['ownership'][0]['representation']=='unresolved'
    assert uncertain['unresolved']


def test_codec_preserves_exact_spans_and_current_raw_rejects_resealed_mutation():
    original=line('Repeat repeat',20,uncertain=True,encoding_unresolved=True,
                  transcription_uncertain=True)
    book,raw=book_for([original],[skeleton.Region('furniture',[original],bbox=original.bbox)])
    assert getattr(book,'source_inventory',{}), 'source evidence was not retained for native transport'
    from cps.services.reflow import source_inventory as si
    replay=native_codec.loads(native_codec.dumps(book)).source_inventory[0]
    assert asdict(replay['lines'][0]['source'])==asdict(original)
    si.validate(replay,raw)
    replay['lines'][0]['source'].spans[0].uncertain=False
    replay['lines'][0]['sha256']=si.digest(replay['lines'][0]['source'])
    replay['sha256']=si.digest({k:v for k,v in replay.items() if k!='sha256'})
    with pytest.raises(ValueError): si.validate(replay,raw)


@pytest.mark.parametrize('kind', ['note', 'figure'])
def test_inverted_derived_region_is_retained_unresolved_without_aborting_assembly(kind):
    """Book566 p31 has a finite note bbox whose bottom precedes its top."""
    source=line('Retained original words',540)
    box=(108.47999999999999,535.68,683.28,465.59999999999997)
    region=skeleton.Region(kind,[source],bbox=box,number=1 if kind=='note' else None)
    book,raw=book_for([source],[region])
    from cps.services.reflow import source_inventory as si
    inventory=book.source_inventory[0]
    assert inventory['regions'][0]['bbox']==box
    assert inventory['regions'][0]['geometry_status']=='invalid_extent'
    assert inventory['ownership'][0]['representation']=='unresolved'
    assert {'region_id':'r0','reason':'invalid_region_geometry'} in inventory['unresolved']
    assert inventory['lines'][0]['source'].text==source.text
    if kind=='note':assert book.notes[0].text==source.text
    else:assert inventory['assets'][0]['bbox']==box
    si.validate(inventory,raw)
    # Raw geometry remains strict even if all public integrity hashes are changed.
    corrupt=copy.deepcopy(inventory)
    corrupt['lines'][0]['source'].bbox=box
    corrupt['lines'][0]['sha256']=si.digest(corrupt['lines'][0]['source'])
    corrupt['sha256']=si.digest({k:v for k,v in corrupt.items() if k!='sha256'})
    with pytest.raises(ValueError):si.validate(corrupt)

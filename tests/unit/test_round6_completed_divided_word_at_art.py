"""SOURCE-free model-completed divided words cannot duplicate at floating art."""
import importlib.util
from pathlib import Path
import pytest
from cps.services.reflow import vision_pipeline

_helper=Path(__file__).with_name('test_round4_defects.py')
if not _helper.exists():_helper=Path(vision_pipeline.__file__).parents[3]/'tests/unit/test_round4_defects.py'
_spec=importlib.util.spec_from_file_location('round6_completed_division_ink',_helper)
h=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(h)
pytestmark=pytest.mark.unit


def fixture(which='upper',cross=False,barrier='none',context=False):
    p=h.InkPage();q=h.InkPage() if cross else p
    prefix='Several careful observers recorded a clear view across the '
    ending=' while comparing the ordinary measurements.'
    upper=prefix+'mea-';lower='dow'+ending
    um=prefix+('meadow' if which in ('upper','both') else 'mea-')
    lm=('meadow' if which in ('lower','both') else 'dow')+ending
    if barrier=='wrong_source':upper=prefix+'mar-'
    if barrier=='wrong_suffix':lower='don'+ending
    if barrier=='two_whole':upper=um=prefix+'meadow';lower=lm='meadow'+ending
    if barrier=='compound':
        upper=prefix+'bridge-';um=prefix+'bridge-side';lower=lm='side'+ending
    p.text(upper,'upper',x=22,y=55,size=6,model=um)
    # Independent uncertainty disclosure away from the divided-word boundary.
    p.blocks[-1]['low_confidence']=[1]
    p.figure()
    if context:p.page.draw_line((120,100),(120,45),width=2,color=(0,0,0))
    role='heading' if barrier=='heading' else 'footnote' if barrier=='note' else 'body'
    x=37 if barrier=='indent' else 22;y=55 if cross else 230
    if barrier=='indent':
        q.page.insert_text((x,y),lower,fontsize=6)
        second='A following line establishes the ordinary original column margin.'
        q.page.insert_text((22,y+11),second,fontsize=6)
        lm+=' '+second
        q.blocks.append(h.model_block(lm,'lower',role,[21,y-9,282,y+15]))
    else:q.text(lower,'lower',role,x=x,y=y,size=6,model=lm,marker='7' if barrier=='note' else '')
    later='Another meadow appears in a separate finished observation.'
    q.text(later,'later',x=22,y=125 if cross else 300,size=6)
    return ([p,q] if cross else [p]),prefix,ending,um,lm,later


@pytest.mark.parametrize('which',['upper','lower','both'])
@pytest.mark.parametrize('cross',[False,True])
@pytest.mark.parametrize('context',[False,True])
def test_model_completed_division_publishes_one_word_before_floating_art(tmp_path,monkeypatch,which,cross,context):
    pages,prefix,ending,_,_,later=fixture(which,cross=cross,context=context)
    path,report,docs=h.convert_ink(tmp_path,monkeypatch,pages)
    expected=prefix+'meadow'+ending
    assert h.body_texts(docs)==[expected,later], 'exact original prefix/suffix must reconcile completed model words, not repeat the suffix or full word'
    nodes=h.body_nodes(docs)
    assert next(i for i,n in enumerate(nodes) if h.plain(n)==expected)<next(i for i,n in enumerate(nodes) if n.tag.endswith('}figure'))
    assert [h.plain(n) for n in nodes if n.get('class')=='caption']==['A connected synthetic diagram.']

    assert_generated_disclosures_keep_exact_returns_and_original_images(path,report,docs)
    spans=[n for root in docs.values() for n in root.findall('.//x:span',h.NS) if n.get('data-source-block') in ('upper','lower')]
    assert {int(n.get('data-source-page')) for n in spans}==({0,1} if cross else {0}), 'both original source owners survive the reconciled publication'
    if context:
        assert report['degraded_figure_count']==1
        figure=next(n for n in nodes if n.tag.endswith('}figure'))
        assert figure.find('.//x:a',h.NS) is not None


def assert_generated_disclosures_keep_exact_returns_and_original_images(path,report,docs):
    import zipfile
    refs=[(name,a) for name,root in docs.items() for a in root.findall('.//x:a',h.NS) if a.get('class')=='source-disclosure-ref']
    assert refs, 'the independent low-confidence reading retains original evidence rather than silently discarding disclosure'
    with zipfile.ZipFile(path) as archive:
        for name,ref in refs:
            target_name,ident=h.destination(name,ref.get('href'))
            target=next((n for n in docs[target_name].iter() if n.get('id')==ident),None)
            assert target is not None and 'vision-disclosure' in target.get('class','')
            images=target.findall('.//x:img',h.NS)
            assert images
            for image in images:
                src=image.get('src');assert src in report['images']
                assert any(member.endswith(src) for member in archive.namelist())
            assert any(a.text=='Return' and h.destination(target_name,a.get('href'))==(name,ref.get('id')) for a in target.findall('.//x:a',h.NS)), 'Return targets the same retained source evidence origin'


@pytest.mark.parametrize('barrier',['wrong_source','wrong_suffix','two_whole','indent','heading','note'])
def test_completed_model_word_does_not_erase_distinct_or_unproved_source_atom(tmp_path,monkeypatch,barrier):
    pages,prefix,ending,um,lm,later=fixture('upper',barrier=barrier)
    _,_,docs=h.convert_ink(tmp_path,monkeypatch,pages)
    texts=h.body_texts(docs);joined=prefix+'meadow'+ending
    assert later in texts
    if barrier=='two_whole':
        assert sum(t.split().count('meadow') for t in texts)==3, 'two actual full SOURCE occurrences plus separate vocabulary occurrence remain three, never deduplicated by word equality'
        assert any(prefix+'meadow meadow'+ending==t for t in texts)
    elif barrier=='note':
        _,notes=h.refs_and_notes(docs)
        assert any(lm in ' '.join(''.join(n.itertext()).split()) for _,n in notes)
        assert all(lm not in t for t in texts)
        assert um in texts
    elif barrier=='heading':
        assert any(h.plain(n)==lm and n.tag.endswith('}h2') for n in h.body_nodes(docs))
        assert um in texts
    else:
        assert joined not in texts, 'source mismatch and real indent cannot authorize completed-word deduplication'
        assert any('meadow' in t for t in texts)
        assert any(lm in t for t in texts), 'the separate lower authored atom is not lost'


def test_source_compound_reconciliation_keeps_printed_hyphen(tmp_path,monkeypatch):
    pages,prefix,ending,_,_,later=fixture('upper',barrier='compound')
    _,_,docs=h.convert_ink(tmp_path,monkeypatch,pages)
    assert h.body_texts(docs)==[prefix+'bridge-side'+ending,later]
    assert sum(t.count('bridge-side') for t in h.body_texts(docs))==1

"""SOURCE-free actual EPUB controls for one glyph represented twice in a reading.

Locked original ink and optical instances distinguish a redundant adjacent
placeholder from two genuinely printed reference symbols. Publication, owner
identity and every backlink are checked; no reading or production is altered.
"""
import copy
import importlib.util
from pathlib import Path

import pymupdf
import pytest
from cps.services.reflow import vision_pipeline, vision_pixels

_helper=Path(__file__).with_name('test_round4_defects.py')
if not _helper.exists():
    _helper=Path(vision_pipeline.__file__).parents[3]/'tests/unit/test_round4_defects.py'
_spec=importlib.util.spec_from_file_location('round5_reference_identity_ink',_helper)
h=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(h)
pytestmark=pytest.mark.unit


def fixture(mark='*', second=None, proven=True, numeric=False):
    page=h.InkPage();font=pymupdf.Font('cjk')
    page.page.insert_font(fontname='unicode',fontbuffer=font.buffer)
    prefix='Several independent observers describe clear specimens'
    page.page.insert_text((22,55),prefix,fontsize=6)
    x=22+pymupdf.get_text_length(prefix,fontsize=6)
    if numeric:
        mark='7'
    page.page.insert_text((x,55),mark,fontsize=6,fontname='unicode')
    end=x+font.text_length(mark,fontsize=6)
    suffix='' if second is None else second
    if suffix:
        page.page.insert_text((end+2,55),suffix,fontsize=6,fontname='unicode')
    text=prefix+mark+' <n:second>' if second and second!=mark else prefix+mark+' <n:note>'
    page.blocks.append(h.model_block(text,'body','body',[20,43,282,60]))
    note='The independent citation explains the ordinary observation precisely.'
    page.page.insert_text((22,320),mark+' '+note,fontsize=6,fontname='unicode')
    page.blocks.append(h.model_block(note,'note','footnote',[20,309,282,326],marker=mark))
    other='The separate source note records a distinct archival comparison.'
    if second and second!=mark:
        page.page.insert_text((22,350),second+' '+other,fontsize=6,fontname='unicode')
        page.blocks.append(h.model_block(other,'second','footnote',[20,339,282,356],marker=second))
    # Locked character recognition is deterministic and contains no inference
    # from the placeholder. Only the literal's actual original symbol is seen.
    candidates=[] if not proven or numeric else [dict(label=mark,kind='bare',bbox=[x,47,end,56])]
    return page,prefix,mark,note,other,candidates


def convert(tmp_path,monkeypatch,prepared):
    page,prefix,mark,note,other,candidates=prepared
    monkeypatch.setattr(vision_pixels,'raised_markers',lambda display,box,cache:copy.deepcopy(candidates))
    # Unproven literal: original authored mark is deliberately absent from the
    # optical reading. It must remain literal, never be silently deduplicated.
    if not candidates:
        original_finish=page.finish
        def finish():
            image,words,answer=original_finish()
            for word in words:
                if word['bbox'][1]<70 and word['text'].endswith(mark):
                    word['text']=word['text'][:-len(mark)]
            return image,words,answer
        page.finish=finish
    return h.convert_ink(tmp_path,monkeypatch,[page])


@pytest.mark.parametrize('mark',['*','†'])
def test_one_source_glyph_literal_and_adjacent_placeholder_publish_once(tmp_path,monkeypatch,mark):
    prepared=fixture(mark)
    _,result,docs=convert(tmp_path,monkeypatch,prepared)
    refs,notes=h.assert_exact_returns(docs,1)
    assert ''.join(refs[0][1].itertext())==mark
    assert h.body_texts(docs)==[prepared[1]]
    assert sum(prepared[3] in ''.join(n.itertext()) for _,n in notes)==1
    block=next(b for b in result['pages'][0]['blocks'] if b['id']=='body')
    assert block['published_tokens'][-2:]==['specimens'+mark,'<n:note>'], 'reconciliation must not rewrite the model transcription'


def test_two_distinct_adjacent_source_glyphs_same_owner_both_survive(tmp_path,monkeypatch):
    prepared=fixture('*',second='*')
    _,_,docs=convert(tmp_path,monkeypatch,prepared)
    refs,_=h.assert_exact_returns(docs,2)
    assert [''.join(ref.itertext()) for _,ref in refs]==['*','*']
    assert len({h.destination(name,ref.get('href')) for name,ref in refs})==1
    assert h.body_texts(docs)==[prepared[1]]


def test_adjacent_symbols_with_different_note_owners_both_survive(tmp_path,monkeypatch):
    prepared=fixture('*',second='†')
    _,_,docs=convert(tmp_path,monkeypatch,prepared)
    refs,_=h.assert_exact_returns(docs,2)
    assert [''.join(ref.itertext()) for _,ref in refs]==['*','†']
    assert len({h.destination(name,ref.get('href')) for name,ref in refs})==2
    assert h.body_texts(docs)==[prepared[1]]


@pytest.mark.parametrize('numeric',[False,True])
def test_uncorroborated_literal_or_baseline_digit_not_silently_removed(tmp_path,monkeypatch,numeric):
    prepared=fixture(proven=False,numeric=numeric)
    _,_,docs=convert(tmp_path,monkeypatch,prepared)
    refs,_=h.assert_exact_returns(docs,1)
    assert ''.join(refs[0][1].itertext())==prepared[2]
    assert h.body_texts(docs)==[prepared[1]+prepared[2]], 'an unproven authored literal is preserved independently of the placeholder'

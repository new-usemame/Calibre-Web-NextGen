"""SOURCE-free physical reference singularity cannot rely on OCR completeness.

The ORIGINAL contains two distinct glyphs. Only the locked optical description
is incomplete; the model literal plus structured reference remains unchanged.
"""
import copy
import importlib.util
from pathlib import Path

import pytest
from cps.services.reflow import vision_pipeline

_fixture=Path(__file__).with_name('test_round5_literal_structured_reference_identity.py')
if not _fixture.exists():
    _fixture=Path(vision_pipeline.__file__).parents[3]/'tests/unit'/ _fixture.name
_spec=importlib.util.spec_from_file_location('round5_complete_reference_ink_fixture',_fixture)
t=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(t)
pytestmark=pytest.mark.unit


@pytest.mark.parametrize('mark,missing',[('*','second'),('*','first'),('*','merged_word_box'),('†','second')])
def test_two_original_glyphs_survive_incomplete_optical_marker_instances(tmp_path,monkeypatch,mark,missing):
    prepared=t.fixture(mark,second=mark)
    page=prepared[0];finish_original=page.finish;observed=[]
    def finish():
        image,words,answer=finish_original()
        top=[w for w in words if w['bbox'][1]<70]
        first=next(w for w in top if w['text'].endswith(mark) and len(w['text'])>1)
        second=next(w for w in top if w['text']==mark)
        assert first['bbox'][2]<second['bbox'][0], 'two original source glyph instances are physically separate'
        observed.append((copy.deepcopy(first),copy.deepcopy(second)))
        if missing=='first':
            first['text']=first['text'][:-len(mark)]
        else:
            if missing=='merged_word_box':
                first['bbox']=[min(first['bbox'][0],second['bbox'][0]),min(first['bbox'][1],second['bbox'][1]),
                               max(first['bbox'][2],second['bbox'][2]),max(first['bbox'][3],second['bbox'][3])]
            words.remove(second)
        return image,words,answer
    page.finish=finish
    _,result,docs=t.convert(tmp_path,monkeypatch,prepared)
    assert len(observed)==1, 'only the optical description changes; original source is rendered once'
    refs,notes=t.h.assert_exact_returns(docs,2)
    assert [''.join(ref.itertext()) for _,ref in refs]==[mark,mark]
    assert len({t.h.destination(name,ref.get('href')) for name,ref in refs})==1
    assert t.h.body_texts(docs)==[prepared[1]], 'all authored lexical words remain exactly once'
    assert sum(prepared[3] in ''.join(note.itertext()) for _,note in notes)==1
    body=next(b for b in result['pages'][0]['blocks'] if b['id']=='body')
    assert body['published_tokens'][-2:]==['specimens'+mark,'<n:note>'], 'source reconciliation never rewrites the model transcription'


def test_repeated_character_observation_of_one_original_glyph_is_still_one_ref(tmp_path,monkeypatch):
    prepared=t.fixture('*')
    prepared[-1].append(copy.deepcopy(prepared[-1][0]))
    _,result,docs=t.convert(tmp_path,monkeypatch,prepared)
    refs,notes=t.h.assert_exact_returns(docs,1)
    assert ''.join(refs[0][1].itertext())=='*'
    assert t.h.body_texts(docs)==[prepared[1]]
    assert sum(prepared[3] in ''.join(note.itertext()) for _,note in notes)==1
    body=next(b for b in result['pages'][0]['blocks'] if b['id']=='body')
    assert body['published_tokens'][-2:]==['specimens*','<n:note>']

"""Synthetic SOURCE-bound mixed typography: cached native h is not a paragraph oracle."""
import importlib.util
from pathlib import Path
import pytest
from cps.services.reflow import assemble
s=importlib.util.spec_from_file_location('native_mixed_fixture',Path(__file__).with_name('test_native_publication_source_ownership.py'))
f=importlib.util.module_from_spec(s);s.loader.exec_module(f)
Synthetic,tag,words,union=f.Synthetic,f.tag,f.words,f.union

def mixed_row(obj,y,x=92,size=12):
    bold='Amber observer';normal=' explains how the source account continues'
    obj.page.insert_text((x,y),bold,fontname='hebo',fontsize=size)
    end=x+__import__('pymupdf').get_text_length(bold,fontname='hebo',fontsize=size)
    obj.page.insert_text((end,y),normal,fontname='helv',fontsize=size)
    spans=[v for b in obj.page.get_text('dict')['blocks'] for ln in b.get('lines',[]) for v in ln['spans'] if abs(v['origin'][1]-y)<.01]
    assert len(spans)==2 and spans[0]['text']==bold and spans[1]['text']==normal
    row=dict(text=bold+normal,bbox=union([v['bbox'] for v in spans]),size=size,font='mixed',row=len(obj.rows))
    obj.rows.append(row);obj.expected_text.append(row['text'])
    return row,[['t',bold,'bold'],['t',normal]]

@pytest.mark.parametrize('kind',['h','p'])
def test_source_indented_mixed_first_row_and_unindented_continuation_are_one_styled_body_paragraph(tmp_path,kind):
    obj=Synthetic(tmp_path)
    try:
        row,runs=mixed_row(obj,310);e=obj.element([row],kind=kind);e.runs=runs
        b=obj.row('with ordinary unindented words on the next physical source row',330)
        c=obj.row('and concludes this same paragraph without a new heading.',350)
        obj.element([b,c]);r=obj.publish();exact=' '.join([row['text'],b['text'],c['text']])
        assert sum(tag(n)=='p' and words(n)==exact for n in r.paragraphs)==1,'Source-proven mixed bold subject plus normal predicate in an indented first row is BODY and stays with its unindented continuation'
        assert not any(tag(n).startswith('h') and 'Amber observer' in words(n) for n in r.paragraphs),'Mixed body first row must not hijack chapter identity as h'
        assert sum(tag(n)=='strong' and words(n)=='Amber observer' for root in r.roots for n in root.iter())==1,'Paragraph reconciliation preserves the original bold span once'
    finally:obj.close()

@pytest.mark.parametrize('boundary',['all_bold','larger_type','unindented_heading','large_gap','new_indented_paragraph'])
def test_true_heading_or_distinct_source_paragraph_preserves_native_identity_and_styles(tmp_path,boundary):
    obj=Synthetic(tmp_path)
    try:
        if boundary=='all_bold':
            row=obj.row('Amber observer introduces the independent heading',310,x=92,bold=True);runs=[['t',row['text'],'bold']]
        else:row,runs=mixed_row(obj,310,x=70 if boundary=='unindented_heading' else 92,size=15 if boundary=='larger_type' else 12)
        e=obj.element([row],kind='h');e.runs=runs
        y=390 if boundary=='large_gap' else 330;x=92 if boundary=='new_indented_paragraph' else 70
        b=obj.row('This independently authored paragraph begins after the heading.',y,x=x)
        c=obj.row('It retains its own source paragraph and full text.',y+20,x=70)
        obj.element([b,c]);r=obj.publish()
        assert any(tag(n).startswith('h') and words(n)==row['text'] for n in r.paragraphs),'All-bold, larger-type, unindented or source-separated heading evidence remains h'
        assert not any(row['text']+' '+b['text'] in words(n) for n in r.paragraphs),'Source geometry and typographic boundary must veto a guessed paragraph join'
        assert any(words(n)==b['text']+' '+c['text'] for n in r.paragraphs)
        assert sum(tag(n)=='strong' and words(n)==(row['text'] if boundary=='all_bold' else 'Amber observer') for root in r.roots for n in root.iter())==1
    finally:obj.close()

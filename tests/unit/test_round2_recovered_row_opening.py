"""Independent omitted-opening row witnesses; all source words synthetic."""
import pytest
if __package__:
    from .test_round2_assembly import answer,block,optical,convert
else:
    from test_round2_assembly import answer,block,optical,convert


@pytest.mark.parametrize('case',['same_physical_row','recognized_same_row','short_same_row','terminal_prefix','new_indented_row','next_unindented_row','large_horizontal_gap','heading_suffix','caption_suffix','footnote_suffix','labelled_prefix'])
def test_omitted_paragraph_opening_must_attach_forward_only_with_physical_row_proof(tmp_path,monkeypatch,case):
    prefix='Readers cross quiet fields while patient workers prepare'
    if case=='short_same_row':prefix='Readers quietly prepare useful'
    if case=='labelled_prefix':prefix='4 Readers cross quiet fields while workers prepare'
    if case=='terminal_prefix':prefix+='.'
    suffix='small tools before each careful journey through distant villages and familiar gardens beside northern roads.'
    before=optical(prefix,20,90,columns=8,step=45)
    before=[dict(w,bbox=[w['bbox'][0],90,w['bbox'][2],102]) for w in before]
    first=optical('small tools',500 if case=='large_horizontal_gap' else 205 if case=='short_same_row' else 385,94,columns=2,step=45)
    first=[dict(w,bbox=[w['bbox'][0],94,w['bbox'][2],108]) for w in first]
    rest=optical(' '.join(suffix.split()[2:]),20,103,columns=11,step=45)
    rest=[dict(w,bbox=[w['bbox'][0],w['bbox'][1],w['bbox'][2],w['bbox'][1]+12]) for w in rest]
    if case in ('new_indented_row','next_unindented_row'):
        first=optical('small tools',45 if case=='new_indented_row' else 20,120,columns=2,step=45)
        rest=optical(' '.join(suffix.split()[2:]),20,135,columns=11,step=45)
    role={'heading_suffix':'heading','caption_suffix':'caption','footnote_suffix':'footnote'}.get(case,'body')
    bbox=(20,290,980,650) if case in ('new_indented_row','next_unindented_row') else (20,265,980,600)
    blocks=[block(suffix,ident='b2',role=role,bbox=bbox,marker='4' if role=='footnote' else '')]
    if case=='recognized_same_row':blocks.insert(0,block(prefix,ident='b1',bbox=(20,220,640,270)))
    readings=[answer(*blocks)]
    body,_,_=convert(tmp_path,monkeypatch,readings,[before+first+rest],[[]])
    if case in ('same_physical_row','recognized_same_row','short_same_row'):
        assert body==[prefix+' '+suffix],f'A single printed first row was split between omitted opening and surviving body: {body!r}'
    else:
        assert prefix+' '+suffix not in body,f'Unproven forward attachment crossed {case}: {body!r}'

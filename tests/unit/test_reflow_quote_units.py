# SPDX-License-Identifier: GPL-3.0-or-later
"""Complete printed quotation units bound structural choices."""
import copy
import pymupdf
import pytest
from cps.services.reflow import assemble,extract,skeleton,structural_ops as ops
pytestmark=pytest.mark.unit

def fixture(tmp_path,kind='multi'):
    doc=pymupdf.open();page=doc.new_page(width=500,height=700)
    for y,text in [(80,'Ordinary source body establishes a wider text column.'),(94,'Its next complete line supplies a stable source reference.'),(108,'The following passage is displayed separately:')]:page.insert_text((40,y),text,fontsize=11,fontname='tiro')
    prefix=''
    if kind=='attribution':
        prefix='(from an earlier source) ';page.insert_text((70,160),prefix.strip(),fontsize=11,fontname='tiit')
        lines=['"First complete sentence. The next sentence continues','across the printed block and ends here."'];ys=[174,188]
    elif kind=='short':lines=['"Stay."'];ys=[160]
    elif kind=='inline':lines=['Ordinary prose calls this "a name" inside its sentence.'];ys=[160]
    else:lines=['First complete sentence. The next sentence continues','across the printed block. The final sentence must stay.'];ys=[160,174]
    if kind=='closing_only':lines[-1]+='\"'
    if kind=='opening_only':lines[0]='\"'+lines[0]
    for i,(y,text) in enumerate(zip(ys,lines)):
        x=40 if kind=='inline' or (kind=='ordinary_indent' and i>0) else 95 if kind=='display_first_indent' and i==0 else 70
        page.insert_text((x,y),text,fontsize=11,fontname='tiro')
    if kind=='note':page.insert_text((70+pymupdf.get_text_length(lines[-1],fontname='tiro',fontsize=11),ys[-1]-3),'7',fontsize=7,fontname='tiro')
    for y,text in [(240,'Ordinary source body resumes with the original column.'),(254,'The following text remains distinct from the display.')]:page.insert_text((40,y),text,fontsize=11,fontname='tiro')
    path=tmp_path/(kind+'.pdf');doc.save(path);doc.close();doc=pymupdf.open(path);raw=extract.read_page(doc,0)
    source_lines=[l for b in raw.text_blocks for l in b.lines]
    def element(chosen,runs=None):
        box=(min(l.bbox[0] for l in chosen),min(l.bbox[1] for l in chosen),max(l.bbox[2] for l in chosen),max(l.bbox[3] for l in chosen))
        return assemble.Element('p',pno=0,bbox=box,line_boxes=[l.bbox for l in chosen],runs=runs or [['t',' '.join(l.text.strip() for l in chosen)]])
    before=element([l for l in source_lines if l.bbox[1]<120]);after=element([l for l in source_lines if l.bbox[1]>220])
    text=prefix+' '.join(lines);runs=[['t',text]]+([['sup','7',0]] if kind=='note' else [])
    quote=element([l for l in source_lines if 140<l.bbox[1]<210],runs)
    book=assemble.Book(elements=[before,quote,after],pages={0:[before,quote,after]},style=skeleton.BookStyle(body_size=11))
    return book,doc,raw,len(prefix),len(text)+(1 if kind=='note' else 0)

def prepare(data,layer='native'):
    book,doc,raw,*_=data
    return ops.prepare(book,doc,0,'unit-test',{'layer':layer},raw_page=raw)

@pytest.mark.parametrize('kind',['multi','attribution','short','note'])
def test_only_complete_source_unit_is_offered_including_short_displays(tmp_path,kind):
    data=fixture(tmp_path,kind);book,doc,raw,start,end=data;p=prepare(data)
    offered=[c['source_range'] for c in p.candidates() if c['kind']=='quote' and c['element_id']=='e1']
    assert offered==[[start,end]], 'partial sentences, attribution and partial sentences are excluded; attached note stays atomic'
    doc.close()

@pytest.mark.parametrize('kind',['inline','ordinary_indent'])
def test_inline_and_ordinary_first_line_indentation_do_not_make_display_quotes(tmp_path,kind):
    data=fixture(tmp_path,kind);p=prepare(data)
    assert not [c for c in p.candidates() if c['kind']=='quote'], 'ordinary text must not gain a quote menu'
    data[1].close()

def test_missing_or_uncertain_source_cannot_authorize_a_quote(tmp_path):
    data=fixture(tmp_path);book,doc,raw,*_=data
    assert not any(c['kind']=='quote' for c in ops.prepare(book,doc,0,'test',{'layer':'native'}).candidates())
    book.pages[0][1].punctuation_uncertain=True
    assert any(c['kind']=='quote' for c in prepare(data).candidates()), 'word uncertainty is preserved when the complete layout boundary is proven'
    book.pages[0][1].punctuation_uncertain=False
    changed=copy.deepcopy(raw);changed.text_blocks[1].lines[0].spans[0].text='Unrelated source reading'
    assert not any(c['kind']=='quote' for c in ops.prepare(book,doc,0,'test',{'layer':'ocr'},raw_page=changed).candidates())
    doc.close()

@pytest.mark.parametrize('kind',['multi','attribution'])
def test_final_admission_cannot_reintroduce_removed_partial_or_mixed_unit(tmp_path,kind):
    from dataclasses import replace
    from cps.services.reflow import build_epub
    data=fixture(tmp_path,kind);book,doc,raw,start,end=data;p=prepare(data)
    bad_end=book.pages[0][1].runs[0][1].index('.')+1 if kind=='multi' else end
    forged=replace(p,specs=(ops._Spec(1,'quote',0,bad_end),))
    cid=forged.candidates()[0]['candidate_id']
    reply={'protocol':ops.PROTOCOL,'snapshot_id':forged.snapshot_id,'select':[cid]}
    with pytest.raises(ops.ContractError,match='complete source quote evidence'):
        forged.accept(book,doc,reply)
    target=tmp_path/'forged.epub'
    with pytest.raises(ops.ContractError,match='complete source quote evidence'):
        build_epub.build(book,str(target),doc=doc,operation_plans=[ops.OperationPlan(forged,(cid,))])
    assert not target.exists()
    doc.close()


def test_atomic_note_and_source_word_order_survive_current_quote_admission(tmp_path):
    from cps.services.reflow import build_epub
    import zipfile
    from xml.etree import ElementTree as ET
    data=fixture(tmp_path,'note');book,doc,raw,start,end=data;p=prepare(data)
    choice=next(c for c in p.candidates() if c['kind']=='quote' and c['element_id']=='e1')
    assert choice['source_range']==[start,end]
    plan=p.accept(book,doc,dict(protocol=ops.PROTOCOL,snapshot_id=p.snapshot_id,select=[choice['candidate_id']]))
    before=copy.deepcopy(book.pages[0][1].runs);target=tmp_path/'quote.epub'
    build_epub.build(book,str(target),doc=doc,operation_plans=[plan]);assert book.pages[0][1].runs==before
    with zipfile.ZipFile(target) as z:
        blocks=[b for n in z.namelist() if n.endswith('.xhtml') for b in ET.fromstring(z.read(n)).iter('{http://www.w3.org/1999/xhtml}blockquote')]
    assert len(blocks)==1 and 'final sentence must stay.' in ''.join(blocks[0].itertext())
    assert any(s.text=='7' for s in blocks[0].iter('{http://www.w3.org/1999/xhtml}sup'))
    doc.close()


def test_truncated_source_paragraph_is_not_a_complete_display_unit(tmp_path):
    data=fixture(tmp_path);book,doc,raw,*_=data
    quote=book.pages[0][1]
    first=next(l for b in raw.text_blocks for l in b.lines if tuple(l.bbox)==tuple(quote.line_boxes[0]))
    quote.runs=[['t',first.text]];quote.line_boxes=[first.bbox];quote.bbox=first.bbox
    assert not any(c['kind']=='quote' and c['element_id']=='e1' for c in prepare(data).candidates())
    doc.close()


def test_ocr_geometry_preserves_complete_display_but_not_inferred_italic_attribution(tmp_path):
    data=fixture(tmp_path,'multi')
    assert any(c['kind']=='quote' and c['source_range']==[data[3],data[4]] for c in prepare(data,'ocr').candidates())
    data[1].close()
    data=fixture(tmp_path,'attribution')
    assert not any(c['kind']=='quote' and c['element_id']=='e1' for c in prepare(data,'ocr').candidates())
    data[1].close()


def test_missing_line_metadata_recovers_exact_complete_raw_unit(tmp_path):
    data=fixture(tmp_path);book,doc,raw,start,end=data
    quote=book.pages[0][1];quote.line_boxes=quote.line_boxes[:1];quote.bbox=quote.line_boxes[0]
    assert any(c['kind']=='quote' and c['element_id']=='e1' and c['source_range']==[start,end] for c in prepare(data).candidates())
    doc.close()

def test_qualified_uncertain_terminal_marker_keeps_proven_source_unit(tmp_path):
    data=fixture(tmp_path,'note');book,doc,raw,start,end=data
    book.pages[0][1].runs[-1]=['sup','153',0,'uncertain']
    last=next(l for b in raw.text_blocks for l in b.lines if l.bbox==book.pages[0][1].line_boxes[-1])
    last.spans[-1].text='\"'
    assert any(c['kind']=='quote' and c['element_id']=='e1' and c['source_range']==[start,end+2] for c in prepare(data).candidates())
    assert book.pages[0][1].runs[-1]==['sup','153',0,'uncertain']
    doc.close()


def split_display(tmp_path):
    data=fixture(tmp_path);book,doc,raw,*_=data
    quote=book.pages[0][1];raw=raw.to_dict()
    block=next(b for b in raw['blocks'] if len(b.get('lines',[]))==2 and b['lines'][0]['bbox'][1]>140 and b['lines'][0]['bbox'][1]<200)
    parts=[]
    for line in block['lines']:
        part=copy.deepcopy(quote);part.bbox=line['bbox'];part.line_boxes=[line['bbox']]
        part.runs=[['t',''.join(s['text'] for s in line['spans'])]]
        part.punctuation_uncertain=True;parts.append(part)
    book.elements[1:2]=parts;book.pages[0][1:2]=parts
    at=raw['blocks'].index(block)
    raw['blocks'][at:at+1]=[dict(block,lines=[line],bbox=line['bbox']) for line in block['lines']]
    return book,doc,raw,0,len(parts[0].runs[0][1])


@pytest.mark.parametrize('seam',['candidates','admission','builder'])
def test_same_page_split_display_cannot_be_admitted_as_complete_quote(tmp_path,seam):
    from dataclasses import replace
    from cps.services.reflow import build_epub
    data=split_display(tmp_path);book,doc,raw,start,end=data;p=prepare(data)
    if seam=='candidates':
        assert not any(c['kind']=='quote' and c['element_id'] in ('e1','e2') for c in p.candidates())
    else:
        forged=replace(p,specs=(ops._Spec(1,'quote',start,end),))
        cid=forged.candidates()[0]['candidate_id'];target=tmp_path/'split.epub'
        with pytest.raises(ops.ContractError,match='complete source quote evidence'):
            if seam=='admission':forged.accept(book,doc,dict(protocol=ops.PROTOCOL,snapshot_id=p.snapshot_id,select=[cid]))
            else:build_epub.build(book,str(target),doc=doc,operation_plans=[ops.OperationPlan(forged,(cid,))])
        assert not target.exists()
    doc.close()


@pytest.mark.parametrize('kind',['closing_only','display_first_indent'])
def test_complete_display_boundary_does_not_require_paired_quotes_or_uniform_first_indent(tmp_path,kind):
    data=fixture(tmp_path,kind);p=prepare(data)
    assert [c['source_range'] for c in p.candidates() if c['kind']=='quote' and c['element_id']=='e1']==[[data[3],data[4]]]
    data[1].close()


def test_opening_only_quote_does_not_prove_its_terminal_boundary(tmp_path):
    data=fixture(tmp_path,'opening_only')
    assert not any(c['kind']=='quote' and c['element_id']=='e1' for c in prepare(data).candidates())
    data[1].close()

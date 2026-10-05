"""A complete native display split before a proper noun stays one paragraph."""
import copy
from dataclasses import asdict,replace
import pytest
from cps.services.reflow import assemble,extract,skeleton,build_epub
from tests.unit.test_reflow_quote_units import fixture
pytestmark=pytest.mark.unit


@pytest.mark.parametrize('boundary',[None,'gap','indent','column','missing_close','ocr','barrier','partial','reference_far'])
def test_exact_native_split_quote_keeps_continuation_and_disclosure_in_one_source_paragraph(tmp_path,boundary):
    _,doc,raw,*_=fixture(tmp_path,'multi')
    chosen=[line for block in raw.text_blocks for line in block.lines if 140<line.bbox[1]<210]
    chosen[0].spans[0].text='"First source fragment leads into the proper noun and'
    chosen[1].spans[0].text='Saturn completes the same displayed quotation."'
    if boundary=='missing_close':chosen[1].spans[0].text='Saturn begins a different source paragraph.'
    if boundary=='ocr':
        for line in chosen:
            for span in line.spans:span.font='ocr'
    if boundary in ('gap','indent'):
        for span in chosen[1].spans:
            x=30 if boundary=='indent' else 0;y=40 if boundary=='gap' else 0
            span.bbox=tuple(v+(x if i%2==0 else y) for i,v in enumerate(span.bbox))
        chosen[1].bbox=tuple(v+(x if i%2==0 else y) for i,v in enumerate(chosen[1].bbox))
    blocks=[]
    for block in raw.text_blocks:
        if chosen[0] in block.lines:
            blocks += [extract.Block(block.number,line.bbox,[line]) for line in chosen]
        else:blocks.append(block)
    raw=replace(raw,blocks=blocks)
    if boundary=='reference_far':
        for block in raw.text_blocks:
            if any(line in chosen for line in block.lines):continue
            dy=-60 if block.bbox[1]<140 else 100
            block.bbox=tuple(v+dy if i%2 else v for i,v in enumerate(block.bbox))
            for line in block.lines:
                line.bbox=tuple(v+dy if i%2 else v for i,v in enumerate(line.bbox))
                for span in line.spans:span.bbox=tuple(v+dy if i%2 else v for i,v in enumerate(span.bbox))
    before=asdict(raw)
    regions=[skeleton.Region('body',list(block.lines),bbox=block.bbox) for block in blocks]
    first=next(r for r in regions if chosen[0] in r.lines);second=next(r for r in regions if chosen[1] in r.lines)
    if boundary=='column':second.column=1
    if boundary=='barrier':regions.insert(regions.index(second),skeleton.Region('figure',bbox=(45,165,455,170)))
    if boundary=='partial':first.lines=[copy.deepcopy(chosen[0])];first.lines[0].spans[0].text='"Only part of the source'
    # Word-layer uncertainty is retained. This source-layout join is no quote
    # transcription or permission to clear the existing punctuation notice.
    for region in (first,second):
        for line in region.lines:
            for span in line.spans:span.punctuation_uncertain=True
    before=asdict(raw)
    book=assemble.assemble([skeleton.PageSkeleton(0,500,700,regions)],skeleton.BookStyle(body_size=11),[raw])
    assert asdict(raw)==before
    if boundary!='partial':assert book.conservation.ok
    matching=[e for e in book.pages[0] if 'First source fragment' in e.text]
    if boundary is None:
        assert len(matching)==1 and 'Saturn completes' in matching[0].text,'ordinary source leading plus complete delimiters must keep the proper noun inside its quote paragraph'
        assert matching[0].punctuation_uncertain
        html=build_epub.page_fragment(book,0)
        assert html.index('First source fragment')<html.index('Saturn completes')<html.index('Punctuation uncertain',html.index('First source fragment'))
    else:
        assert not any('First source fragment' in e.text and 'Saturn completes' in e.text for e in book.pages[0])
    doc.close()


def test_closing_delimiter_from_later_extracted_line_rejoins_the_original_display(tmp_path):
    _,doc,raw,*_=fixture(tmp_path,'multi')
    chosen=[line for block in raw.text_blocks for line in block.lines if 140<line.bbox[1]<210]
    chosen[0].spans[0].text='"First source fragment ends before a proper noun and'
    chosen[1].spans[0].text='Saturn continues on a source line that ends before'
    last=copy.deepcopy(chosen[1]);last.bbox=tuple(v+14 if i%2 else v for i,v in enumerate(last.bbox))
    for span in last.spans:
        span.bbox=tuple(v+14 if i%2 else v for i,v in enumerate(span.bbox))
        span.text='the final source line closes the displayed quotation."'
    blocks=[]
    for block in raw.text_blocks:
        if chosen[0] in block.lines:blocks += [extract.Block(block.number,line.bbox,[line]) for line in chosen+[last]]
        else:blocks.append(block)
    raw=replace(raw,blocks=blocks);before=asdict(raw)
    book=assemble.assemble([skeleton.PageSkeleton(0,500,700,[skeleton.Region('body',block.lines,bbox=block.bbox) for block in blocks])],skeleton.BookStyle(body_size=11),[raw])
    assert book.conservation.ok and asdict(raw)==before
    quoted=[e for e in book.pages[0] if 'First source fragment' in e.text]
    assert len(quoted)==1 and 'Saturn continues' in quoted[0].text and 'final source line closes' in quoted[0].text
    assert len(quoted[0].line_boxes)==3
    doc.close()

"""Native PDF Unicode can collapse distinct printed quotation glyphs."""
import re
import zipfile
from xml.etree import ElementTree as ET
import pymupdf
import pytest
from cps.services.reflow import assemble, build_epub, structural_ops
pytestmark = pytest.mark.unit


def punctuation_pdf(path, kind):
    doc=pymupdf.open();page=doc.new_page(width=500,height=700)
    font=page.insert_font(fontname='body',fontbuffer=pymupdf.Font('tiro').buffer)
    text='“Quoted original words.”' if kind!='straight' else '"Quoted original words."'
    if kind=='apostrophe_variants':text='‘Quoted original words.’'
    page.insert_text((50,100),text,fontname='body',fontsize=12)
    if kind in ('collapsed','apostrophe_variants'):
        cmap=int(doc.xref_get_key(font,'ToUnicode')[1].split()[0])
        stream=doc.xref_stream(cmap)
        if kind=='collapsed':stream=stream.replace(b'<201c>',b'<0022>').replace(b'<201d>',b'<0022>')
        else:stream=stream.replace(b'<2018>',b'<0027>').replace(b'<2019>',b'<0027>')
        doc.update_stream(cmap,stream)
    doc.save(path);doc.close()
    return pymupdf.open(path)


@pytest.mark.parametrize('kind',['straight','curly','collapsed','apostrophe_variants'])
def test_only_collapsed_native_quote_forms_require_original_evidence(tmp_path,kind):
    with punctuation_pdf(tmp_path/'source.pdf',kind) as doc:
        native=doc[0].get_text().strip();book=assemble.deterministic_book(doc)
        assert book.needs_source_evidence(0)==(kind=='collapsed')
        assert ''.join(e.text for e in book.pages[0]).strip()==native
        assert book.conservation.ok


def test_native_punctuation_disclosure_links_to_original_passage_and_survives_wrappers(tmp_path):
    with punctuation_pdf(tmp_path/'source.pdf','collapsed') as doc:
        book=assemble.deterministic_book(doc)
        prepared=structural_ops.prepare(book,doc,0,'test',{'layer':'native'},seed=1)
        choice=next(c['candidate_id'] for c in prepared.candidates() if c['kind']=='quote')
        plan=prepared.accept(book,doc,{'protocol':structural_ops.PROTOCOL,'snapshot_id':prepared.snapshot_id,'select':[choice]})
        target=tmp_path/'book.epub';build_epub.build(book,str(target),doc=doc,operation_plans=[plan])
        assert build_epub.validate(str(target))==[]
        with zipfile.ZipFile(target) as z:
            chapters=''.join(z.read(n).decode() for n in z.namelist() if re.fullmatch(r'OEBPS/ch\d+\.xhtml',n))
            assert 'Original punctuation may differ' in chapters
            assert 'original-p0000.xhtml#text_0' in chapters
            original=ET.fromstring(z.read('OEBPS/original-p0000.xhtml'))
            assert any(e.get('id')=='text_0' for e in original.iter())
            assert 'Return to reflowed PDF page 1' in ''.join(original.itertext())
        book.pages[0][0].punctuation_uncertain=False
        with pytest.raises(structural_ops.ContractError):
            build_epub.build(book,str(tmp_path/'stale.epub'),doc=doc,operation_plans=[plan])


def test_joined_native_passage_keeps_uncertainty_and_complete_source_context():
    first=assemble.Element(kind='p',pno=0,runs=[['t','A passage continuing']],
                           bbox=(50,100,400,115),pages=[0])
    tail=assemble.Element(kind='p',pno=0,runs=[['t','with "quoted words".']],
                          bbox=(50,130,400,145),pages=[0],punctuation_uncertain=True)
    joined=assemble._join_within_page([first,tail],None)
    assert len(joined)==1 and joined[0].punctuation_uncertain
    assert joined[0].bbox==(50,100,400,145)
    assert assemble._copy_element(joined[0]).punctuation_uncertain

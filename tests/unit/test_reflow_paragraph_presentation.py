"""Source indentation survives semantic abstention without changing source words."""
import copy,json,zipfile
from dataclasses import asdict
from xml.etree import ElementTree as ET
import pytest
from cps.services.reflow import assemble,build_epub,enriched_source,extract,skeleton
from tests.unit.test_reflow_quote_units import fixture
pytestmark=pytest.mark.unit
X='{http://www.w3.org/1999/xhtml}'


def source_fixture(tmp_path,kind='multi'):
    _,doc,raw,*_=fixture(tmp_path,kind)
    regions=[skeleton.Region('body',block.lines,bbox=block.bbox) for block in raw.text_blocks]
    book=assemble.assemble([skeleton.PageSkeleton(0,500,700,regions)],skeleton.BookStyle(11),[raw])
    book.source_fingerprint=extract.document_fingerprint(doc)
    source=enriched_source.prepare_source_page(book,0,{'layer':'native'})
    return book,doc,raw,source


def test_written_source_inset_remains_when_no_model_chose_a_quote(tmp_path):
    book,doc,raw,source=source_fixture(tmp_path)
    before=(source.identity,source.html,copy.deepcopy(book.conservation.to_dict()),asdict(raw))
    target=tmp_path/'inset.epub'
    build_epub.build(book,str(target),doc=doc,source_pages={0:source},raw_pages={0:raw})
    assert build_epub.validate(str(target))==[]
    with zipfile.ZipFile(target) as archive:
        roots=[ET.fromstring(archive.read(n)) for n in archive.namelist() if n.startswith('OEBPS/ch') and n.endswith('.xhtml')]
        paragraphs=[p for root in roots for p in root.iter(X+'p') if 'First complete sentence.' in ''.join(p.itertext())]
        assert len(paragraphs)==1
        assert paragraphs[0].get('data-source-inset'), 'source display indentation must survive model abstention'
        assert not any(list(root.iter(X+'blockquote')) for root in roots), 'source geometry does not invent quote semantics'
        report=json.loads(archive.read('META-INF/reflow.json'))
        entry=next(e for page in report['paragraph_presentation'] for e in page['paragraph_presentation']['entries'] if e['displayed'])
        assert entry['left_em']==pytest.approx(30/11)
        css=archive.read('OEBPS/style.css').decode()
        assert 'margin-left:2.72727273em' in css
        assert 'text-indent:0' in css
        assert report['conservation']==before[2]
    assert (source.identity,source.html,book.conservation.to_dict(),asdict(raw))==before
    doc.close()


@pytest.mark.parametrize('damage',['ordinary_indent','table','column','partial','pdf_binding','wrapped','duplicate'])
def test_unsupported_geometry_or_changed_publication_retains_current_paragraph(tmp_path,damage):
    from cps.services.reflow import paragraph_presentation as pp
    book,doc,raw,source=source_fixture(tmp_path,'ordinary_indent' if damage=='ordinary_indent' else 'multi')
    if damage=='table':book.pages[0][1].table_row=True
    elif damage=='column':book.pages[0][1].column=1
    elif damage=='partial':
        element=book.pages[0][1];line=raw.text_blocks[1].lines[0]
        element.runs=[['t',line.text]];element.line_boxes=[line.bbox];element.bbox=line.bbox
    elif damage=='pdf_binding':book.source_fingerprint='0'*64
    source=enriched_source.prepare_source_page(book,0,{'layer':'native'})
    fragment=source.html
    if damage in ('wrapped','duplicate'):
        block=build_epub.split_blocks(source.html)[json.loads(source.blocks_json)['1']]
        fragment=fragment.replace(block,'<blockquote>'+block+'</blockquote>') if damage=='wrapped' else fragment+'\n'+block
    out,audit=pp.render(book,doc,source,raw,fragment)
    assert out==fragment
    assert not any(entry['displayed'] for entry in audit['entries'])
    doc.close()


def test_stale_raw_cannot_authorize_an_inset(tmp_path):
    from cps.services.reflow import paragraph_presentation as pp
    book,doc,raw,source=source_fixture(tmp_path)
    changed=copy.deepcopy(raw);changed.text_blocks[1].lines[0].spans[0].text='Changed source words'
    with pytest.raises(ValueError):pp.render(book,doc,source,changed,source.html)
    doc.close()

"""Structural chapter numerals retain their local source territory."""
import importlib.util
from pathlib import Path
import pytest
_spec=importlib.util.spec_from_file_location('number_helpers',Path(__file__).with_name('test_round4_defects.py'))
h=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(h)

@pytest.mark.parametrize('kind',['chapter','folio','no_remote_borrow'])
def test_numeric_chapter_heading_is_not_a_small_page_folio(tmp_path,monkeypatch,kind):
    page=h.InkPage()
    if kind=='folio':
        page.text('12','n','folio',x=144,y=387,size=7)
    else:
        title=page.text('12','n','heading',x=144,y=45,size=18)
        if kind=='no_remote_borrow':
            page.page.insert_text((260,387),'12.',fontsize=7)
            old_finish=page.finish
            def damaged_local_digit():
                image,words,reading=old_finish()
                for w in words:
                    if w['text']=='12' and w['bbox'][1]<80:w['text']='I2'
                return image,words,reading
            page.finish=damaged_local_digit
    page.text('A distinct authored chapter title','title','heading',y=90,size=12)
    page.text('The ordinary paragraph retains its independent authored meaning.', 'body',y=120)
    _,result,docs=h.convert_ink(tmp_path,monkeypatch,[page])
    headings=[h.plain(n) for n in h.body_nodes(docs) if n.tag.endswith('}h2')]
    if kind=='folio':assert '12' not in headings
    else:
        assert '12' in headings,'the source-typed large numeral is a chapter heading'
        record=next(b for b in result['pages'][0]['blocks'] if b['id']=='n')
        assert record['bbox'][3]<80,'a damaged local heading must never bind to a remote tiny footer of the same number'
    assert h.body_texts(docs)==['The ordinary paragraph retains its independent authored meaning.']

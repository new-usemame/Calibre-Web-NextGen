"""Original same-row spacing fences paragraph identity independently of cached columns."""
import importlib.util
from pathlib import Path
import pymupdf,pytest
from cps.services.reflow import assemble,skeleton,vision_pipeline as vp
p=Path(__file__).with_name('test_native_publication_source_ownership.py')
s=importlib.util.spec_from_file_location('readonly_native_gap_fixture',p);f=importlib.util.module_from_spec(s);s.loader.exec_module(f)

def pair(obj,wide):
    left=obj.row('The precise reading',310,x=70)
    x=390 if wide else left['bbox'][2]+4
    right=obj.row('continues across the row.',310,x=x)
    assert (right['bbox'][0]-left['bbox'][2]>100) if wide else (0<right['bbox'][0]-left['bbox'][2]<8)
    obj.element([left],column=0);obj.element([right],column=0)
    return left,right

@pytest.mark.parametrize('public',[False,True])
def test_wide_original_same_row_gap_cannot_join_damaged_cached_column_ids(tmp_path,public):
    obj=f.Synthetic(tmp_path)
    try:
        a,b=pair(obj,True)
        if public:
            result=obj.publish()
            assert not any(f.words(n)==a['text']+' '+b['text']for n in result.paragraphs),'Distinct original prose territories separated by a wide same-row gap must retain separate paragraph parents'
            assert any(f.words(n)==a['text']for n in result.paragraphs)and any(f.words(n)==b['text']for n in result.paragraphs)
        else:
            elements=obj.elements[-2:];book=assemble.Book(elements=elements,pages={0:elements},style=skeleton.BookStyle(body_size=12,page_count=1))
            physical=[dict(text=w[4],bbox=list(w[:4]))for w in obj.page.get_text('words')]
            vp._native_inline_paragraph_ownership(obj.doc,book,0,physical)
            assert len(book.pages[0])==2,'Unique consecutive Source characters and equal cached columns cannot override a wide independently printed paragraph gap'
            assert [e.text for e in book.pages[0]]==[a['text'],b['text']]
    finally:obj.close()

def test_small_original_same_row_gap_preserves_semantic_atom_and_continuity(tmp_path):
    obj=f.Synthetic(tmp_path)
    try:
        a,b=pair(obj,False)
        e=obj.elements[-2];e.runs=[['t','The precise '],['raised','reading']]
        # Semantic style is retained in cached runs; original Source supplies exact identity/spacing.
        result=obj.publish();exact=a['text']+' '+b['text']
        parents=[n for n in result.paragraphs if f.words(n)==exact];assert len(parents)==1,'Small independently printed same-row spacing must retain the continuous paragraph'
        assert sum(f.tag(n)=='sup'and f.words(n)=='reading'for n in parents[0].iter())==1,'Continuation must preserve the semantic run once'
    finally:obj.close()

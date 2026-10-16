"""Fresh native process retains its own source; parent values cannot mint it."""
import json
import copy
import pytest
import pymupdf
from cps.services.reflow import enriched_source,layout_ops,build_epub,layout_build
from cps.services.reflow.native_ipc import NativeDocument
from tests.unit.test_reflow_layout_ops import answer
pytestmark=pytest.mark.unit
  
def test_native_preparation_and_layout_publication_rebind_child_source(tmp_path):
 path=tmp_path/'native-layout.pdf'
 pdf=pymupdf.open();page=pdf.new_page(width=500,height=700)
 page.insert_text((50,80),'A printed heading',fontsize=18)
 page.insert_text((50,120),'A complete original paragraph with several words.',fontsize=12)
 diagram=pymupdf.open();drawing=diagram.new_page(width=200,height=200)
 drawing.draw_circle((100,100),60)
 page.insert_image(pymupdf.Rect(150,200,350,400),stream=drawing.get_pixmap().tobytes('png'))
 diagram.close()
 page.insert_text((50,440),'Figure 1. Original diagram',fontsize=10)
 pdf.save(path);pdf.close()
 with NativeDocument(path,scratch_root=tmp_path/'native') as doc:
  result=doc.prepare_result(recovery_opts={'mode':'off'},require_figure_caption=False)
  book=result.book;raw=result.raw_pages[0]
  canonical=enriched_source.prepare_source_page(book,0,{'layer':'native'})
  assert 'images/fig_p0000_' in canonical.html
  prepared=layout_ops.prepare(book,doc,0,canonical,raw)
  response=answer(prepared)
  plan=prepared.accept(book,doc,response,source_page=canonical,raw_page=raw)
  assert layout_build.figure_failures(book,doc,[plan],{0:canonical},{0:raw})=={}
  built=build_epub.build(book,str(tmp_path/'native.epub'),doc=doc,layout_plans=[plan],source_pages={0:canonical},raw_pages={0:raw})
  assert build_epub.validate(built.path)==[]
  assert built.sidecar['layout_operations']['applied_pages']==[0]
  # Public hashes and a genuine parent-side factory cannot re-authorize a
  # different Book from the one that the child extracted and assembled.
  forged=copy.deepcopy(book);forged.pages[0][0].runs[0][1]+=' fabricated'
  other=enriched_source.prepare_source_page(forged,0,{'layer':'native'})
  with pytest.raises(ValueError):
   layout_build.figure_failures(forged,doc,[plan],{0:other},{0:raw})
  with pytest.raises(ValueError):layout_ops.prepare(forged,doc,0,other,raw)
  with pytest.raises(ValueError):
   build_epub.build(forged,str(tmp_path/'forged.epub'),doc=doc,layout_plans=[plan],source_pages={0:other},raw_pages={0:raw})
  assert not (tmp_path/'forged.epub').exists()

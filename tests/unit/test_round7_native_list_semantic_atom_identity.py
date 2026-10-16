"""Actual native seam ownership must distinguish typed atoms and raw characters."""
import copy,re
from cps.services.reflow import assemble,skeleton,vision_pipeline as vp,build_epub
import pymupdf,pytest
A='1. Independently printed first source item.'
B='2. Independently printed second source item.'
@pytest.mark.parametrize('case',['correct_atom','wrong_marker','duplicate_owner','foreign_page','literal_bracket_alias'])
def test_published_native_list_owner_requires_exact_typed_semantic_identity(tmp_path,case):
 doc=pymupdf.open();doc.new_page(width=600,height=800)
 literal=case=='literal_bracket_alias';first=A+'[1]'if literal else A;doc[0].insert_text((70,430),first,fontsize=12)
 spans=[s for block in doc[0].get_text('dict')['blocks']for ln in block.get('lines',[])for s in ln['spans']if s['text']==first];assert len(spans)==1;box=tuple(spans[0]['bbox'])
 if not literal:
  doc[0].insert_text((box[2]+1,424),'1',fontsize=7);glyph=[s for block in doc[0].get_text('dict')['blocks']for ln in block.get('lines',[])for s in ln['spans']if s['text']=='1'];assert len(glyph)==1;b=glyph[0]['bbox'];box=(box[0],min(box[1],b[1]),max(box[2],b[2]),max(box[3],b[3]))
 doc[0].insert_text((70,458),B,fontsize=12);tail=[s for block in doc[0].get_text('dict')['blocks']for ln in block.get('lines',[])for s in ln['spans']if s['text']==B][0]['bbox']
 runs=[['t',A+'[1]']]if literal else[['t',A],['sup','1',0]];last=[['t',B]];e=assemble.Element('list',pno=0,runs=copy.deepcopy(runs)+[['t',' ']]+copy.deepcopy(last),bbox=(box[0],box[1],max(box[2],tail[2]),tail[3]),line_boxes=[box,tuple(tail)],list_items=[runs,last],list_item_boxes=[[box],[tuple(tail)]])
 book=assemble.Book(elements=[e],pages={0:[e]},notes=[]if literal else[assemble.Note(1,'Synthetic note.',0,bbox=(85,760,240,785))],style=skeleton.BookStyle(body_size=12,page_count=1))
 fragment=next(s for s in build_epub.split_blocks(build_epub.page_fragment(book,0))if s.startswith('<ol'))
 if case=='wrong_marker':fragment=fragment.replace('<sup>1</sup>','<sup>9</sup>');assert '<sup>9</sup>'in fragment
 if case=='duplicate_owner':book.pages[0].append(copy.deepcopy(e))
 if case=='foreign_page':e.pno=1
 if literal:
  alias=copy.deepcopy(e);alias.list_items[0]=[['t',A],['sup','1',0]];alias.runs=copy.deepcopy(alias.list_items[0])+[['t',' ']]+copy.deepcopy(last);book.pages[0].append(alias)
 before=copy.deepcopy(book);page={'pno':0,'body':[fragment],'asides':[]};doc.save(tmp_path/'synthetic-original.pdf')
 try:
  records=vp._native_seam_blocks(doc,book,0,page);assert len(records)==1 and book==before;record=records[0]
  if case in ('correct_atom','literal_bracket_alias'):
   assert record.get('native_source_list')is True,'Exact emitted typed source list must retain its own item identities; literal bracket text cannot alias a note atom'
   assert record['native_source_list_regions']==[[list(box)],[list(tail)]],'Every published item uses its exact original admitted physical territory'
   vp._native_published_provenance(page,records);assert page['body'][0].count('data-source-block=')==2,'Each actual source item gets its own published origin provenance once'
  else:
   assert not record.get('native_source_list',False),'Wrong displayed marker, duplicate owners or foreign physical page cannot fabricate native list ownership'
 finally:doc.close()

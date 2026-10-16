"""Independent source-free folio family positives and authored-region negatives."""
import re,zipfile
from types import SimpleNamespace
from xml.etree import ElementTree as ET
import pymupdf,pytest
from cps.services.reflow import assemble,build_epub,vision_pipeline
TITLES=('RIVER SOURCES','ANCIENT VALLEYS','COASTAL CHANGES','MOUNTAIN STREAMS')
AUTHORED='The title RIVER SOURCES belongs to this authored sentence.'
CHAPTER='Authored Chapter Heading'
class OfflineReader:
 def __init__(self,path):self.cache=path;path.mkdir();self.calls=0
 def read(self,*args):self.calls+=1;raise AssertionError('No model read')
 def read_marker(self,*args):raise AssertionError('No marker read')

def fixture(tmp_path,mode):
 count=4 if mode in ('smaller','blank') else 3
 doc=pymupdf.open();pages={};figures=[];expected=[];labels=[]
 try:
  for n in range(count):
   page=doc.new_page(width=431.97,height=647.97)
   title='ANNUAL RIVER DATA' if mode in ('figure','table','authored_heading') else TITLES[n]
   folio=str((201,205,213)[n] if mode in ('wrong_sequence','wrong_sequence_paragraph') else 201+n)
   fontsize=9 if mode=='smaller' and n==3 else 10
   page.insert_text((54,66),title,fontsize=fontsize,fontname='tiro')
   page.insert_text((370,66),folio,fontsize=fontsize,fontname='tiro')
   words=page.get_text('words');numeric=next(w for w in words if w[4]==folio)
   alpha=[w for w in words if w[4]!=folio]
   assert numeric[0]-max(w[2] for w in alpha)>=fontsize*3
   assert max(w[3] for w in words)/page.rect.height>.10
   label=title+' '+folio;labels.append(label)
   rows=[]
   def element(kind,text,bbox,level=0):return assemble.Element(kind,runs=[['t',text]],pno=n,bbox=bbox,level=level)
   blank=mode=='blank' and n==3
   if n==0:
    page.insert_text((54,106),CHAPTER,fontsize=12)
    rows.append(element('h',CHAPTER,(54,92,310,110),1))
    page.insert_text((54,128),AUTHORED,fontsize=10)
    rows.append(element('p',AUTHORED,(54,114,391,132)))
   body=f'The number {folio} remains in this ordinary authored body sentence.'
   if not blank:
    page.insert_text((54,154),body,fontsize=10);expected.append(body)
   if mode in ('figure','table'):
    # Explicit source-region ownership beats a plausible folio/style pattern.
    bbox=(46,48,397,81)
    page.draw_rect(pymupdf.Rect(bbox),color=(0,0,0),width=.5)
    rows.append(element('p',label,(54,53,391,71)))
    rows.append(assemble.Element('fig',pno=n,bbox=bbox))
    figures.append({'pno':n,'bbox':bbox,'found':'source_visual_table' if mode=='table' else 'native'})
    rows.append(element('p',body,(54,140,391,158)))
   elif mode in ('wrong_sequence','wrong_sequence_paragraph','authored_heading'):
    rows.append(element('p' if mode=='wrong_sequence_paragraph' else 'h',label,(54,53,391,71),2))
    rows.append(element('p',body,(54,140,391,158)))
   else:
    # Native cache already omitted the printed folio, and fused only title to body.
    rows.append(element('p',title+(' '+body if not blank else ''),(54,53,391,158 if not blank else 71)))
   pages[n]=rows
  book=assemble.Book(elements=[e for rows in pages.values() for e in rows],pages=pages,figures=figures)
  reader=OfflineReader(tmp_path/'unused-cache');out=tmp_path/'folio-family.epub'
  assert all(vision_pipeline.clean_native(p) for p in doc)
  report=vision_pipeline.convert(doc,str(out),reader,native_prepared=SimpleNamespace(book=book))
  assert reader.calls==0 and report['vision_pages']==[] and report['native_pages']==list(range(count))
  assert build_epub.validate(str(out))==[]
  with zipfile.ZipFile(out) as z:
   roots=[ET.fromstring(z.read(name)) for name in z.namelist() if re.fullmatch(r'OEBPS/ch\d+\.xhtml',name)]
  texts=lambda kinds:[' '.join(''.join(e.itertext()).split()) for root in roots for e in root.iter() if e.tag.rsplit('}',1)[-1] in kinds and 'source-evidence-notice' not in e.get('class','').split()]
  paragraphs=texts(['p']);headings=texts(['h1','h2','h3','h4','h5','h6'])
  body=' '.join(paragraphs)
  assert all(t in body for t in expected),'Every authored BODY number/word must remain'
  assert AUTHORED in paragraphs and CHAPTER in headings,'Same title words in BODY and real chapter heading survive'
  return paragraphs,headings,labels
 finally:doc.close()

def test_varying_titles_share_native_font_and_physical_folio_offset(tmp_path):
 paragraphs,_,_=fixture(tmp_path,'varying')
 assert all(not any(p.startswith(title) for p in paragraphs) for title in TITLES[:3]),'Varying source-proven running titles leave only their authored BODY continuation'

def test_smaller_header_joins_same_font_folio_family_without_rewriting_body(tmp_path):
 paragraphs,_,_=fixture(tmp_path,'smaller')
 assert all(not any(p.startswith(title) for p in paragraphs) for title in TITLES),'One smaller source-proven header belongs to the consistent physical folio/font family'

def test_blank_native_leaf_keeps_no_proven_header_in_body(tmp_path):
 paragraphs,_,_=fixture(tmp_path,'blank')
 assert all(not any(p.startswith(title) for p in paragraphs) for title in TITLES),'Source-family proof covers a header-only page without inventing a local body font median'

def test_unrelated_numeric_chapter_sequence_and_authored_heading_survive(tmp_path):
 _,headings,labels=fixture(tmp_path,'wrong_sequence')
 assert all(label in headings for label in labels),'Far-apart authored chapter numbering is not the physical folio sequence'

@pytest.mark.parametrize('mode',['figure','table'])
def test_author_owned_native_region_label_survives_even_consistent_folio_style(tmp_path,mode):
 paragraphs,_,labels=fixture(tmp_path,mode)
 assert all(label in paragraphs for label in labels),'Author-owned native figure/table labels cannot become marginal furniture from repetition or sequence'


def test_unrelated_numeric_sequence_on_paragraph_carrier_is_not_folio_family(tmp_path):
 paragraphs,_,labels=fixture(tmp_path,'wrong_sequence_paragraph')
 assert all(label in paragraphs for label in labels),'Varying authored marginal labels with inconsistent numeric-minus-physical-index offsets remain in paragraph carriers'

def test_authored_heading_stays_even_with_same_font_and_constant_folio_offset(tmp_path):
 _,headings,labels=fixture(tmp_path,'authored_heading')
 assert all(label in headings for label in labels),'Native authored h structure cannot be erased by repeated marginal font and constant number sequence'

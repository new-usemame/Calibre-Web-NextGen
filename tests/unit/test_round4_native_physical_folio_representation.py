"""Independent same-line folio geometry and title-only native carrier controls."""
import re,zipfile
from types import SimpleNamespace
from xml.etree import ElementTree as ET
import pymupdf
from cps.services.reflow import assemble,build_epub,vision_pipeline
HEADER="RIVER STUDIES"
AUTHORED="The phrase RIVER STUDIES belongs to this sentence."
CHAPTER="Sources and Journeys"
class OfflineReader:
 def __init__(self,p):self.cache=p;p.mkdir();self.calls=0
 def read(self,*args):self.calls+=1;raise AssertionError("No model request")
 def read_marker(self,*args):raise AssertionError("No marker request")

def fixture(tmp_path,mode,body_size=10,header_baseline=48):
 doc=pymupdf.open();pages={};expected_body=[]
 for n in range(3):
  page=doc.new_page(width=431.97,height=647.97);folio=str(201+n)
  if mode=="title_only":
   page.insert_text((54,header_baseline),HEADER,fontsize=10,fontname="tiro")
   page.insert_text((370,header_baseline),folio,fontsize=10,fontname="tiro")
  else:
   line=HEADER+(" " if mode=="close_heading" else " "*40)+folio
   page.insert_text((54,header_baseline),line,fontsize=10,fontname="tiro")
   lines=[line for block in page.get_text("dict")["blocks"] for line in block.get("lines",[]) if HEADER in "".join(s.get("text","") for s in line["spans"])]
   assert len(lines)==1 and len(lines[0]["spans"])==1,"Source representation is exactly one PDF line and one font span"
   words=page.get_text("words");title_last=next(w for w in words if w[4]=="STUDIES");numeric=next(w for w in words if w[4]==folio);gap=numeric[0]-title_last[2]
   assert gap <30 if mode=="close_heading" else gap >=100,"Source gap distinguishes authored adjacent number from physical detached folio"
  body=f"The number {folio} belongs to an authored body observation."
  expected_body.append(body);page.insert_text((54,140),body,fontsize=body_size)
  rows=[]
  def element(kind,text,bbox,level=0):return assemble.Element(kind,runs=[["t",text]],pno=n,bbox=bbox,level=level)
  if n==0:
   page.insert_text((54,90),CHAPTER,fontsize=12);rows.append(element("h",CHAPTER,(54,76,260,94),1))
   page.insert_text((54,110),AUTHORED,fontsize=body_size);rows.append(element("p",AUTHORED,(54,96,365,114)))
  if mode=="close_heading":
   rows.append(element("h",HEADER+" "+folio,(54,header_baseline-12,391,header_baseline+4),2))
   rows.append(element("p",body,(54,126,391,144)))
  else:
   prefix=HEADER+" " if mode=="title_only" else HEADER+" "+folio+" "
   rows.append(element("p",prefix+body,(54,header_baseline-12,391,144)))
  pages[n]=rows
 book=assemble.Book(elements=[e for rows in pages.values() for e in rows],pages=pages)
 reader=OfflineReader(tmp_path/"unused-cache");out=tmp_path/"physical-folio.epub"
 try:
  assert all(vision_pipeline.clean_native(page) for page in doc)
  report=vision_pipeline.convert(doc,str(out),reader,native_prepared=SimpleNamespace(book=book))
  assert reader.calls==0 and report["vision_pages"]==[] and report["native_pages"]==[0,1,2]
  assert build_epub.validate(str(out))==[]
  with zipfile.ZipFile(out) as z:roots=[ET.fromstring(z.read(name)) for name in z.namelist() if re.fullmatch(r"OEBPS/ch\d+\.xhtml",name)]
  paragraphs=[" ".join("".join(e.itertext()).split()) for root in roots for e in root.iter() if e.tag.rsplit("}",1)[-1]=="p" and "source-evidence-notice" not in e.get("class","").split()]
  headings=[" ".join("".join(e.itertext()).split()) for root in roots for e in root.iter() if e.tag.rsplit("}",1)[-1] in ["h1","h2","h3","h4","h5","h6"]]
  body=" ".join(paragraphs)
  assert all(text in body for text in expected_body),"Physical folio fallback cannot delete same authored number later in BODY"
  assert AUTHORED in paragraphs and CHAPTER in headings
  return body,headings
 finally:doc.close()

def test_one_pdf_line_and_span_detached_folio_proves_equal_font_header(tmp_path):
 body,headings=fixture(tmp_path,"one_span",10,48)
 assert body.count(HEADER)==1,"A physical10-font detached folio is source proof even within one PDF line/span"

def test_one_pdf_line_and_span_detached_folio_proves_vertically_shifted_header(tmp_path):
 body,headings=fixture(tmp_path,"one_span",11,66)
 assert body.count(HEADER)==1,"A one-span detached folio proves shifted repeated marginal style"

def test_separate_printed_folio_title_with_title_only_cached_body_preserves_authored_number(tmp_path):
 body,headings=fixture(tmp_path,"title_only",10,48)
 assert body.count(HEADER)==1,"Compound source row with already-excluded cached folio can remove unique source-owned title only"

def test_close_number_authored_marginal_heading_is_not_detached_folio(tmp_path):
 body,headings=fixture(tmp_path,"close_heading",10,48)
 assert all(HEADER+" "+str(201+n) in headings for n in range(3)),"Near numeric authored chapter label is preserved despite repeated alpha words"
 assert body.count(HEADER)==1

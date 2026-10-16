"""Independent source-free native repeated marginal style versus local layout controls."""
import re,zipfile
from types import SimpleNamespace
from xml.etree import ElementTree as ET
import pymupdf
from cps.services.reflow import assemble,build_epub,vision_pipeline
HEADER="RIVER STUDIES"
AUTHORED="The phrase RIVER STUDIES belongs to this sentence."
CHAPTER="Sources and Journeys"
UNPROVEN="Unrepeated Section Name"
class OfflineReader:
 def __init__(self,p):self.cache=p;p.mkdir();self.calls=0
 def read(self,*args):self.calls+=1;raise AssertionError("No model request")
 def read_marker(self,*args):raise AssertionError("No marker request")
def fixture(tmp_path,body_size,header_baseline,unproven=False):
 doc=pymupdf.open();pages={}
 for n in range(3):
  page=doc.new_page(width=431.97,height=647.97)
  page.insert_text((54,header_baseline),HEADER,fontsize=10)
  page.insert_text((370,header_baseline),str(201+n),fontsize=10)
  rows=[]
  def element(kind,text,bbox,level=0):return assemble.Element(kind,runs=[["t",text]],pno=n,bbox=bbox,level=level)
  if n==0:
   page.insert_text((54,90),CHAPTER,fontsize=12);rows.append(element("h",CHAPTER,(54,76,260,94),1))
   if unproven:
    page.insert_text((54,80),UNPROVEN,fontsize=10);rows.append(element("h",UNPROVEN,(54,69,260,84),2))
   page.insert_text((54,110),AUTHORED,fontsize=body_size);rows.append(element("p",AUTHORED,(54,96,365,114)))
   text="A native sentence moves through the valley"
   page.insert_text((54,140),text,fontsize=body_size)
   cached=text+" "+HEADER+" 201"
   rows.append(element("p",cached,(54,header_baseline-12,391,144)))
  elif n==1:
   text="before entering the sea."
   page.insert_text((54,110),text,fontsize=body_size)
   rows.append(element("p",HEADER+" 202 "+text,(54,header_baseline-12,391,114)))
  else:
   text="The final paragraph gives a separate complete observation."
   page.insert_text((54,110),text,fontsize=body_size)
   rows.append(element("p",text+" "+HEADER+" 203",(54,header_baseline-12,391,114)))
  pages[n]=rows
 book=assemble.Book(elements=[e for rows in pages.values() for e in rows],pages=pages)
 reader=OfflineReader(tmp_path/"unused-cache");out=tmp_path/"repeated-style.epub"
 try:
  assert all(vision_pipeline.clean_native(page) for page in doc)
  report=vision_pipeline.convert(doc,str(out),reader,native_prepared=SimpleNamespace(book=book))
  assert reader.calls==0 and report["vision_pages"]==[] and report["native_pages"]==[0,1,2]
  assert build_epub.validate(str(out))==[]
  with zipfile.ZipFile(out) as z:roots=[ET.fromstring(z.read(name)) for name in z.namelist() if re.fullmatch(r"OEBPS/ch\d+\.xhtml",name)]
  paragraphs=[" ".join("".join(e.itertext()).split()) for root in roots for e in root.iter() if e.tag.rsplit("}",1)[-1]=="p" and "source-evidence-notice" not in e.get("class","").split()]
  headings=[" ".join("".join(e.itertext()).split()) for root in roots for e in root.iter() if e.tag.rsplit("}",1)[-1] in ["h1","h2","h3","h4","h5","h6"]]
  assert AUTHORED in paragraphs and CHAPTER in headings
  return paragraphs,headings
 finally:doc.close()
def test_repeated_marginal_style_survives_equal_local_body_or_table_font(tmp_path):
 paragraphs,headings=fixture(tmp_path,10,48)
 assert " ".join(paragraphs).count(HEADER)==1,"Repeated source-proved header style remains furniture when local body/table median equals its font"
 assert any("A native sentence moves through the valley before entering the sea." in p for p in paragraphs)
 assert not any(re.search(r"\b20[123]\b",p) for p in paragraphs)
def test_repeated_marginal_style_survives_shift_past_ten_percent_page_height(tmp_path):
 paragraphs,headings=fixture(tmp_path,11,66)
 assert " ".join(paragraphs).count(HEADER)==1,"Same repeated marginal source style at10.6% height cannot be kept in BODY merely by hard10% gate"
 assert any("A native sentence moves through the valley before entering the sea." in p for p in paragraphs)
def test_unproven_equal_font_section_and_authored_chapter_are_preserved(tmp_path):
 paragraphs,headings=fixture(tmp_path,10,48,unproven=True)
 assert UNPROVEN in headings and CHAPTER in headings
 assert AUTHORED in paragraphs

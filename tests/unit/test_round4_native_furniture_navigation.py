"""Independent source-free actual-converter native navigation preservation controls."""
from types import SimpleNamespace
from xml.etree import ElementTree as ET
import zipfile, pymupdf
from cps.services.reflow import assemble, build_epub, vision_pipeline
HEADER="RIVER STUDIES"
SOURCE="Read the valley reference in this ordinary native sentence."
DEST="The destination preserves its ordinary native body sentence."
NS={"x":"http://www.w3.org/1999/xhtml"}

class OfflineNativeReader:
 def __init__(self,path):self.cache=path;path.mkdir();self.calls=0
 def read(self,*args):self.calls+=1;raise AssertionError("No model request permitted")
 def read_marker(self,*args):raise AssertionError("No marker request permitted")

def fixture(tmp_path,mode):
 doc=pymupdf.open();pages={}
 for n in range(3):
  page=doc.new_page(width=431.97,height=647.97)
  page.insert_text((54,48),HEADER,fontsize=10);page.insert_text((370,48),str(201+n),fontsize=10)
  text=SOURCE if n==0 else DEST if n==1 else "The final page contains another ordinary complete native sentence."
  page.insert_text((54,103),text,fontsize=11)
  prefix=HEADER+" "+str(201+n)+" "
  def el(runs,bbox):return assemble.Element("p",runs=runs,pno=n,bbox=bbox)
  if mode=="offsets":pages[n]=[el([["t",prefix+text]],(54,36,391,108))]
  elif mode=="removed_target" and n==1:
   pages[n]=[el([["t",prefix.strip()]],(54,36,391,55)),el([["t",text]],(54,90,391,108))]
  elif mode=="empty_slot" and n==1:
   pages[n]=[el([],(54,70,391,75)),el([["t",text]],(54,90,391,108)),el([["t",prefix.strip()]],(54,36,391,55))]
  else:pages[n]=[el([["t",text]],(54,90,391,108)),el([["t",prefix.strip()]],(54,36,391,55))]
 nav=dict(id="pdfgoto_p0000_x17",kind=1,status="resolved",pno=0,
  source_element=0,source_item=None,source_offset=SOURCE.index("valley"),source_extent=len("valley"),
  dest_page=1,dest_element=1 if mode=="empty_slot" else 0,dest_item=None,dest_offset=DEST.index("destination"),dest_note=None,
  label="valley",dest_point=[54,100])
 if mode=="offsets":
  nav["source_offset"]+=len(HEADER+" 201 ");nav["dest_offset"]+=len(HEADER+" 202 ")
 if mode=="removed_target":nav["dest_offset"]=3
 book=assemble.Book(elements=[e for values in pages.values() for e in values],pages=pages,source_navigation=[nav])
 reader=OfflineNativeReader(tmp_path/"unused-cache");out=tmp_path/"navigation.epub"
 try:
  assert all(vision_pipeline.clean_native(page) for page in doc)
  report=vision_pipeline.convert(doc,str(out),reader,native_prepared=SimpleNamespace(book=book))
  assert reader.calls==0 and report["vision_pages"]==[] and report["native_pages"]==[0,1,2]
  assert build_epub.validate(str(out))==[],"Header text removal must preserve valid native link targets"
  with zipfile.ZipFile(out) as z:
   docs={name:ET.fromstring(z.read(name)) for name in z.namelist() if name.startswith("OEBPS/ch") and name.endswith(".xhtml")}
  refs=[(name,a) for name,root in docs.items() for a in root.findall(".//x:a",NS) if "pdfgoto" in a.get("href","")]
  assert len(refs)==1
  name,ref=refs[0];assert "".join(ref.itertext())=="valley","Exact surviving authored source-link label must retain its original word"
  file,ident=ref.get("href").split("#",1);targetfile="OEBPS/"+file if file else name
  targets=[e for e in docs[targetfile].iter() if e.get("id")==ident]
  assert len(targets)==1
  target=targets[0]
  parents={child:parent for root in docs.values() for parent in root.iter() for child in parent}
  source_ids=[e.get("id") for e in docs[name].iter() if "pdfref_" in e.get("id","")]
  assert len(source_ids)==1
  assert any(a.get("href","").endswith("#"+source_ids[0]) and "".join(a.itertext())=="Return" for root in docs.values() for a in root.findall(".//x:a",NS)),"Native destination keeps exact return to source"
  body=" ".join("".join(e.itertext()) for root in docs.values() for e in root.findall(".//x:p",NS) if "source-evidence-notice" not in e.get("class","").split())
  assert HEADER not in body,"Navigation survival must not restore source-proved marginal furniture"
  return target,parents[target]
 finally:doc.close()

def test_untouched_empty_native_element_before_destination_preserves_element_index(tmp_path):
 target,parent=fixture(tmp_path,"empty_slot")
 assert (target.tail or "").startswith("destination"),"Destination remains at original native phrase after empty carrier"

def test_surviving_source_and_destination_offsets_shift_after_header_prefix_removal(tmp_path):
 target,parent=fixture(tmp_path,"offsets")
 assert (target.tail or "").startswith("destination"),"Source-proved removed prefix shifts target to same surviving body word"

def test_destination_inside_removed_header_remains_valid_empty_anchor(tmp_path):
 target,parent=fixture(tmp_path,"removed_target")
 assert not "".join(parent.itertext()).strip(),"Destination inside removed furniture is retained at its empty original carrier, not moved into authored body"

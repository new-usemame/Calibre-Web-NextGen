"""Frozen excerpt accuracy from actual visible source-provenance XHTML.
Sidecar geometry selects source blocks; sidecar words never supply scored text.
"""
from __future__ import annotations
import argparse,collections,hashlib,importlib,json,math,pathlib,posixpath,re,sys,unicodedata,zipfile
from lxml import etree
import cssselect2,tinycss2
VERSION='actual-dom-excerpt-2'
HERE=pathlib.Path(__file__).resolve().parent
BENCH=HERE.parents[2]/'v2-plan/run-v3-model-reading'
EPUBTYPE='{http://www.idpf.org/2007/ops}type'
ROLES={'body','heading','caption','other'}
SKIP={'noteref','source-disclosure-ref','note-return','vision-disclosure','source-disclosure','native-disclosure','about-conversion','original-page','source-original'}
NOTES={'footnote','footnotes','source-notes','notes','endnotes'}

def sha(p):return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()
def lexical(s):
 # U+00AD is an invisible discretionary break, never a visible word boundary.
 s=s.replace('\u00ad','')
 s=re.sub(r'<n:[^>]+>',' ',s);s=re.sub(r'(\w)-\s*\n\s*(\w)',r'\1\2',s)
 return re.findall(r"\w+(?:[’']\w+)*",unicodedata.normalize('NFKC',s).replace('’',"'").casefold())
def score(expected,published):
 a,b=lexical(expected),lexical(published);m,n=len(a),len(b);dp=[[0]*(n+1) for _ in range(m+1)]
 for i in range(1,m+1):
  dp[i][0]=i
  for j in range(1,n+1):dp[i][j]=min(dp[i-1][j]+1,dp[i][j-1]+1,dp[i-1][j-1]+(a[i-1]!=b[j-1]))
 end=min(range(n+1),key=lambda j:dp[m][j]);i,j=m,end;s=ins=d=0
 while i:
  if j and dp[i][j]==dp[i-1][j-1]+(a[i-1]!=b[j-1]):s+=a[i-1]!=b[j-1];i-=1;j-=1
  elif dp[i][j]==dp[i-1][j]+1:d+=1;i-=1
  else:ins+=1;j-=1
 return {'reference_words':m,'candidate_words':n,'substitutions':s,'insertions':ins,'deletions':d,'errors':s+ins+d,'wer':(s+ins+d)/m if m else None,'window_start':j,'window_end':end}
def rectangle(b):return isinstance(b,list) and len(b)==4 and all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) for v in b) and b[2]>b[0] and b[3]>b[1]
def overlap(a,b):return rectangle(a) and rectangle(b) and min(a[2],b[2])>max(a[0],b[0]) and min(a[3],b[3])>max(a[1],b[1])
def tag(el):return etree.QName(el).localname

def hidden_set(z,root,path,warnings):
 matcher=cssselect2.Matcher();seen=set();hidden=set()
 def hides(decls):
  for d in decls:
   if d.type!='declaration':continue
   v=tinycss2.serialize(d.value).strip().lower()
   if d.lower_name in ('display','visibility') and v in ('none','hidden','collapse'):return True
   if d.lower_name in ('opacity','font-size') and re.fullmatch(r'0(?:\.0+)?(?:px|pt|em|rem|%)?',v):return True
  return False
 def rules(items,base):
  for item in items:
   if item.type=='qualified-rule':
    if not hides(tinycss2.parse_declaration_list(item.content,skip_comments=True,skip_whitespace=True)):continue
    try:
     for sel in cssselect2.compile_selector_list(tinycss2.serialize(item.prelude)):matcher.add_selector(sel,True)
    except cssselect2.SelectorError:warnings.append('unresolved_hidden_css_selector')
   elif item.type=='at-rule' and item.lower_at_keyword=='import':
    v=[x for x in item.prelude if x.type not in ('whitespace','comment')];target=None
    if v and v[0].type in ('string','url'):target=v[0].value
    elif v and v[0].type=='function' and v[0].lower_name=='url':target=tinycss2.serialize(v[0].arguments).strip().strip('"\'')
    if target:load(posixpath.normpath(posixpath.join(posixpath.dirname(base),target)))
    else:warnings.append('unresolved_css_import')
   elif item.type=='at-rule' and item.content is not None:rules(tinycss2.parse_rule_list(item.content,skip_comments=True,skip_whitespace=True),base)
   elif item.type=='error':warnings.append('css_parse_error')
 def load(p):
  if p in seen:return
  seen.add(p)
  if p not in z.namelist():warnings.append('unresolved_css_resource');return
  rules(tinycss2.parse_stylesheet(z.read(p).decode(errors='replace'),skip_comments=True,skip_whitespace=True),p)
 for el in root.iter():
  if not isinstance(el.tag,str):continue
  if tag(el)=='link' and 'stylesheet' in el.get('rel','').split():load(posixpath.normpath(posixpath.join(posixpath.dirname(path),el.get('href',''))))
  if tag(el)=='style':rules(tinycss2.parse_stylesheet(el.text or '',skip_comments=True,skip_whitespace=True),path)
 for wrapper in cssselect2.ElementWrapper.from_xml_root(root).iter_subtree():
  e=wrapper.etree_element
  if matcher.match(wrapper) or e.get('hidden') is not None or e.get('aria-hidden','').lower()=='true' or hides(tinycss2.parse_declaration_list(e.get('style',''),skip_comments=True,skip_whitespace=True)):hidden.add(e)
 return hidden

def extract_actual(epub):
 blocks=collections.defaultdict(list);runs=[];paragraphs=[];marks=[];warnings=[];occurrences=collections.Counter();geometries={};excluded=collections.Counter();spine=[]
 with zipfile.ZipFile(epub) as z:
  container=etree.fromstring(z.read('META-INF/container.xml'));opfpath=container.xpath('//*[local-name()="rootfile"]/@full-path')[0];opf=etree.fromstring(z.read(opfpath));base=posixpath.dirname(opfpath)
  manifest={e.get('id'):e for e in opf.xpath('//*[local-name()="manifest"]/*')}
  for item in opf.xpath('//*[local-name()="spine"]/*'):
   if item.get('linear')=='no':continue
   entry=manifest[item.get('idref')]
   if 'nav' in entry.get('properties','').split():continue
   path=posixpath.normpath(posixpath.join(base,entry.get('href')))
   # These are converter control/support channels, not published source prose.
   if re.search(r'(?:vision-about|original[_-]|source-checks|source-index|source-original)',posixpath.basename(path)):excluded['support_documents']+=1;continue
   spine.append(path);root=etree.fromstring(z.read(path));body=root.xpath('//*[local-name()="body"]')
   if not body:continue
   hidden=hidden_set(z,root,path,warnings)
   def walk(e,owner=None,key=None,p=None,note=False,figure=False):
    if not isinstance(e.tag,str):return
    cls=set(e.get('class','').split());typ=set(e.get(EPUBTYPE,'').split());kind=tag(e)
    note=note or bool(cls&NOTES) or bool(typ&{'footnote','endnote','endnotes'}) or kind=='aside';figure=figure or kind=='figure'
    if e in hidden:excluded['hidden_nodes']+=1;return
    if 'data-source-page' in e.attrib:
     try:owner=int(e.get('data-source-page'))
     except ValueError:warnings.append('invalid_source_page');owner=None
     if 'data-source-block' in e.attrib:
      key=(owner,e.get('data-source-block'));occurrences[key]+=1
      try:geometry=json.loads(e.get('data-source-regions','null'))
      except ValueError:geometry=None
      if key in geometries and geometries[key]!=geometry:warnings.append('conflicting_dom_block_geometry')
      geometries[key]=geometry
    if not note and not figure and owner is not None and 'source-disclosure-ref' in cls:marks.append({'page':owner,'block':key[1] if key else None,'href':e.get('href'),'path':path,'kind':p['kind'] if p else 'unowned'})
    if note or figure or cls&SKIP or 'noteref' in typ or kind in ('img','script','style','nav'):
     excluded['notes' if note else 'figures' if figure else kind]+=1;return
    if kind in ('p','li','blockquote','h1','h2','h3','h4','h5','h6','td','th'):
     p={'index':len(paragraphs),'path':path,'kind':'heading' if kind.startswith('h') else 'caption' if 'caption' in cls else 'body','owned':[],'unowned':[]};paragraphs.append(p)
    def chunk(text):
     if text is None:return
     if p is not None:
      p['owned' if owner is not None else 'unowned'].append(text)
      if owner is None and not lexical(text):p['owned'].append(text)
     runs.append({'key':key,'text':text,'paragraph':p['index'] if p else None})
     if key is not None:blocks[key].append(text)
    chunk(e.text)
    for child in e:
     walk(child,owner,key,p,note,figure);chunk(child.tail)
   walk(body[0])
  side=json.loads(z.read('META-INF/reflow.json')) if 'META-INF/reflow.json' in z.namelist() else {}
 counts=collections.Counter()
 for p in paragraphs:
  counts[p['kind']+'_lexical_words']+=len(lexical(''.join(p['owned'])));counts['unowned_visible_lexical_words']+=len(lexical(''.join(p['unowned'])))
 counts['reading_flow_lexical_words']=sum(counts[k+'_lexical_words'] for k in ['body','heading','caption']);counts['body_disclosure_marks']=sum(m['kind']=='body' for m in marks);counts['reading_flow_disclosure_marks']=len(marks)
 for prefix in ('body','reading_flow'):
  denom=counts[prefix+'_lexical_words'];counts[prefix+'_marks_per_1000_words']=1000*counts[prefix+'_disclosure_marks']/denom if denom else None
 return {'blocks':dict(blocks),'runs':runs,'geometries':geometries,'occurrences':dict(occurrences),'counts':dict(counts),'marks':marks,'warnings':warnings,'excluded':dict(excluded),'spine':spine,'sidecar':side,'epub_sha256':sha(epub)}

def measure_span(sample,expected,dom):
 page=sample['page_index0'];geometry=sample['bbox'];selected=[];missing=[];candidate=[];mismatch=[];invalid=[]
 record=dom['sidecar'].get('pages',{}).get(str(page),dom['sidecar'].get('pages',{}).get(page,{}))
 for block in record.get('blocks',[]):
  if block.get('role') not in ROLES:continue
  regions=block.get('source_regions',[block.get('bbox')])
  if not isinstance(regions,list) or not regions or not all(rectangle(b) for b in regions):invalid.append(block.get('id'));continue
  if not any(overlap(b,geometry) for b in regions):continue
  key=(page,block['id']);selected.append(block['id'])
  if key not in dom['blocks']:missing.append(block['id']);continue
  if dom['geometries'].get(key)!=regions:mismatch.append(block['id'])
  candidate.append(key)
 selected_keys=set(candidate);pieces=[];previous=None;gap=False
 for run in dom['runs']:
  if run['key'] not in selected_keys:
   if run['key'] is None and not lexical(run['text']) and pieces and run['paragraph']==previous:pieces.append(run['text'])
   elif lexical(run['text']):gap=True
   continue
  if pieces and (run['paragraph']!=previous or gap):pieces.append('\n')
  pieces.append(run['text']);previous=run['paragraph'];gap=False
 published=''.join(pieces)
 return {'id':sample['id'],'book':sample['book'],'page_index0':page,'source_bbox':geometry,'selected_block_ids':selected,'missing_live_dom_blocks':missing,'dom_sidecar_geometry_mismatches':mismatch,'invalid_geometry_blocks':invalid,'candidate_sha256':hashlib.sha256(published.encode()).hexdigest(),'candidate_source':'actual_visible_body_provenance_span_text','sidecar_words_used':False,**score(expected,published)}

def frozen_inputs():
 return {str(p):sha(p) for p in [BENCH/'manual-truth.json',BENCH/'samples.json']}
def freeze(out):
 helpers={}
 for name in ('lxml','tinycss2','cssselect2'):
  m=importlib.import_module(name);root=pathlib.Path(m.__file__).parent;helpers[name]={'version':getattr(m,'__version__',None),'sources':{str(p):sha(p) for p in sorted(root.rglob('*.py'))}}
 value={'method_version':VERSION,'method_sha256':sha(__file__),'control_test_sha256':sha(HERE/'test_dom_accuracy.py'),'control_log_sha256':sha(HERE/'controls.log'),'normalization_control_test_sha256':sha(HERE/'test_soft_hyphen.py'),'normalization_control_log_sha256':sha(HERE/'soft-hyphen-after.log'),'normalization_failed_probe_sha256':sha(HERE/'soft-hyphen-before.log'),'normalization_change':'Strip invisible U+00AD before lexical segmentation. Historical round-one normalizer retained it as a boundary. Visible ASCII/U+2010/U+2011 hyphens remain equivalent compound separators and distinct from an unhyphenated word.','freeze_utc':__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),'frozen_input_sha256':frozen_inputs(),'helpers':helpers,'runtime':sys.version,'method':'Actual visible XHTML provenance text, geometry-only source-rectangle selection, semi-global optimistic excerpt window','limits':['Free prefix/suffix ignored, so not full-block/page/book WER.','Geometry is converter supplied and does not independently prove source alignment.','Frozen source truth and all expected span IDs remain the accuracy denominator.']}
 pathlib.Path(out).write_text(json.dumps(value,indent=2)+'\n');pathlib.Path(out).chmod(0o444);return value

def run(artifact,output,freeze_file):
 pins=json.loads(pathlib.Path(freeze_file).read_text());assert pins['method_sha256']==sha(__file__);assert pins['frozen_input_sha256']==frozen_inputs()
 for h in pins['helpers'].values():assert all(sha(p)==v for p,v in h['sources'].items())
 samples=json.loads((BENCH/'samples.json').read_text())['samples'];manual=json.loads((BENCH/'manual-truth.json').read_text());expected=[s for s in samples if s['book'] in (561,566,564) and not(s['book']==564 and s['page_index0']>=60)];results=[];books={};a=pathlib.Path(artifact)
 for bid in (561,566,564):
  epub=a/f'book{bid}.epub'
  if not epub.exists():books[str(bid)]={'status':'ARTIFACT_ABSENT'};continue
  dom=extract_actual(epub);source_pins={s['source_pdf_sha256'] for s in samples if s['book']==bid};assert len(source_pins)==1 and dom['sidecar'].get('source_document_sha256') in source_pins,'Actual package source PDF differs from frozen source samples';report=a/f'book{bid}-report.json';equal=report.exists() and json.loads(report.read_text())==dom['sidecar'];rows=[measure_span(s,manual.get(s['id']) or s.get('ground_truth'),dom) for s in expected if s['book']==bid];results.extend(rows)
  marked=[]
  for page,record in dom['sidecar'].get('pages',{}).items():
   for b in record.get('blocks',[]):
    if b.get('role') in ROLES and b.get('marked'):marked.append({'page':int(page),'block':b['id'],'role':b['role'],'marked_word_indexes':b['marked'],'count':len(b['marked']),'actual_dom_block_present':(int(page),b['id']) in dom['blocks']})
  n=sum(r['reference_words'] for r in rows);errors=sum(r['errors'] for r in rows)
  books[str(bid)]={'epub_sha256':dom['epub_sha256'],'report_sha256':sha(report) if report.exists() else None,'report_equals_packaged_sidecar':equal,'source_document_sha256_matches_frozen_samples':True,'reference_spans':len(rows),'reference_words':n,'errors':errors,'wer':errors/n if n else None,'coverage':'NO_IN_SCOPE_TRUTH' if not any(s['book']==bid for s in expected) else 'COMPLETE' if len(rows)==sum(s['book']==bid for s in expected) else 'INCOMPLETE','actual_dom_counts':dom['counts'],'metadata_marked_word_count':sum(r['count'] for r in marked),'metadata_marked_block_rows':marked,'marked_rows_metadata_only':True,'duplicate_source_span_keys':[{'page':k[0],'block':k[1],'count':v} for k,v in dom['occurrences'].items() if v>1],'warnings':dom['warnings'],'excluded_dom':dom['excluded'],'reading_spine':dom['spine']}
 value={'method_version':VERSION,'method_sha256':sha(__file__),'freeze_record_sha256':sha(freeze_file),'artifact_root':str(a),'command':sys.argv,'expected_ids':[s['id'] for s in expected],'scored_ids':[r['id'] for r in results],'unscored_ids':sorted({s['id'] for s in expected}-{r['id'] for r in results}),'books':books,'spans':results,'frozen_input_sha256':pins['frozen_input_sha256'],'limits':pins['limits']+['Missing actual provenance span is empty candidate and all reference words become deletions if no other selected text exists.','DOM rates count source-owned visible text; unowned visible words are reported separately and excluded as unresolved/control text.','Sidecar marked-word rows are metadata diagnostics only, never scored text.','Body denominator excludes headings/captions; reading-flow denominator includes them.','Conditional CSS hiding is conservatively excluded; this is not a screenshot visibility grade.']};pathlib.Path(output).write_text(json.dumps(value,indent=2)+'\n');return value

if __name__=='__main__':
 p=argparse.ArgumentParser();sub=p.add_subparsers(dest='command',required=True);f=sub.add_parser('freeze');f.add_argument('--out',required=True);r=sub.add_parser('grade');r.add_argument('--artifact',required=True);r.add_argument('--out',required=True);r.add_argument('--freeze',required=True);args=p.parse_args();value=freeze(args.out) if args.command=='freeze' else run(args.artifact,args.out,args.freeze);print(json.dumps({'method_sha256':value['method_sha256'],'books':{b:{k:v for k,v in r.items() if k in ('coverage','reference_words','errors','wer','actual_dom_counts')} for b,r in value.get('books',{}).items()}},indent=2))

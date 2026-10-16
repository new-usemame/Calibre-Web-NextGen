"""Recompile layout authority at publication; never trust cached rendered HTML."""
import json
from .layout_ops import LayoutPlan, CompiledLayout
from .structural_ops import ContractError

def compile_pages(book, doc, plans, sources, raws, available, occupied=()):
 compiled={};seen=set(occupied)
 for plan in plans:
  if type(plan) is not LayoutPlan:raise ContractError('validated layout plan required')
  pno=plan.prepared.page
  if pno in seen or pno not in available:raise ContractError('duplicate or absent layout page')
  seen.add(pno)
  if pno not in sources or pno not in raws:raise ContractError('current layout source and raw page required')
  previous=json.loads(plan.prepared.contract_json).get('previous')
  context={}
  if previous:
   if pno-1 not in sources or pno-1 not in raws:raise ContractError('current previous layout source required')
   context=dict(previous_source=sources[pno-1],previous_raw=raws[pno-1])
  compiled[pno]=plan.compile(book,doc,source_page=sources[pno],raw_page=raws[pno],**context)
 return compiled

def report(compiled):
 """Audit compiler results, never model proposals or uncompiled plans.

 The caller supplies the outputs of compile_pages; publication and reporting
 both recompile live source before reaching this serialization-only boundary.
 """
 pages=[];count=0
 for pno,c in sorted(compiled.items()):
  if type(c) is not CompiledLayout or c.page!=pno:
   raise ContractError('compiled layout page required for audit')
  answer=json.loads(c.answer_json)
  row=dict(page=pno,snapshot=c.snapshot_id,gates=json.loads(c.word_gates_json),
           groups=json.loads(c.groups_json),joins=answer['joins'])
  bindings=answer.get('note_bindings',[])
  if bindings:
   row['note_bindings']=bindings
   row['note_bindings_applied']=len(bindings)
   count+=len(bindings)
  pages.append(row)
 result=dict(applied_pages=sorted(compiled),pages=pages)
 if count:result['note_bindings_applied']=count
 return result


def note_report(result, document=None):
 """Revalidate only selected binding-bearing plans for a pre-publication report.

 No document is needed for legacy/no-binding reports. A preview, stored count,
 or proposed answer cannot establish current applied note authority.
 """
 selected=[];seen=set()
 for plan in getattr(result,'layout_plans',()) or ():
  if type(plan) is not LayoutPlan:
   raise ContractError('validated layout plan required for note report')
  pno=plan.prepared.page
  if pno in seen:raise ContractError('duplicate layout page in note report')
  seen.add(pno)
  try:
   answer=json.loads(plan.answer_json)
   bindings=answer.get('note_bindings',[])
   if type(bindings) is not list:raise ValueError('note bindings must be an array')
  except (ValueError,TypeError,AttributeError) as exc:
   raise ContractError('invalid layout answer in note report') from exc
  if bindings:selected.append(plan)
 if not selected:return {}
 if document is None:
  raise ContractError('current document required for applied note report')
 compiled=compile_pages(result.book,document,selected,getattr(result,'source_pages',{}),
     {r.pno:r for r in result.raw_pages},result.page_html)
 return report(compiled)


def boundaries(book,doc,plans,sources,raws):
 from .layout_ops import compile_boundary
 by_page={plan.prepared.page:plan for plan in plans};result={}
 for pno,right in by_page.items():
  if pno-1 not in by_page or not json.loads(right.answer_json).get('continuation'):continue
  left=by_page[pno-1]
  context={}
  if json.loads(left.prepared.contract_json).get('previous'):
   context=dict(left_previous_source=sources[pno-2],left_previous_raw=raws[pno-2])
  result[pno]=compile_boundary(left,right,book,doc,left_source=sources[pno-1],right_source=sources[pno],
    left_raw=raws[pno-1],right_raw=raws[pno],**context)
 return result


def figure_failures(book,doc,plans,sources,raws,*,recovery=None,should_stop=None):
 """Probe actual protected figure crops; archive writes remain a publication gate."""
 from .native_ipc import NativeDocument
 if isinstance(doc,NativeDocument):
  return doc.layout_figure_failures(book,plans)
 from types import SimpleNamespace
 from . import build_epub,transcript_regions
 compiled=compile_pages(book,doc,plans,sources,raws,sources)
 html={p:c.page_html for p,c in compiled.items()}
 required=transcript_regions.required_images(book,html,doc)
 class Discard:
  def image(self,src,data):pass
 failed={}
 for pno,fragment in html.items():
  build_epub._check_cancelled(should_stop)
  refs={src for src in build_epub._IMG_SRC.findall(fragment) if src.startswith('images/fig_p')}
  if not refs:continue
  chapters=[SimpleNamespace(blocks=['<img src="%s"/>'%src for src in sorted(refs)])]
  missing,blank=build_epub._figure_images(chapters,doc,book,Discard(),
   figure_transform=recovery.figure_rect if recovery else None,
   required_source_regions=required,
   runtime_progress=lambda event:build_epub._check_cancelled(should_stop))
  losses=set(missing+blank)
  if losses.intersection(required):
   raise ContractError('required source region could not be packaged')
  if losses:failed[pno]=sorted(losses)
 return failed

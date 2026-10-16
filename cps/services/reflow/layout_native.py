"""Child-owned layout source authority minted only by native preparation."""
import hashlib
from . import native_codec, enriched_source, source_inventory

def book_digest(book):
 return hashlib.sha256(native_codec.dumps(book)).hexdigest()

class SourceAuthority:
 def __init__(self,result):
  self.result=result
  self.digest=book_digest(result.book)
  self.raws={r.pno:r for r in result.raw_pages}
  self.reading_sources={}
 def source(self,pno,doc=None,old=None):
  if pno not in self.raws:raise ValueError('page not in native preparation')
  result=self.result
  source=(enriched_source.prepare_recovery_page(result.book,pno,result.recovery) if result.recovery else
          enriched_source.prepare_source_page(result.book,pno,{'layer':'native'}))
  if old is not None and old.report().get('source_readings'):
   from . import source_readings
   if doc is None:raise ValueError('reading replay needs current document')
   source=source_readings.replay(result.book,doc,source,self.raws[pno],old)
  return source
 def prepare(self,doc,args):
  from . import layout_ops
  if set(args)-{'reading_source','previous_reading_source'}!={'page','book_digest','source_identity','raw_digest','previous_identity','previous_raw_digest'}:
   raise ValueError('layout source fields')
  pno=args['page']
  if type(pno) is not int or args['book_digest']!=self.digest:raise ValueError('layout book differs from native preparation')
  source=self.source(pno,doc,args.get('reading_source'));raw=self.raws[pno];context={}
  if source.identity!=args['source_identity'] or source_inventory.digest(raw)!=args['raw_digest']:
   raise ValueError('layout source differs from native preparation')
  if args['previous_identity'] is not None:
   previous=self.source(pno-1,doc,args.get('previous_reading_source'));prior_raw=self.raws[pno-1]
   if previous.identity!=args['previous_identity'] or source_inventory.digest(prior_raw)!=args['previous_raw_digest']:
    raise ValueError('layout previous source differs from native preparation')
   context=dict(previous_source=previous,previous_raw=prior_raw)
  elif args['previous_raw_digest'] is not None:raise ValueError('incomplete previous source')
  prepared=layout_ops.prepare(self.result.book,doc,pno,source,raw,**context)
  if source.report().get('source_readings'):self.reading_sources[pno]=source
  else:self.reading_sources.pop(pno,None)
  return prepared
 def publication(self,book,supplied,plans,output_pages=None,doc=None):
  if book_digest(book)!=self.digest:raise ValueError('publication book differs from native preparation')
  wanted=set(book.pages if output_pages is None else output_pages)|set(supplied or {})|{p.prepared.page for p in plans}
  if not wanted.issubset(book.pages):raise ValueError('unknown native output page')
  for plan in plans:
   import json
   if json.loads(plan.prepared.contract_json).get('previous'):wanted.add(plan.prepared.page-1)
  current={pno:self.source(pno,doc,(supplied or {}).get(pno)) for pno in wanted}
  for pno,old in (supplied or {}).items():
   if current[pno].identity!=old.identity:raise ValueError('publication source differs from native preparation')
  return self.result.book,current,self.raws

 def figure_failures(self,doc,args,should_stop=None):
  from . import layout_build
  if set(args)!={'book_digest','plans'} or args['book_digest']!=self.digest:
   raise ValueError('layout figure source differs from native preparation')
  plans=args['plans']
  book,sources,raws=self.publication(self.result.book,{p:self.reading_sources[p] for p in self.reading_sources if any(plan.prepared.page==p for plan in plans)},plans,output_pages=[],doc=doc)
  return layout_build.figure_failures(book,doc,plans,sources,raws,
   recovery=self.result.recovery,should_stop=should_stop)

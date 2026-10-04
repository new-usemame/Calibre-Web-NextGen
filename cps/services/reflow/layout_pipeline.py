"""AI owns page structure; checked canonical output is the per-page fallback."""
import json
from collections import Counter
from dataclasses import replace
from types import SimpleNamespace

from . import (pipeline, extract, model, shared_budget, layout_ops as ops,
               layout_requests as requests, layout_ordering as ordering,
               layout_lexical as lexical, layout_emphasis as emphasis)
from .enriched_source import prepare_recovery_page, prepare_source_page
from .operation_cache import OperationCache, PriorRequestPending
from .structural_pipeline import _stage, EstimateStale
from .layout_model import (LayoutStageClient, PROFILES, ROUTE_VERSION, CHALLENGER_SELECTION, DISABLED_SELECTION, QWEN_SELECTION, LUNA_STANDARD_SELECTION, LUNA_REVIEW_SELECTION, GLM_SELECTION, LUNA_HIGH_SELECTION,
                           profile_selection as select_profiles)

SOURCE_REVISION = '11.0-layout-1'
QUALITY_RELEASED = True


class LayoutClient:
    def __init__(self, api_key, enabled=QUALITY_RELEASED, session=None, *, profile_selection=None):
        from . import layout_luna_high
        self.enabled = bool(enabled)
        self.profile_selection = select_profiles(profile_selection)
        if self.profile_selection and self.profile_selection not in (dict(CHALLENGER_SELECTION), dict(DISABLED_SELECTION), dict(QWEN_SELECTION), dict(LUNA_STANDARD_SELECTION), dict(LUNA_REVIEW_SELECTION), dict(GLM_SELECTION), dict(LUNA_HIGH_SELECTION)):
            raise ValueError('layout challenger requires the complete approved proposer/reviewer pair')
        self.stages = {}
        for stage in PROFILES:
            if stage in self.profile_selection:
                self.stages[stage] = LayoutStageClient(api_key, stage,
                    profile_id=self.profile_selection[stage], session=session,
                    review_policy=(layout_luna_high.REVIEW_POLICY if self.profile_selection == dict(LUNA_HIGH_SELECTION) else
                                   'r8' if self.profile_selection == dict(GLM_SELECTION) else None) if stage == 'reviewer' else None)
                continue
            profile = PROFILES[stage]
            if stage in ('reviewer','ordering'):
                profile = replace(profile, spec=PROFILES['proposer'].spec, reasoning_effort=PROFILES['proposer'].reasoning_effort)
            self.stages[stage] = LayoutStageClient(api_key, stage, profile=profile, session=session)

    @property
    def configured(self):
        return all(c.configured for c in self.stages.values())

    def describe(self):
        result = dict(model='Luna layout and review + Opus lexical choices', tier='source_verified',
            configured=self.configured, dry_run=not self.enabled or not self.configured,
            prompt_version=requests.PROMPT_VERSION, route_version=ROUTE_VERSION,
            quality_released=self.enabled)
        if self.profile_selection:
            result.update(model=' + '.join(c.model_id for c in self.stages.values()),
                profile_selection=dict(self.profile_selection),
                route_versions={s:c.route_version for s,c in self.stages.items()})
        return result


def run_layout(doc, client=None, ledger=None, cache=None, page_numbers=None,
               sample_count=None, progress=None, should_stop=None, recovery_opts=None,
               prepared_result=None, prepared_observer=None, measure_eligibility=True,
               sample_context=False, request_observer=None, layout_hint_packets=None):
    if client is not None and client.enabled and client.configured:
        from .ledger import Ledger
        if not isinstance(cache,(pipeline.PageCache,OperationCache)) or not isinstance(ledger,Ledger):
            raise ops.ContractError('layout dispatch requires durable cache and ledger')
    bounded=bool(sample_context) and sample_count is not None
    if bounded and prepared_observer:
        raise ops.ContractError('consented review requires complete source context')
    search_end,context_end=pipeline.sample_context(len(doc),sample_count) if bounded else (len(doc),len(doc))
    result=prepared_result or pipeline.run(doc,client=None,progress=progress,
        page_numbers=list(range(context_end)) if bounded else None,
        should_stop=should_stop,recovery_opts=recovery_opts)
    if result.fingerprint!=extract.document_fingerprint(doc) or set(result.book.pages)!=set(range(context_end)):
        raise ops.ContractError('complete current source context is required')
    selected=sorted(set(page_numbers)) if page_numbers is not None else sorted(result.book.pages)
    if sample_count is not None:
        start=pipeline.first_body_page(result.book,before=search_end)
        selected=list(range(start,min(context_end,start+int(sample_count))))
    if any(p not in result.book.pages for p in selected):
        raise ValueError('unknown output page')
    result.pdf_pages=len(doc)
    result.page_html={p:result.page_html[p] for p in selected}
    retained_sources=dict(getattr(result,'source_pages',{}))
    result.source_pages={}; result.operation_plans=[]; result.layout_plans=[]; result.stage_records=[]
    result.outcomes={p:pipeline.PageOutcome(pno=p) for p in selected}
    result.routed=[]
    raw={r.pno:r for r in (result.raw_pages or (result.recovery.pages if result.recovery else []))}
    sources={}; errors={}; prepared={}; proposed={}; approved={}; states={}; styles={}
    cache=cache if isinstance(cache,OperationCache) else OperationCache(cache.directory) if cache else None
    halted='not_configured' if client is None or not client.configured else 'quality_gate' if not client.enabled else None
    def cancelled():
        if should_stop and should_stop():
            raise model.AttemptCancelled('cancelled during layout review')
    def source(p):
        if p not in sources:
            sources[p]=(prepare_recovery_page(result.book,p,result.recovery) if result.recovery else
                prepare_source_page(result.book,p,{'layer':'native'}))
            prior=retained_sources.get(p)
            if prior is not None and prior.report().get('source_readings'):
                from .source_readings import replay
                sources[p]=replay(result.book,doc,sources[p],raw[p],prior)
        return sources[p]
    for p in selected:
        cancelled()
        try:
            s=source(p); result.source_pages[p]=s; result.page_html[p]=s.html
            result.outcomes[p].uncertain=s.report()['uncertain'];result.outcomes[p].marked=s.report()['marked']
            if not measure_eligibility:
                states[p]=dict(status='unreviewed',reason='not_requested');continue
            value=ops.prepare(result.book,doc,p,s,raw.get(p),
                previous_source=source(p-1) if p else None,previous_raw=raw.get(p-1) if p else None)
            if not value.coverage['production_ready']:
                states[p]=dict(status='unsupported',reason=value.coverage['unsupported_reasons']);continue
            prepared[p]=value;result.routed.append(p)
            if prepared_observer:
                prepared_observer(result.book,value,s)
        except EstimateStale:
            halted='estimate_stale'
        except ops.ContractError as exc:
            errors[p]=str(exc);states[p]=dict(status='unsupported',reason=str(exc))
    # Every output page needs genuine source authority before any layout publication.
    if set(result.source_pages)!=set(selected):
        halted='source_unavailable'
    def dispatch(stage,request,p,raster=None,optional_record=None):
        nonlocal halted
        if halted:
            return None
        try:
            cancelled()
            if request_observer:
                wire=client.stages[stage].prepare_request(request,raster)
                request_observer(stage,wire,p)
            return _stage(client,stage,SimpleNamespace(raster=raster),request,ledger,cache,p,
                          result.stage_records,should_stop)
        except EstimateStale: halted='estimate_stale'
        except shared_budget.BudgetError as exc: halted=exc.code
        except model.CapExceeded: halted='cost_cap'
        except model.AttemptCancelled: halted='cancelled'
        except model.UncertainBilling: halted='billing_uncertain'
        except PriorRequestPending: halted='prior_request_pending'
        except model.ModelError as exc:
            if getattr(exc,'stop_dispatch',False):halted=exc.reason_code
            if optional_record is not None:
                optional_record.update(status='rejected',reason=str(exc),reason_code=getattr(exc,'reason_code',None))
            else:
                states[p]=dict(status='rejected',reason=str(exc),reason_code=getattr(exc,'reason_code',None))
        return None
    def compile(plan):
        p=plan.prepared.page
        return plan.compile(result.book,doc,source_page=sources[p],raw_page=raw[p],
            previous_source=sources.get(p-1),previous_raw=raw.get(p-1) if p else None)
    hints = dict(layout_hint_packets or {})
    if hints:
        from . import layout_hints
        ops._need(set(hints) <= set(prepared), 'hint pages require current eligible source')
        for p,hint in hints.items():
            layout_hints.validate(prepared[p],hint)
    for i,(p,value) in enumerate(prepared.items()):
        cancelled()
        if progress:progress(pipeline.Progress('model',page=i+1,pages=len(prepared),message='Checking page layout'))
        try:
            request=requests.proposal(value, proposed.get(p-1), hints=hints.get(p),
                profile_selection=client.profile_selection if client is not None else None)
            answer=dispatch('proposer',request,p,value.raster)
            if answer is None:
                states.setdefault(p,dict(status='unreviewed',reason=halted or 'proposal_rejected'));continue
            from . import layout_ranges as construction
            ops._need(type(answer) is dict and answer.get('contract') == construction.VERSION,
                      'live proposal requires current constructive contract')
            structural,style=emphasis.split(value,answer)
            styles[p]=dict(page=p,**style)
            plan=value.accept(result.book,doc,structural,source_page=sources[p],raw_page=raw[p],
                previous_source=sources.get(p-1),previous_raw=raw.get(p-1) if p else None)
            raw_construction = answer
            plan = replace(plan, construction_json=ops._json(raw_construction))
            compile(plan);proposed[p]=plan
        except (ops.ContractError,ValueError) as exc:
            states[p]=dict(status='rejected',reason=str(exc))
    for p,plan in proposed.items():
        cancelled()
        try:
            previous=proposed.get(p-1)
            request=requests.review(plan.prepared,plan,previous,
                profile_selection=client.profile_selection if client is not None else None)
            answer=dispatch('reviewer',request,p,plan.prepared.raster)
            if answer is None:
                states.setdefault(p,dict(status='unreviewed',reason=halted or 'review_rejected'));continue
            verdict=requests.validate_review(plan.prepared,plan,answer,previous)
            if verdict.accepted_plan is None:
                states[p]=dict(status='rejected',reason=list(verdict.problems));continue
            approved[p]=verdict.accepted_plan
        except (ops.ContractError,ValueError) as exc:
            states[p]=dict(status='rejected',reason=str(exc))
    def batch(module,stage):
        nonlocal approved
        plans=list(approved.values());rows=module.candidates(plans)
        if not rows:return
        responses=[];failed=False
        # Recursively split by actual text admission before any dispatch.
        batches=[rows]
        while batches:
            part=batches.pop(0)
            view=([dict(id=r['id'],groups=r['groups']) for r in part] if stage=='ordering' else
                  [{k:r[k] for k in ('id','left_text','right_text','before','after')} for r in part])
            request=requests.envelope(stage,ops.atoms.digest(part),view,module.PROMPT,module.schema(part))
            try:
                if client is not None:client.stages[stage].prepare_request(request)
            except ValueError:
                if len(part)>1:
                    mid=len(part)//2;batches[:0]=[part[:mid],part[mid:]];continue
                failed=True;break
            response=dispatch(stage,request,part[0].get('page',part[0].get('right_page')))
            if response is None:failed=True;break
            try:module.accept(part,response)
            except ops.ContractError:failed=True;break
            responses.extend(response['decisions'])
        if failed:
            refused=({r['page'] for r in rows} if stage=='ordering' else
                     {r[k] for r in rows for k in ('left_page','right_page')})
            approved={p:v for p,v in approved.items() if p not in refused}
        else:
            apply=module.approved if stage=='ordering' else module.apply
            try:
                plans,refused=apply(plans,dict(decisions=responses),ops.atoms.digest(rows))
                approved={p.prepared.page:p for p in plans}
            except (ops.ContractError,ValueError):
                refused=({r['page'] for r in rows} if stage=='ordering' else
                         {r[k] for r in rows for k in ('left_page','right_page')})
                approved={p:v for p,v in approved.items() if p not in refused}
        for p in refused:states[p]=dict(status='rejected',reason=stage+'_not_approved')
    batch(ordering,'ordering');batch(lexical,'lexical')
    for p,plan in list(approved.items()):
        try:
            answer=json.loads(plan.answer_json)
            if answer.get('continuation'):
                if p-1 not in approved:
                    answer.update(continuation=False,boundary_join=None)
                    plan=replace(plan,answer_json=ops._json(answer));approved[p]=plan
                else:
                    ops.compile_boundary(approved[p-1],plan,result.book,doc,
                        left_source=sources[p-1],right_source=sources[p],left_raw=raw[p-1],right_raw=raw[p],
                        left_previous_source=sources.get(p-2),left_previous_raw=raw.get(p-2) if p>1 else None)
            compile(plan)
        except ops.ContractError as exc:
            del approved[p];states[p]=dict(status='rejected',reason=str(exc))
    from . import layout_build
    failures=layout_build.figure_failures(result.book,doc,list(approved.values()),sources,raw,
        recovery=result.recovery,should_stop=should_stop)
    for p,resources in failures.items():
        del approved[p]
        states[p]=dict(status='rejected',reason='protected_figure_not_publishable',resources=resources)
    # A locally valid page cannot strand a proposed continuation at a mixed
    # model/fallback seam. Original proposals retain that dependency even when
    # review or lexical checks cleared the boundary in the accepted answer.
    # This only withdraws model authority; it never authorizes a model join.
    dependencies=[(p-1,p) for p,plan in proposed.items()
                  if p-1 in selected and json.loads(plan.answer_json).get('continuation')]
    while True:
        withdrawn={p for left,right in dependencies
                   if (left in approved)!=(right in approved)
                   for p in (left,right) if p in approved}
        if not withdrawn:
            break
        for p in withdrawn:
            del approved[p]
            states[p]=dict(status='rejected',reason='continuation_neighbor_fallback')
    # Optional styling cannot invalidate approved structure, nor bypass its own
    # source/range and independent semantic gates. Review only complete surviving
    # components, after ordering and lexical choices have settled their endpoints.
    for p,style in styles.items():
        if style['status'] != 'pending_review':
            continue
        if p not in approved:
            style.update(status='withheld',reason='structure_not_approved')
            continue
        try:
            plan=approved[p]
            request=emphasis.review(plan,style['requested'])
            style['review_snapshot']=request['source_identity']['emphasis_snapshot']
            response=dispatch('reviewer',request,p,plan.prepared.raster,optional_record=style)
            if response is None:
                style.update(status='rejected',reason=halted or style.get('reason') or 'emphasis_review_unavailable')
                continue
            marked=emphasis.accept(plan,style['requested'],response)
            style['review_problems']=response['problems']
            if marked is None:
                style.update(status='rejected',reason='unsupported_change')
            else:
                compile(marked)
                approved[p]=marked
                style.update(status='approved',review_snapshot=response['snapshot'])
        except (ops.ContractError,ValueError) as exc:
            style.update(status='rejected',reason=str(exc))
    result.layout_plans=list(approved.values())
    result.source_pages.update(sources)
    result.preview_html=dict(result.page_html)
    for p,plan in approved.items():
        result.preview_html[p]=compile(plan).page_html
        states[p]=dict(status='approved',operations=len(json.loads(plan.answer_json)['groups']))
        result.outcomes[p].source='model';result.outcomes[p].gate='PASS'
    for p in selected:
        states.setdefault(p,dict(status='unreviewed',reason=halted or 'not_approved'))
        records=[r for r in result.stage_records if r['page']==p]
        result.outcomes[p].cost_usd=sum(r.get('cost_usd') or 0 for r in records)
        result.outcomes[p].cached=bool(records) and all(r['cached'] for r in records)
    counts=Counter(r['status'] for r in states.values())
    result.structural=dict(total_pages=len(selected),eligible=len(prepared),approved_pages=len(approved),
        approved_operations=sum(len(json.loads(p.answer_json)['groups']) for p in approved.values()),
        unsupported=counts['unsupported'],limited=0,no_choices=0,unreviewed=counts['unreviewed'],
        rejected=counts['rejected'],proposed_pages=len(proposed),
        cached_stages=sum(r['cached'] for r in result.stage_records),answered_stages=len(result.stage_records),
        pages=[dict(page=p,**states[p]) for p in selected],source_context_pages=context_end,
        source_pages=len(doc),context='sample' if bounded else 'complete',
        eligibility_measured=measure_eligibility,route_version=ROUTE_VERSION,source_revision=SOURCE_REVISION)
    result.structural['emphasis']=list(styles.values())
    if hints:
        result.structural['layout_hint_experiment'] = {p:layout_hints.identity(prepared[p],hint) for p,hint in hints.items()}
    attempts=[e for e in ledger.entries('reservation') if e.get('event')=='pending'] if ledger else []
    result.structural.update(attempted_stages=len(attempts),attempted_pages=len({e['page_label'] for e in attempts}),
        requested_models=dict(Counter(e['model'] for e in attempts if e.get('model'))),
        proposed_operations=sum(len(json.loads(p.answer_json)['groups']) for p in proposed.values()),
        proposer_abstained=0,verifier_abstained=0,layout_primary=True)
    result.stopped=(halted or ('model_rejections' if counts['rejected'] else None)) if measure_eligibility else None
    result.pages_done=len(approved);result.gate_failures=counts['rejected']
    result.spend_usd=ledger.spent() if ledger else sum(r.get('cost_usd') or 0 for r in result.stage_records)
    result.pending_usd=ledger.pending_usd() if ledger else 0
    result.reused=result.structural['cached_stages']
    if ledger:ledger.record(dict(kind='structural_summary',summary=result.structural))
    return result

"""Two-stage source operations, with complete deterministic output on every stop."""
from collections import Counter
from dataclasses import asdict

from . import pipeline, prompts, structural_ops as ops, model, extract
from .enriched_source import prepare_recovery_page, prepare_source_page
from .operation_cache import OperationCache, PriorRequestPending
from .typed_model import TypedStageClient, TypedStageRejected, QUALITY_RELEASED, SOURCE_REVISION, ROUTE_VERSION


class TwoStageClient:
    def __init__(self, api_key, enabled=QUALITY_RELEASED, session=None):
        self.enabled = bool(enabled)
        self.stages = {stage: TypedStageClient(api_key, stage, session=session, max_retries=1)
                       for stage in ('proposer','verifier')}

    @property
    def configured(self):
        return all(c.configured for c in self.stages.values())

    def describe(self):
        return {'model':'Luna proposal + Terra approval', 'tier':'source_verified',
                'configured':self.configured,'dry_run':not self.enabled or not self.configured,
                'prompt_version':prompts.OPERATION_PROMPT_VERSION,
                'approval_prompt_version':prompts.VERIFICATION_PROMPT_VERSION,
                'route_version':ROUTE_VERSION,'quality_released':self.enabled}


def _stage(client, stage, prepared, request, ledger, cache, pno, records, should_stop):
    wire = client.stages[stage].prepare_request(request, prepared.raster)
    token, saved = cache.claim(wire.sha256) if cache else (None, None)
    if saved is not None:
        records.append({'page':pno,'stage':stage,'cached':True,'cost_usd':0,
                        'status':saved['state'],'request_sha256':wire.sha256})
        if saved['state']=='rejected':
            raise TypedStageRejected('cached stage response was rejected', reason_code=saved.get('failure_code','response_rejected'))
        return saved['response']
    try:
        answer = client.stages[stage].call(wire, ledger=ledger, page_label=str(pno), should_stop=should_stop)
        if cache:cache.finish(wire.sha256,token,response=answer.response)
        records.append({'page':pno,'stage':stage,'cached':False,'cost_usd':answer.cost_usd,
                        'status':'answered','request_sha256':wire.sha256,'attempt':answer.attempt})
        if ledger:
            # No cost_usd: the durable reconcile is the debit. Diagnostics cannot
            # suppress or duplicate it, and cached users never inherit this cost.
            ledger.record({'kind':'typed_stage','page':pno,'stage':stage,'attempt':answer.attempt,
                           'request_sha256':wire.sha256,'status':'answered'})
        return answer.response
    except (model.CapExceeded, model.AttemptCancelled):
        if cache:cache.release_unsent(wire.sha256,token)
        raise
    except model.UncertainBilling:
        # Keep the durable claim, even across another job/owner. No prior user's
        # job identity, report path or cost is returned to the new caller.
        raise
    except model.UnusableAnswer as exc:
        records.append({'page':pno,'stage':stage,'cached':False,'cost_usd':exc.cost_usd,
                        'status':'rejected','request_sha256':wire.sha256,'attempt':exc.attempt})
        if cache:cache.finish(wire.sha256,token,rejected=True,failure_code=getattr(exc,'reason_code','response_rejected'))
        raise
    except model.ModelError:
        # Common transport only raises a bare ModelError for proven unsent or
        # pre-generation rejection; possibly-billed failures use specific types.
        if cache:cache.release_unsent(wire.sha256,token)
        raise


def run_structural(doc, client=None, ledger=None, cache=None, page_numbers=None,
                   sample_count=None, progress=None, should_stop=None,
                   recovery_opts=None, prepared_result=None):
    """Read full source context before selecting output/paid pages.

    ``prepared_result`` is an explicit local-rig reuse seam, never an API pickle
    input. It must match the current PDF and contain its complete Book context.
    """
    result = prepared_result or pipeline.run(doc, client=None, progress=progress,
                    should_stop=should_stop, recovery_opts=recovery_opts)
    if result.fingerprint != extract.document_fingerprint(doc) or set(result.book.pages) != set(range(len(doc))):
        raise ops.ContractError('complete current source context is required')
    selected = sorted(set(page_numbers)) if page_numbers is not None else sorted(result.book.pages)
    if sample_count is not None:
        start = pipeline.first_body_page(result.book)
        selected = list(range(start,min(len(doc),start+int(sample_count))))
    if any(p not in result.book.pages for p in selected):raise ValueError('unknown output page')
    result.page_html={p:result.page_html[p] for p in selected}
    result.source_pages={};result.operation_plans=[];result.stage_records=[]
    result.outcomes={p:pipeline.PageOutcome(pno=p) for p in selected}
    result.routed=[];counts=Counter(total_pages=len(selected));states={};canonical_errors={}
    for pno in selected:
        if should_stop and should_stop():raise model.AttemptCancelled('cancelled during source preparation')
        try:
            source = (prepare_recovery_page(result.book,pno,result.recovery) if result.recovery else
                      prepare_source_page(result.book,pno,{'layer':'native'}))
            result.source_pages[pno]=source;result.page_html[pno]=source.html
            outcome=result.outcomes[pno];outcome.uncertain=source.report()['uncertain'];outcome.marked=source.report()['marked']
        except ops.ContractError as exc:canonical_errors[pno]=str(exc)
    stage_cache=OperationCache(cache.directory) if cache else None
    halted = 'not_configured' if client is None or not client.configured else 'quality_gate' if not client.enabled else None
    for index,pno in enumerate(selected):
        outcome=result.outcomes[pno];reviewed=False;source=result.source_pages.get(pno)
        try:
            if pno in canonical_errors:raise ops.ContractError(canonical_errors[pno])
            import json
            p=ops.prepare(result.book,doc,pno,SOURCE_REVISION,json.loads(source.provenance_json),source_page=source)
        except ops.ContractError as exc:
            counts['unsupported']+=1;states[pno]={'status':'unsupported','reason':str(exc)};continue
        coverage=p.model_view()['coverage']
        if any(coverage.get(k) for k in ('omitted_element_ids','omitted_inventory_records','candidate_limit_applied')):
            counts['limited']+=1;states[pno]={'status':'limited','coverage':coverage};continue
        if not p.candidates():
            counts['no_choices']+=1;states[pno]={'status':'no_choices'};continue
        counts['eligible']+=1;result.routed.append(pno)
        if should_stop and should_stop():halted='cancelled'
        if halted:
            counts['unreviewed']+=1;states[pno]={'status':'unreviewed','reason':halted};continue
        try:
            response=_stage(client,'proposer',p,prompts.operation_request(p.model_view()),
                            ledger,stage_cache,pno,result.stage_records,should_stop)
            proposal=p.accept(result.book,doc,response,source_page=source)
            if not proposal.selected:
                counts['proposer_abstained']+=1;states[pno]={'status':'proposer_abstained'};reviewed=True
            else:
                counts['proposed_pages']+=1;counts['proposed_operations']+=len(proposal.selected)
                v=ops.prepare_verification(result.book,doc,proposal,source_page=source)
                response=_stage(client,'verifier',p,prompts.verification_request(v.model_view()),
                                ledger,stage_cache,pno,result.stage_records,should_stop)
                approved=v.accept(result.book,doc,response,source_page=source)
                reviewed=True
                if approved.selected:
                    result.operation_plans.append(approved);outcome.source='model'
                    counts['approved_pages']+=1;counts['approved_operations']+=len(approved.selected)
                    by_id={c['candidate_id']:c for c in p.candidates()}
                    for cid in approved.selected:counts['approved_'+by_id[cid]['kind']]+=1
                    states[pno]={'status':'approved','operations':len(approved.selected)}
                else:counts['verifier_abstained']+=1;states[pno]={'status':'verifier_abstained'}
            outcome.gate='PASS'
        except model.CapExceeded:halted='cost_cap'
        except model.AttemptCancelled:halted='cancelled'
        except model.UncertainBilling:halted='billing_uncertain'
        except PriorRequestPending:halted='prior_request_pending'
        except (ops.ContractError,model.ModelError) as exc:
            counts['rejected']+=1;outcome.gate='FAIL';outcome.gate_reasons=[str(exc)]
            states[pno]={'status':'rejected','reason':str(exc)}
            if getattr(exc,'stop_dispatch',False):halted=exc.reason_code
        if not reviewed:
            counts['unreviewed']+=1
            states.setdefault(pno,{'status':'unreviewed','reason':halted or 'rejected'})
        outcome.cost_usd=sum(r.get('cost_usd') or 0 for r in result.stage_records if r['page']==pno)
        if ledger and ledger.would_exceed(0) and not halted:halted='cost_cap'
        if progress:
            progress(pipeline.Progress('review',page=index+1,pages=len(selected),
                spend_usd=ledger.spent() if ledger else 0,message='source review %d/%d'%(index+1,len(selected))))
    for name in ('eligible','limited','unsupported','no_choices','unreviewed','proposed_pages','proposed_operations',
                 'approved_pages','approved_operations','approved_heading','approved_quote','proposer_abstained',
                 'verifier_abstained','rejected'):counts.setdefault(name,0)
    counts['cached_stages']=sum(r['cached'] for r in result.stage_records)
    counts['answered_stages']=len(result.stage_records)
    result.structural=dict(counts,pages=[dict(page=p,**row) for p,row in states.items()],
                           route_version=ROUTE_VERSION,source_revision=SOURCE_REVISION)
    result.stopped=halted;result.pages_done=sum(o.gate=='PASS' for o in result.outcomes.values())
    result.spend_usd=ledger.spent() if ledger else sum(r['cost_usd'] for r in result.stage_records)
    result.pending_usd=ledger.pending_usd() if ledger else 0
    result.reused=counts['cached_stages']
    return result

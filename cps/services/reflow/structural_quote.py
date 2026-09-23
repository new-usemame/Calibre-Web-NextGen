"""No-network cost ceilings from the exact current two-stage request builders."""
import hashlib
import json

from . import prompts,structural_ops as ops,structural_pipeline,typed_model,pipeline

VERSION='source-review-quote-1'


def maximum_legal_ids(candidates):
    """Maximum-cardinality nonoverlapping source intervals, independently per unit.

    Candidate IDs have equal serialized width. The verifier adds only those IDs
    and a fixed-width proposal digest to the complete immutable source payload,
    so this maximum bounds both input and response serialization for every legal
    proposal. Reject a future variable-width protocol rather than underquote it.
    """
    widths={len(json.dumps(c['candidate_id'],ensure_ascii=False).encode()) for c in candidates}
    if len(widths)>1:raise ValueError('candidate identity widths differ from quote contract')
    selected=[];ends={}
    for c in sorted(candidates,key=lambda c:(c['element_id'],c['source_range'][1],c['source_range'][0],c['candidate_id'])):
        start,end=c['source_range'];element=c['element_id']
        if start>=ends.get(element,-1):
            selected.append(c['candidate_id']);ends[element]=end
    return selected


def measure_page(book,doc,prepared,source_page=None):
    proposal=prepared.accept(book,doc,dict(protocol=ops.PROTOCOL,snapshot_id=prepared.snapshot_id,
        select=maximum_legal_ids(prepared.candidates())),source_page=source_page)
    verifier=ops.prepare_verification(book,doc,proposal,source_page=source_page)
    requests={
        'proposer':prompts.operation_request(prepared.model_view()),
        'verifier':prompts.verification_request(verifier.model_view()),
    }
    row=dict(page_index0=prepared.page,source_sha256=prepared.pdf_digest,snapshot_id=prepared.snapshot_id,
             candidates=len(prepared.candidates()),maximum_proposed_operations=len(proposal.selected))
    for stage,request in requests.items():
        wire=typed_model.TypedStageClient('',stage).prepare_request(request,prepared.raster)
        row.update({stage+'_bound_usd':wire.bound_usd,stage+'_request_sha256':wire.sha256,
                    stage+'_prompt_tokens_bound':wire.prompt_tokens_bound,
                    stage+'_response_token_bound':wire.response_token_bound})
    return row


def measure(doc,*,recovery_opts=None,prepared_result=None,progress=None,should_stop=None):
    """Prepare complete source context once; unsupported/limited pages buy nothing.

    No session, credential, route preflight, cache claim or billing ledger is used.
    A quote assumes a verifier request on every eligible page and no paid cache
    reuse. Dispatch still rechecks current route prices and reserves each stage.
    """
    pages=[]
    def observe(book,prepared,source_page):
        pages.append(measure_page(book,doc,prepared,source_page))
    result=structural_pipeline.run_structural(doc,client=None,recovery_opts=recovery_opts,
        prepared_result=prepared_result,progress=progress,should_stop=should_stop,prepared_observer=observe)
    counts=result.structural
    quote=dict(version=VERSION,source_sha256=result.fingerprint,
        source_revision=typed_model.SOURCE_REVISION,route_version=typed_model.ROUTE_VERSION,
        proposer_prompt=prompts.OPERATION_PROMPT_VERSION,verifier_prompt=prompts.VERIFICATION_PROMPT_VERSION,
        source_context_pages=len(result.book.pages),first_body_page=pipeline.first_body_page(result.book),
        eligible_pages=counts['eligible'],limited_pages=counts['limited'],unsupported_pages=counts['unsupported'],
        no_choice_pages=counts['no_choices'],pages=pages,coverage=[
            dict(row,status='eligible',reason='') if row['page'] in {p['page_index0'] for p in pages} else row
            for row in counts['pages']],
        kind='reservation_ceiling_not_expected_bill',assumes_verifier_on_every_eligible_page=True,
        assumes_no_paid_cache_hits=True,confirmed_usd=0,held_usd=0,
        proposer_bound_usd=sum(p['proposer_bound_usd'] for p in pages),
        verifier_bound_usd=sum(p['verifier_bound_usd'] for p in pages),
        models={stage:{'id':spec.model_id,'input_usd_per_million':spec.prompt_usd_per_mtok,
                       'output_usd_per_million':spec.completion_usd_per_mtok} for stage,spec in typed_model.STAGES.items()},
        provider='openai/flex',service_tier='flex',reasoning='medium',max_output_tokens=typed_model.MAX_OUTPUT_TOKENS)
    quote['full_bound_usd']=quote['proposer_bound_usd']+quote['verifier_bound_usd']
    quote['identity']=hashlib.sha256(json.dumps(quote,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    return quote


def consent_observer(quote,doc):
    """Re-measure current source-bound wire ceilings before any paid/cache stage.

    A different source, prompt, route or candidate snapshot stops subsequent
    review while the pipeline retains the complete deterministic conversion.
    Quotes are server-owned prepared data; their hash is integrity, not authority
    to supply source HTML or substitute an operation plan.
    """
    expected={p['page_index0']:p for p in quote.get('pages',[])}
    def observe(book,prepared,source_page):
        versions={'version':VERSION,'source_revision':typed_model.SOURCE_REVISION,
                  'route_version':typed_model.ROUTE_VERSION,
                  'proposer_prompt':prompts.OPERATION_PROMPT_VERSION,
                  'verifier_prompt':prompts.VERIFICATION_PROMPT_VERSION}
        if (any(quote.get(k)!=v for k,v in versions.items()) or
                prepared.pdf_digest!=quote.get('source_sha256') or
                expected.get(prepared.page)!=measure_page(book,doc,prepared,source_page)):
            raise structural_pipeline.EstimateStale('current source review differs from the prepared estimate')
    return observe

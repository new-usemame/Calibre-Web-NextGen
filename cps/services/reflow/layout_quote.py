"""No-network layout reservation ceilings; these are not expected provider bills.

Unknown future review/batch text is quoted at transport admission maxima. The
source observer and request-bound check are explicit hooks for future integration;
this module does not activate dispatch or confer source/consent authority.
"""
import hashlib
import json
from dataclasses import asdict

from . import (layout_glm, layout_luna_high, layout_model as transport, layout_pipeline, layout_requests,
               layout_ordering, layout_lexical, layout_emphasis, layout_allocation, layout_wire, pipeline)
from .structural_pipeline import EstimateStale

VERSION = 'layout-quote-8-grouping'
PROFILE_VERSION = 'layout-quote-12-grouping-profile'
QWEN_GROUPING_VERSION = 'layout-quote-12-qwen-grouping'


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _clients(profile_selection=None):
    # Uses the same deliberate standard-Luna overrides as the inactive pipeline.
    return layout_pipeline.LayoutClient('', profile_selection=profile_selection).stages


def _versions(profile_selection=None, hinted=False):
    from . import layout_construction,layout_domain,layout_ranges as layout_choices
    selection = transport.profile_selection(profile_selection)
    clients = _clients(selection)
    result = dict(construction_version=layout_choices.VERSION,
        range_required_task_sha256=_digest(layout_choices.REQUIRED_TASK),
        range_source_prompt_sha256=_digest(layout_choices.SOURCE),
        range_layout_prompt_sha256=_digest(layout_choices.LAYOUT),
        range_choice_prompt_sha256=_digest(layout_choices.CHOICES),
        range_review_prompt_sha256=_digest(layout_choices.REVIEW),
        domain_source_prompt_sha256=_digest(layout_domain.SOURCE),
        domain_layout_prompt_sha256=_digest(layout_domain.LAYOUT),
        domain_choice_prompt_sha256=_digest(layout_domain.CHOICES),
        domain_review_prompt_sha256=_digest(layout_domain.REVIEW),
        construction_prompt_sha256=_digest(layout_construction.PROMPT),
        boundary_review_prompt_sha256=_digest(layout_construction.REVIEW_PROMPT),
        version=PROFILE_VERSION if selection else VERSION, allocation_version=layout_allocation.VERSION, wire_version=layout_wire.VERSION, source_revision=layout_pipeline.SOURCE_REVISION,
        route_version=transport.PROFILE_ROUTE_VERSION if selection else transport.ROUTE_VERSION,
        proposer_prompt=layout_choices.PROPOSER_PROMPT_VERSION+'-proposer',
        verifier_prompt=layout_requests.PROMPT_VERSION+'-reviewer',
        emphasis_version=layout_emphasis.VERSION,
        previous_candidate_prompt_sha256=_digest(layout_requests.PREVIOUS_CANDIDATE_PROMPT),
        protocol=layout_requests.PROTOCOL,
        prompt_hashes={stage:_digest(prompt) for stage,prompt in {
            'proposer':layout_requests.PROMPT, 'reviewer':layout_requests.REVIEW_PROMPT,
            'ordering':layout_ordering.PROMPT, 'lexical':layout_lexical.PROMPT,
            'emphasis':layout_emphasis.PROMPT}.items()},
        profiles={stage:asdict(client.profile) for stage,client in clients.items()},
        reservation_rates={stage:list(client.reservation_rates) for stage,client in clients.items()},
        admission=dict(max_text_bytes=transport.MAX_TEXT_BYTES,
            max_image_pixels=transport.MAX_IMAGE_PIXELS,
            cache_write_multiplier=transport.CACHE_WRITE_MULTIPLIER))
    if hinted:
        from . import layout_hints
        result.update(hint_quote_version=layout_hints.QUOTE_VERSION,
            hint_projection_version=layout_hints.PROJECTION_VERSION,
            hint_prompt_sha256=_digest(layout_hints.PROMPT))
    if selection:
        result.update(profile_selection=selection,
            route_versions={stage:client.route_version for stage,client in clients.items()})
    if selection == dict(transport.QWEN_SELECTION):
        from . import layout_qwen
        result.update(version=QWEN_GROUPING_VERSION, route_version=layout_qwen.ROUTE_VERSION,
            meter_policy=layout_qwen.POLICY_VERSION,
            input_liability_usd_per_million={s:c.input_reservation_rate for s,c in clients.items()})
    if selection == dict(transport.LUNA_STANDARD_SELECTION):
        from . import layout_luna_standard
        result.update(version=layout_luna_standard.QUOTE_VERSION, route_version=layout_luna_standard.ROUTE_VERSION,
            meter_policy=layout_luna_standard.POLICY_VERSION,
            canonical_model=layout_luna_standard.CANONICAL,
            reasoning_budget_is_native_cap=False, max_attempts=1,
            input_liability_usd_per_million={s:c.input_reservation_rate for s,c in clients.items()})
    if selection == dict(transport.LUNA_REVIEW_SELECTION):
        from . import layout_luna_standard, layout_eligibility
        result.update(version=layout_luna_standard.PAIR_QUOTE_VERSION,
            route_version=layout_luna_standard.REVIEW_ROUTE_VERSION,
            allocation_versions={s:layout_allocation.version(c.profile_id) for s,c in clients.items()},
            source_policy=layout_eligibility.VERSION,
            proposer_prompt=layout_eligibility.PROMPT_VERSION+'-proposer',
            source_policy_prompt_sha256=_digest(layout_eligibility.PROMPT),
            meter_policy=layout_luna_standard.POLICY_VERSION,
            canonical_model=layout_luna_standard.CANONICAL,
            reasoning_budget_is_native_cap=False, max_attempts=1,
            input_liability_usd_per_million={s:c.input_reservation_rate for s,c in clients.items()})
    if layout_glm.selected(selection):
        from . import layout_eligibility
        result.update(version=layout_glm.QUOTE_VERSION, route_version=layout_glm.ROUTE_VERSION,
            proposer_prompt='p38', verifier_prompt=layout_glm.REVIEW_POLICY,
            allocation_versions={s:layout_allocation.version(c.profile_id,c.review_policy) for s,c in clients.items()},
            source_policy=layout_eligibility.VERSION, source_policy_prompt_sha256=_digest(layout_eligibility.PROMPT),
            join_policy=layout_glm.REVIEW_POLICY, join_prompt_sha256=_digest(layout_glm.JOIN),
            review_policy=layout_glm.REVIEW_POLICY, meter_policy=layout_glm.POLICY_VERSION,
            canonical_model=layout_glm.CANONICAL, reasoning_budget_is_native_cap=False, max_attempts=1,
            input_liability_usd_per_million={s:c.input_reservation_rate for s,c in clients.items()})
    if layout_luna_high.selected(selection):
        from . import layout_eligibility
        result.update(version=layout_luna_high.QUOTE_VERSION, route_version=layout_luna_high.ROUTE_VERSION,
            proposer_prompt=layout_luna_high.PROPOSER_PROMPT, verifier_prompt=layout_luna_high.REVIEW_POLICY,
            allocation_versions={s:layout_allocation.version(c.profile_id,c.review_policy) for s,c in clients.items()},
            source_policy=layout_eligibility.VERSION, source_policy_prompt_sha256=_digest(layout_eligibility.PROMPT),
            join_policy=layout_luna_high.REVIEW_POLICY, join_prompt_sha256=_digest(layout_luna_high.JOIN),
            review_policy=layout_luna_high.REVIEW_POLICY, meter_policy=layout_luna_high.POLICY_VERSION,
            control_version=layout_luna_high.CONTROL_VERSION, canonical_model=layout_luna_high.CANONICAL,
            reasoning_budget_is_native_cap=False, max_attempts=1,
            input_liability_usd_per_million={s:c.input_reservation_rate for s,c in clients.items()})
    return result


def _maximum(stage, client=None):
    """Measure maximal admitted text using the real serializer, then add pixels.

    No guessed future candidate serialization or fixed chat-overhead copy lives
    here. A transport change is reflected in this synthetic maximum wire.
    """
    client = client or _clients()[stage]
    schema = dict(type='object', properties={}, required=[], additionalProperties=False)
    request = layout_requests.envelope(stage, 'quote-limit', [], '', schema)
    wire = client.prepare_request(request)
    text_bytes = len(transport._encoded(wire.payload['messages'])) + len(transport._encoded(schema))
    request['messages'][0]['content'] = 'x' * (transport.MAX_TEXT_BYTES-text_bytes)
    wire = client.prepare_request(request)
    pixels = transport.MAX_IMAGE_PIXELS if client.profile.images else 0
    prompt_tokens = wire.prompt_tokens_bound + pixels
    if client.model_id == 'openai/gpt-6-luna':
        prompt_tokens = min(prompt_tokens, 922000)  # Same admitted input ceiling as transport.
    bound = client.reservation_bound(prompt_tokens, client.profile.completion_cap)
    return dict(bound_usd=bound, prompt_tokens_bound=prompt_tokens,
                response_token_bound=client.profile.completion_cap)


def _source_binding(prepared):
    return dict(page_index0=prepared.page, source_sha256=prepared.pdf_digest,
        source_identity=prepared.source_identity, snapshot_id=prepared.snapshot_id,
        prepared_sha256=_digest({k:v for k,v in asdict(prepared).items() if k!='raster'}),
        raster_sha256=hashlib.sha256(prepared.raster).hexdigest())


def _limited_binding(prepared, hints=None):
    row = _source_binding(prepared)
    if hints is not None:
        from . import layout_hints
        row['layout_advisory'] = layout_hints.identity(prepared,hints)
    return row

def measure_page(book, doc, prepared, source_page=None, *, profile_selection=None, hints=None):
    """Exact proposer wire and source identity; conservative later-stage ceilings."""
    clients = _clients(profile_selection)
    wire = clients['proposer'].prepare_request(layout_requests.proposal(prepared, hints=hints, profile_selection=profile_selection), prepared.raster)
    source = json.loads(prepared.contract_json)
    reviewer = _maximum('reviewer', clients['reviewer'])
    from . import _layout_emphasis
    emphasis_requests = int(bool(_layout_emphasis.eligible(source)))
    verifier_requests = 1 + emphasis_requests
    ordering = _maximum('ordering', clients['ordering'])
    lexical = _maximum('lexical', clients['lexical'])
    # Every current wrap-left atom occurs once, hence can own at most one row.
    # The incoming real page boundary contributes at most one additional row.
    lexical_rows = len(set(source.get('wrap_lefts', [])))
    if source.get('previous', {}).get('wrap_lefts') and source.get('wrap_rights'):
        lexical_rows += 1
    # Ordering emits at most one row/page; singleton requests bound any batching.
    ordering_rows = 1
    row = dict(_source_binding(prepared),
        candidates=len(source['atoms']), maximum_proposed_operations=len(source['atoms']),
        proposer_bound_usd=wire.bound_usd, proposer_request_sha256=wire.sha256,
        proposer_prompt_tokens_bound=wire.prompt_tokens_bound,
        proposer_response_token_bound=wire.response_token_bound,
        verifier_bound_usd=verifier_requests*reviewer['bound_usd'], verifier_request_sha256=None,
        verifier_request_bound_usd=reviewer['bound_usd'], verifier_max_requests=verifier_requests,
        emphasis_max_requests=emphasis_requests,
        verifier_prompt_tokens_bound=reviewer['prompt_tokens_bound'],
        verifier_response_token_bound=reviewer['response_token_bound'],
        ordering_max_requests=ordering_rows, lexical_max_requests=lexical_rows,
        ordering_request_bound_usd=ordering['bound_usd'], lexical_request_bound_usd=lexical['bound_usd'],
        ordering_bound_usd=ordering_rows*ordering['bound_usd'],
        lexical_bound_usd=lexical_rows*lexical['bound_usd'])
    # A preceding semantic candidate is unavailable at estimate time. Quote its
    # full admitted transport allowance, while binding the exact original source,
    # schema and shared prompt. No candidate context gets an exact cache shortcut.
    proposal = layout_requests.proposal(prepared, hints=hints, profile_selection=profile_selection)
    row.update(proposer_view_sha256=_digest(json.loads(proposal['messages'][-1]['content'])),
        proposer_schema_sha256=_digest(proposal['response_schema']),
        proposer_prompt_sha256=_digest(proposal['messages'][0]['content']),
        proposer_neighbor_context=bool(source.get('previous')),
        proposer_baseline_bound_usd=wire.bound_usd,
        proposer_baseline_prompt_tokens_bound=wire.prompt_tokens_bound,
        proposer_baseline_response_token_bound=wire.response_token_bound)
    if hints is not None or (transport.profile_selection(profile_selection) == dict(transport.LUNA_REVIEW_SELECTION) or (layout_glm.selected(profile_selection) or layout_luna_high.selected(profile_selection))):
        if hints is not None:
            from . import layout_hints
            row['layout_advisory'] = layout_hints.identity(prepared,hints)
        if (transport.profile_selection(profile_selection) == dict(transport.LUNA_REVIEW_SELECTION) or (layout_glm.selected(profile_selection) or layout_luna_high.selected(profile_selection))):
            from . import layout_eligibility
            row['source_policy'] = layout_eligibility.VERSION
        row['hint_envelope_sha256'] = _digest({k:v for k,v in proposal.items() if k not in ('messages','response_schema')})
        from . import layout_ranges
        row['review_fixed_view_sha256'] = _digest(layout_ranges.source_view(prepared,True))
        row['review_prompt_sha256'] = _digest('\n'.join((layout_ranges.SOURCE,layout_ranges.LAYOUT,layout_ranges.CHOICES,layout_ranges.REVIEW)) + (layout_glm.JOIN if (layout_glm.selected(profile_selection) or layout_luna_high.selected(profile_selection)) else ''))
        if (layout_glm.selected(profile_selection) or layout_luna_high.selected(profile_selection)): row['review_policy'] = layout_luna_high.REVIEW_POLICY if layout_luna_high.selected(profile_selection) else layout_glm.REVIEW_POLICY
    if row['proposer_neighbor_context']:
        limit = _maximum('proposer', clients['proposer'])
        row.update(proposer_bound_usd=limit['bound_usd'],
            proposer_prompt_tokens_bound=limit['prompt_tokens_bound'],
            proposer_response_token_bound=limit['response_token_bound'])
    row['batch_bound_usd'] = row['ordering_bound_usd']+row['lexical_bound_usd']
    row['full_bound_usd'] = row['proposer_bound_usd']+row['verifier_bound_usd']+row['batch_bound_usd']
    return row


def measure(doc, *, recovery_opts=None, prepared_result=None, progress=None, should_stop=None,
            profile_selection=None, layout_hint_packets=None):
    """Prepare current complete source without network, credentials or cache claims."""
    selection = transport.profile_selection(profile_selection)
    pages = []
    limited = {}
    hints = dict(layout_hint_packets or {})
    def observe(book, prepared, source_page):
        if prepared.page in hints:
            from . import layout_hints
            layout_hints.validate(prepared,hints[prepared.page])
        try:
            pages.append(measure_page(book, doc, prepared, source_page, profile_selection=selection, hints=hints.get(prepared.page)))
        except ValueError:
            # A transport-inadmissible proposer cannot buy a later stage.
            limited[prepared.page] = _limited_binding(prepared,hints.get(prepared.page))
    result = layout_pipeline.run_layout(doc, client=None, recovery_opts=recovery_opts,
        prepared_result=prepared_result, progress=progress, should_stop=should_stop,
        prepared_observer=observe, layout_hint_packets=hints)
    counts = result.structural
    eligible = {p['page_index0'] for p in pages}
    coverage = []
    for row in counts['pages']:
        if row['page'] in eligible:
            row = dict(row, status='eligible', reason='')
        elif row['page'] in limited:
            row = dict(row, status='limited', reason='layout_request_exceeds_transport_admission')
        coverage.append(row)
    clients = _clients(selection)
    models = {stage:dict(id=client.model_id, input_usd_per_million=client.spec.prompt_usd_per_mtok,
        output_usd_per_million=client.spec.completion_usd_per_mtok,
        reservation_input_usd_per_million=client.reservation_rates[0],
        reservation_output_usd_per_million=client.reservation_rates[1],
        provider=client.profile.route, max_output_tokens=client.profile.completion_cap,
        minimum_output_tokens=client.profile.output_tokens, reasoning_effort=client.profile.reasoning_effort,
        allocation_version=layout_allocation.version(client.profile_id))
        for stage,client in clients.items()}
    models['verifier'] = dict(models['reviewer'])  # Existing estimate UI vocabulary.
    quote = dict(_versions(selection, bool(hints)), source_sha256=result.fingerprint,
        source_context_pages=len(result.book.pages), first_body_page=pipeline.first_body_page(result.book),
        eligible_pages=len(pages), limited_pages=len(limited), unsupported_pages=counts['unsupported'],
        no_choice_pages=counts['no_choices'], pages=pages, coverage=coverage,
        limited_sources=list(limited.values()),
        kind='reservation_ceiling_not_expected_bill', assumes_verifier_on_every_eligible_page=True,
        assumes_separate_emphasis_review_on_every_eligible_page=True,
        assumes_no_paid_cache_hits=True, assumes_singleton_batch_requests=True,
        confirmed_usd=0, held_usd=0, models=models,
        provider='openai/flex + anthropic', service_tier='flex (OpenAI)',
        reasoning={stage:client.profile.reasoning_effort for stage,client in clients.items()},
        max_output_tokens=max(c.profile.completion_cap for c in clients.values()),
        request_limits={stage:_maximum(stage,client) for stage,client in clients.items()},
        ordering_max_requests=sum(p['ordering_max_requests'] for p in pages),
        verifier_max_requests=sum(p['verifier_max_requests'] for p in pages),
        emphasis_max_requests=sum(p['emphasis_max_requests'] for p in pages),
        lexical_max_requests=sum(p['lexical_max_requests'] for p in pages))
    if selection:
        quote.update(provider=' + '.join(dict.fromkeys(c.profile.route for c in clients.values())),
            service_tier={stage:c.profile.service_tier or 'standard' for stage,c in clients.items()})
        for stage,client in clients.items():
            quote['models'][stage].update(profile_id=client.profile_id,
                supporting_reasoning_tokens=client.profile.supporting_reasoning_tokens)
        quote['models']['verifier'] = dict(quote['models']['reviewer'])
    for key in ('proposer_bound_usd','verifier_bound_usd','ordering_bound_usd','lexical_bound_usd','batch_bound_usd'):
        quote[key] = sum(p[key] for p in pages)
    quote['full_bound_usd'] = quote['proposer_bound_usd']+quote['verifier_bound_usd']+quote['batch_bound_usd']
    quote['identity'] = _digest(quote)
    return quote


def _current_quote(quote, profile_selection=None):
    body = dict(quote)
    identity = body.pop('identity', None)
    hinted = 'hint_quote_version' in quote
    if identity != _digest(body) or any(quote.get(k)!=v for k,v in _versions(profile_selection, hinted).items()):
        raise EstimateStale('layout source, prompt, profile or quote version changed')
    if quote.get('profile_selection', {}) != transport.profile_selection(profile_selection):
        raise EstimateStale('layout profile selection differs from the quote')


def consent_observer(quote, doc, *, profile_selection=None, layout_hint_packets=None):
    """Re-measure every current page before paid work; hash is not consent authority."""
    hints = dict(layout_hint_packets or {})
    def observe(book, prepared, source_page):
        _current_quote(quote, profile_selection)
        if bool(hints) != ('hint_quote_version' in quote):
            raise EstimateStale('layout hint selection differs from quote')
        expected = {p['page_index0']:p for p in quote.get('pages', [])}
        if prepared.page in hints:
            from . import layout_hints
            layout_hints.validate(prepared,hints[prepared.page])
        try:
            actual = measure_page(book, doc, prepared, source_page, profile_selection=profile_selection, hints=hints.get(prepared.page))
        except (ValueError, TypeError, KeyError) as exc:
            limited = {p['page_index0']:p for p in quote.get('limited_sources', [])}
            if prepared.pdf_digest==quote.get('source_sha256') and limited.get(prepared.page)==_limited_binding(prepared,hints.get(prepared.page)):
                return  # Same inadmissible source; no request has a quoted ceiling.
            raise EstimateStale('current layout request no longer fits the quote') from exc
        if prepared.pdf_digest!=quote.get('source_sha256') or expected.get(prepared.page)!=actual:
            raise EstimateStale('current layout source differs from the prepared estimate')
    return observe


def assert_request_bound(quote, stage, request, page_index0=None, *, profile_selection=None):
    """Check an actual typed wire against the consented stage ceiling, no dispatch.

    Batch splitting must retain the unique-row counts quoted above. This function
    checks one wire, not repeated attempts or cumulative job expenditure; the
    durable ledger is still mandatory for total spend control.
    """
    _current_quote(quote, profile_selection)
    if stage == 'verifier':
        stage = 'reviewer'
    clients = _clients(profile_selection)
    if stage not in clients:
        raise EstimateStale('unknown layout quote stage')
    try:
        clients[stage]._check_request(request)
        limit = quote['request_limits'][stage]
        if stage in ('proposer','reviewer'):
            rows = {p['page_index0']:p for p in quote['pages']}
            row = rows[page_index0]
            key = 'proposer' if stage=='proposer' else 'verifier'
            limit = {name:row[key+'_'+name] for name in ('bound_usd','prompt_tokens_bound','response_token_bound')}
            if stage=='reviewer':
                limit['bound_usd']=row['verifier_request_bound_usd']
                style = json.loads(request.context_json).get('decision_channel')=='emphasis'
                if style and not row['emphasis_max_requests']:
                    raise ValueError('optional emphasis has no quoted capacity')
                if row.get('layout_advisory') is not None and style:
                    from . import layout_wire
                    messages=request.payload['messages'][:-1]
                    metadata=json.loads(messages[1]['content'])
                    view=layout_wire.source_view(messages)
                    identity=metadata['source_identity']
                    binding=identity.get('emphasis_snapshot')
                    properties=dict(snapshot=dict(type='string',enum=[binding]),accept=dict(type='boolean'),
                        problems=dict(type='array',items=dict(type='string',enum=['unsupported_change']),maxItems=1))
                    schema=dict(type='object',properties=properties,required=list(properties),additionalProperties=False)
                    if (len(messages)!=3 or metadata['protocol']!=layout_requests.PROTOCOL
                            or metadata['prompt_version']!=layout_requests.PROMPT_VERSION+'-emphasis-reviewer'
                            or metadata['context']!=dict(stage='emphasis-reviewer',decision_channel='emphasis')
                            or set(identity)!={'snapshot','emphasis_snapshot'} or not binding
                            or view.get('snapshot')!=binding or 'layout_advisory' in view
                            or messages[0]['content']!=layout_emphasis.PROMPT
                            or request.payload['response_format']['json_schema']['schema']!=schema):
                        raise ValueError('optional style wire differs')
                if (row.get('layout_advisory') is not None or row.get('source_policy') is not None) and not style:
                    from . import layout_wire, layout_ranges
                    messages=request.payload['messages'][:-1]
                    metadata=json.loads(messages[1]['content'])
                    view=layout_wire.source_view(messages)
                    binding=view.pop('snapshot')
                    choices=view.pop('candidate_choices')
                    table=view.pop('decisions')
                    view.pop('previous_candidate',None)
                    expected=dict(snapshot=row['snapshot_id'],review_snapshot=binding,construction_version=layout_ranges.VERSION)
                    schemas=[layout_ranges.review_schema(binding,layout_ranges.decision_count(table))]
                    if (layout_glm.selected(profile_selection) or layout_luna_high.selected(profile_selection)):
                        expected['review_policy'] = row['review_policy']
                        import copy
                        restricted=copy.deepcopy(schemas[0])
                        restricted['properties']['continuation_accept']['enum']=[False]
                        schemas.append(restricted)
                    if (len(messages)!=3 or metadata['protocol']!=layout_requests.PROTOCOL
                            or metadata['prompt_version']!=quote['verifier_prompt']
                            or metadata['context']!=dict(stage='reviewer')
                            or metadata['source_identity']!=expected
                            or _digest(messages[0]['content'])!=row['review_prompt_sha256']
                            or _digest(view)!=row['review_fixed_view_sha256']
                            or request.payload['response_format']['json_schema']['schema'] not in schemas):
                        raise ValueError('independent review full source or wire differs')
            identity = json.loads(request.context_json)['source_identity']
            if identity.get('snapshot') != row['snapshot_id']:
                raise ValueError('request source differs')
            if stage=='proposer' and identity.get('layout_advisory') != row.get('layout_advisory'):
                raise ValueError('proposer hint binding differs')
            if stage=='proposer' and request.sha256!=row['proposer_request_sha256']:
                if not row['proposer_neighbor_context']:
                    raise ValueError('proposer wire differs')
                from . import layout_wire
                messages=request.payload['messages'][:-1]
                view=layout_wire.source_view(messages)
                candidate=view.pop('previous_candidate',None)
                prompt=messages[0]['content']
                # Only the source-checked neighbor field/suffix may vary. Bind
                # every other instruction and envelope byte to the experiment.
                metadata=json.loads(messages[1]['content'])
                base_identity=dict(identity)
                base_identity.pop('previous_candidate',None)
                baseline={k:metadata[k] for k in ('protocol','prompt_version','context')}
                baseline['source_identity']=base_identity
                if row.get('layout_advisory') is not None or row.get('source_policy') is not None:
                    metadata_ok=_digest(baseline)==row['hint_envelope_sha256']
                else:
                    metadata_ok=baseline==dict(protocol=layout_requests.PROTOCOL,
                        prompt_version=quote['proposer_prompt'],context=dict(stage='proposer'),source_identity=base_identity)
                keys={'snapshot','previous_candidate','construction_version'}
                if profile_selection == dict(transport.LUNA_REVIEW_SELECTION) or (layout_glm.selected(profile_selection) or layout_luna_high.selected(profile_selection)): keys.add('source_policy')
                if (layout_glm.selected(profile_selection) or layout_luna_high.selected(profile_selection)): keys.add('join_policy')
                if row.get('layout_advisory') is not None:keys.add('layout_advisory')
                if (len(messages)!=3 or not metadata_ok or not isinstance(candidate,dict) or set(identity)!=keys
                        or identity.get('construction_version')!=quote['construction_version']
                        or candidate.get('snapshot')!=identity['previous_candidate']
                        or not prompt.endswith(layout_requests.PREVIOUS_CANDIDATE_PROMPT)
                        or _digest(prompt[:-len(layout_requests.PREVIOUS_CANDIDATE_PROMPT)])!=row['proposer_prompt_sha256']
                        or _digest(view)!=row['proposer_view_sha256']
                        or _digest(request.payload['response_format']['json_schema']['schema'])!=row['proposer_schema_sha256']):
                    raise ValueError('proposer source, schema, prompt or neighbor binding differs')
        elif quote[stage+'_max_requests'] < 1:
            raise ValueError('batch stage has no quoted candidate capacity')
        if request.bound_usd>limit['bound_usd']+1e-12 or request.prompt_tokens_bound>limit['prompt_tokens_bound'] or request.response_token_bound>limit['response_token_bound']:
            raise ValueError('request exceeds stage reservation')
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        raise EstimateStale('actual layout request differs from the prepared quote') from exc
    return True

"""Changed required task bytes cannot reuse old consent/cache; no quality oracle."""
import copy
import json
import pytest
from cps.services.reflow import layout_ranges as ranges, layout_requests as req
from cps.services.reflow import layout_quote as quote, layout_pipeline as lp, layout_model as lm, layout_wire as wire
from cps.services.reflow.operation_cache import OperationCache
from cps.services.reflow.structural_pipeline import EstimateStale
from tests.unit.test_reflow_layout_ops import prepared
from tests.unit.test_reflow_structural_ops import source
from tests.unit.test_reflow_structural_pipeline import prepared_result
pytestmark=pytest.mark.unit


def old_range_envelope(prep):
    """Original range1 declaration grammar and task; not an adopted answer."""
    src=json.loads(prep.contract_json)
    return req.envelope('proposer',dict(snapshot=prep.snapshot_id,construction_version=ranges.VERSION),
        ranges.source_view(prep), '\n'.join((ranges.SOURCE,ranges.LAYOUT,ranges.CHOICES)),
        ranges.schema(src,prep.model_view()))


def test_changed_task_requires_fresh_quote_and_cache_with_identical_source_universe(tmp_path,source):
    """Drive full runtime wire/quote/cache for all supported profile selections.

    Reusing the previous proposer task or quote must fail this check. It makes
    no claim about a model's natural-paragraph judgment.
    """
    _,doc,_,_,prep=prepared(source)
    for i,selection in enumerate((None,lm.DISABLED_SELECTION,lm.QWEN_SELECTION)):
        c=lp.LayoutClient('',profile_selection=selection).stages['proposer']
        current=c.prepare_request(req.proposal(prep),prep.raster)
        prior=c.prepare_request(old_range_envelope(prep),prep.raster)
        assert current.payload['response_format']==prior.payload['response_format']
        assert wire.source_view(current.payload['messages'][:-1])==wire.source_view(prior.payload['messages'][:-1])
        assert current.sha256!=prior.sha256 and current.prompt_version!=prior.prompt_version
        consent=quote.measure(doc,prepared_result=prepared_result(source),profile_selection=selection)
        assert quote.assert_request_bound(consent,'proposer',current,0,profile_selection=selection)
        with pytest.raises(EstimateStale):quote.assert_request_bound(consent,'proposer',prior,0,profile_selection=selection)
        stale=copy.deepcopy(consent);stale['proposer_prompt']=prior.prompt_version
        stale['identity']=quote._digest({k:v for k,v in stale.items() if k!='identity'})
        with pytest.raises(EstimateStale):quote._current_quote(stale,selection)
        cache=OperationCache(tmp_path/str(i));claim,_=cache.claim(prior.sha256)
        cache.finish(prior.sha256,claim,response={'negative_historical_fixture':True})
        claim,saved=cache.claim(current.sha256);assert claim and saved is None
        cache.release_unsent(current.sha256,claim)

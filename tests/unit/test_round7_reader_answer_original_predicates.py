"""Exact strict answer-normalization predicates stay contained by physical page."""
import copy,pytest
from cps.services.reflow import vision_reading as vr
from cps.services.reflow.ledger import Ledger
from tests.unit.round7_recovery_test_support import fixture,publish,h,TEXTS

@pytest.mark.parametrize('defect',['tokens_not_list','empty_token','confidence_not_words','confidence_absent_word'])
def test_original_answer_normalization_failure_has_only_owned_source_fallback(tmp_path,monkeypatch,defect):
    doc,reader,_=fixture(tmp_path,monkeypatch);actual=reader.read;reached=[]
    strict=vr.Reader(None,tmp_path/'strict-cache',Ledger(tmp_path/'ledger.jsonl',cap_usd=0),confidence_protocol='tokens' if defect.startswith(('tokens','empty')) else None,token_confidence=defect.startswith('confidence'))
    def reject(image):
        receipt=actual(image)
        if reader.calls[-1]==1:
            answer=copy.deepcopy(receipt['answer']);block=answer['blocks'][0]
            if defect=='tokens_not_list':block['tokens']='malformed'
            elif defect=='empty_token':block['tokens']=[{'text':'','low_confidence':False}]
            elif defect=='confidence_not_words':block['low_confidence']=[1]
            else:block['low_confidence']=['neverprintedword']
            try:strict._answer(answer)
            except ValueError as error:reached.append(str(error));raise
        return receipt
    monkeypatch.setattr(reader,'read',reject)
    _,report,docs=publish(doc,reader,tmp_path)
    assert reached==[{'tokens_not_list':'invalid transcription tokens','empty_token':'invalid transcription token text','confidence_not_words':'invalid confidence word','confidence_absent_word':'invalid confidence word'}[defect]]
    assert h.body_texts(docs)==[TEXTS[0],TEXTS[2]]
    assert {(u['page'],u.get('block')) for u in report['degraded_units']}=={(1,None)}
    assert reader.calls==[0,1,2] and strict.ledger.spent()==strict.ledger.pending_usd()==0

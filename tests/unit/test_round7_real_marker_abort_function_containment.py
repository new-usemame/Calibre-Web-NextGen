"""Actual compact marker cache/response predicates cannot abort an owned source block."""
import hashlib,json,copy,pytest
from types import SimpleNamespace
from cps.services.reflow import vision_pipeline as vp,vision_pixels,vision_reading as vr
from cps.services.reflow.ledger import Ledger
from tests.unit.round7_recovery_test_support import h
from tests.unit.round7_public_note_contract_support import source_visible
BODY='The amber source ends with a compact mark.*'
NOTE='The finite note preserves every violet attribution word.'
PROMPT='Read only the printed note-reference label in this source crop. Return its exact digits or reference symbol (*, dagger or double dagger). Ignore enclosing parentheses, punctuation and surrounding prose. Do not infer the label from context. If the ink cannot be read, return an empty label and uncertain true.'
SCHEMA={'type':'object','additionalProperties':False,'required':['label','uncertain'],'properties':{'label':{'type':'string'},'uncertain':{'type':'boolean'}}}

@pytest.mark.parametrize('defect',['invalid_label','cache_identity','pending','offline','invalid_bill','outside_ceiling','incomplete'])
def test_real_compact_marker_abort_preserves_exact_owned_source_and_note(tmp_path,monkeypatch,defect):
    page=h.InkPage();page.text(BODY,'body',y=65);page.text('* '+NOTE,'note','footnote',y=335,size=7,marker='*',model=NOTE)
    marker=vr.Reader(None if defect in ('cache_identity','pending','offline') else 'synthetic-local-transport',tmp_path/'marker-cache',Ledger(tmp_path/'ledger.jsonl',cap_usd=.02))
    calls=[];dispatches=[];convert=vp.convert
    def verify(image):
        identity=hashlib.sha256(image).hexdigest();config=hashlib.sha256(('printed-marker-v1'+vr.MODEL+PROMPT+json.dumps(SCHEMA,sort_keys=True)).encode()).hexdigest()
        path=marker.cache/('marker-'+identity+'-'+config+'.json')
        if defect=='cache_identity':path.write_text(json.dumps({'label':'*','uncertain':False,'image_sha256':'0'*64,'config_sha256':config,'cost_usd':0}))
        if defect=='pending':path.with_suffix('.pending').write_text(json.dumps({'reservation':'retained-untrusted-pending'}))
        try:return marker.read_marker(image)
        except Exception as error:calls.append((type(error).__name__,str(error)));raise
    def public(doc,out,reader,**kwargs):
        reader.read_marker=verify;reader.ledger=marker.ledger
        return convert(doc,out,reader,**kwargs)
    monkeypatch.setattr(vp,'convert',public)
    def characters(display,box,cache):
        # Exact physical star ink; shrink only the candidate envelope to demand
        # the existing mandatory compact-label reader protocol.
        words=vision_pixels.words(display,cache)
        word=next(w for w in words if w['text'].endswith('*'))
        ink=vision_pixels.terminal_symbol_ink(display,word['bbox']);assert ink
        cy=(ink[1]+ink[3])/2
        return [{'label':'*','bbox':[ink[0],cy-1,ink[2],cy+1],'kind':'bare'}]
    monkeypatch.setattr(vision_pixels,'raised_markers',characters)
    import requests
    def transport(*args,**kwargs):
        dispatches.append('actual marker request intercepted locally')
        cost=float('nan') if defect=='invalid_bill' else .2 if defect=='outside_ceiling' else .00001
        data={'id':'synthetic-marker-receipt','model':vr.MODEL,'usage':{'cost':cost},'choices':[{'finish_reason':'length' if defect=='incomplete' else 'stop','message':{'content':json.dumps({'label':'invalid' if defect=='invalid_label' else '*','uncertain':False})}}]}
        return SimpleNamespace(raise_for_status=lambda:None,json=lambda:data)
    monkeypatch.setattr(requests,'post',transport)
    path,report,docs=h.convert_ink(tmp_path,monkeypatch,[page])
    expected={'invalid_label':'Invalid printed marker response','cache_identity':'Marker cache identity mismatch','pending':'Unreconciled marker reading; not retried','offline':'Printed marker absent from offline cache','invalid_bill':'Invalid marker bill','outside_ceiling':'Marker bill outside reserved ceiling','incomplete':'Incomplete or substituted marker reader'}[defect]
    assert calls and all(message==expected for _,message in calls)
    assert len(dispatches)==(0 if defect in ('cache_identity','pending','offline') else 1)
    assert {(u['page'],u.get('block')) for u in report['degraded_units']}=={(0,'body')}
    assert h.refs_and_notes(docs)[0]==[]
    assert sum(NOTE in ''.join(note.itertext()) for _,note in h.refs_and_notes(docs)[1])==1
    source_visible(path,report,{0})

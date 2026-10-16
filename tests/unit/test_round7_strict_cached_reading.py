"""Round7 safe public recovery does not weaken the strict cached Reader API."""
import copy
import hashlib
import io
import json
from PIL import Image
import pytest
from cps.services.reflow import vision_reading as vr
from cps.services.reflow.ledger import Ledger


@pytest.mark.parametrize('alias',['foreign','body'])
def test_strict_reader_rejects_unbound_note_alias_from_actual_image_cache(tmp_path,alias):
    image=Image.new('RGB',(20,20),'white');stream=io.BytesIO();image.save(stream,'JPEG');data=stream.getvalue()
    reader=vr.Reader(None,tmp_path/'readings',Ledger(tmp_path/'ledger.jsonl',cap_usd=.02))
    answer={'blocks':[{'id':'body','role':'body','text':f'A bounded printed citation <n:{alias}> remains visible.','marker':'','bbox':[0,0,1000,1000],'low_confidence':[],'continues_previous':False,'continues_next':False}]}
    identity=hashlib.sha256(data).hexdigest()
    config=hashlib.sha256((vr.VERSION+vr.MODEL+reader.prompt+json.dumps(reader.api_schema,sort_keys=True)).encode()).hexdigest()
    record={'image_sha256':identity,'config_sha256':config,'cost_usd':.001,'request_id':'synthetic-prior-complete','answer':copy.deepcopy(answer)}
    path=reader.cache/(identity+'-'+config+'.json');path.write_text(json.dumps(record))
    before=path.read_bytes()
    with pytest.raises(ValueError,match='unbound printed note marker'):
        reader.read(data)
    assert path.read_bytes()==before, 'strict inspection cannot rewrite a rejected receipt'
    assert reader.ledger.spent()==reader.ledger.pending_usd()==0, 'cached strict rejection cannot dispatch or invent a bill'

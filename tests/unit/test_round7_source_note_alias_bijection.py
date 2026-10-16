"""SOURCE-correlated aliases need a complete ordered glyph bijection.
These tests call the new public cached reading protocol with synthetic bounded
source observations. Converter pixel-correlation is independently controlled.
"""
import copy
import hashlib
import io
import json
import re
from PIL import Image
import pytest
from cps.services.reflow import vision_reading as vr
from cps.services.reflow.ledger import Ledger


def fixture(tmp_path):
    image=Image.new('RGB',(160,200),'white');stream=io.BytesIO();image.save(stream,'JPEG');pixels=stream.getvalue()
    block=lambda ident,role,text,marker='':dict(id=ident,role=role,text=text,marker=marker,bbox=[0,0,1000,1000],low_confidence=[0] if role=='body' else [],continues_previous=False,continues_next=False)
    answer={'blocks':[block('body','body','First amber item <n:fn1> remains. Second violet item <n:fn2> remains.'),block('n1','footnote','Amber attribution remains exactly original.','*'),block('n2','footnote','Violet attribution remains exactly original.','†')]}
    matches=list(re.finditer(r'<n:[^>]+>',answer['blocks'][0]['text']))
    evidence=dict(source_sha256=hashlib.sha256(pixels).hexdigest(),references=[dict(block='body',offset=m.start(),label=label,bbox=[20+i*30,20,25+i*30,27],source_proved=True) for i,(m,label) in enumerate(zip(matches,['*','†']))],notes=[dict(id='n'+str(i+1),label=label,bbox=[10,130+i*20,15,137+i*20],source_proved=True) for i,label in enumerate(['*','†'])])
    reader=vr.Reader(None,tmp_path/'readings',Ledger(tmp_path/'ledger.jsonl',cap_usd=.02))
    identity=hashlib.sha256(pixels).hexdigest();config=hashlib.sha256((vr.VERSION+vr.MODEL+reader.prompt+json.dumps(reader.api_schema,sort_keys=True)).encode()).hexdigest()
    cache=reader.cache/(identity+'-'+config+'.json');cache.write_text(json.dumps(dict(answer=answer,image_sha256=identity,config_sha256=config,cost_usd=.001,request_id='synthetic-known-billed-rejection')))
    return reader,pixels,answer,evidence,cache


def test_cached_alias_repair_preserves_exact_words_flags_and_rejected_receipt(tmp_path):
    reader,pixels,answer,evidence,path=fixture(tmp_path);before=path.read_bytes()
    with pytest.raises(ValueError,match='unbound printed note marker'):reader.read(pixels)
    record=reader.read_unit(pixels,source_repair=lambda value:vr.repair_note_ids(value,evidence))
    assert not record.get('degraded_source') and record['cached'] and record['new_cost_usd']==0
    expected=copy.deepcopy(answer);expected['blocks'][0]['text']=expected['blocks'][0]['text'].replace('<n:fn1>','<n:n1>').replace('<n:fn2>','<n:n2>')
    assert record['answer']==expected, 'only proved alias IDs may change, never words/confidence/order'
    assert vr.validate(record['answer'])==expected
    assert record['source_repairs'][0]['mapping']=={'fn1':'n1','fn2':'n2'}
    retained=record['rejected_receipt'];assert retained['sha256']==hashlib.sha256(before).hexdigest()
    assert __import__('pathlib').Path(retained['path']).read_bytes()==before
    assert reader.ledger.spent()==reader.ledger.pending_usd()==0


@pytest.mark.parametrize('defect',['count','order','glyph','offset','unproved','repeated_alias'])
def test_unproved_alias_correspondence_stays_source_degraded_without_prefix_guess(tmp_path,defect):
    reader,pixels,answer,evidence,path=fixture(tmp_path)
    if defect=='count':evidence['references'].pop()
    elif defect=='order':evidence['notes'].reverse()
    elif defect=='glyph':evidence['references'][0]['label']='†'
    elif defect=='offset':evidence['references'][0]['offset']+=1
    elif defect=='unproved':evidence['notes'][0]['source_proved']=False
    elif defect=='repeated_alias':
        answer['blocks'][0]['text']=answer['blocks'][0]['text'].replace('fn2','fn1')
        record=json.loads(path.read_text());record['answer']=answer;path.write_text(json.dumps(record))
    with pytest.raises(ValueError):vr.repair_note_ids(copy.deepcopy(answer),evidence)
    outcome=reader.read_unit(pixels,source_repair=lambda value:vr.repair_note_ids(value,evidence))
    assert outcome['degraded_source'] and outcome['answer'] is None
    assert outcome['new_cost_usd']==0 and not path.exists(), 'untrusted cache is retained/quarantined rather than silently promoted'
    assert any(p.is_file() for p in reader.cache.glob('rejected/*'))
    assert reader.ledger.spent()==reader.ledger.pending_usd()==0

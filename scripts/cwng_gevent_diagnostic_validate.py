from pathlib import Path
import json,os
R=Path(os.environ['CWNG_GEVENT_DIAGNOSTIC_DIR']);record={'diagnostic_evidence_status':'FAILED','original_outcome_unchanged':True,'errors':[]}
try:
 summaries={}
 for path in R.glob('gw*-summary.jsonl'):
  rows=[json.loads(line) for line in path.read_text().splitlines()];summaries[path.name]=rows[-1] if rows else {}
 assert summaries,'No original target-call worker evidence; partial/setup failure is not diagnostic PASS'
 record['worker_summaries']={name:{'status':data.get('status'),'observer_evidence_valid':data.get('observer_evidence_valid',False)} for name,data in summaries.items()}
 assert all(data.get('observer_evidence_valid',False) for data in summaries.values()),'Incomplete, unsupported, limited or failed observer evidence'
 processes=[json.loads(line) for line in (R/'processes.jsonl').read_text().splitlines()]
 finishes=[p for p in processes if p['kind']=='sessionfinish'];assert finishes,'No actual pytest session finish; abrupt exit remains ungraded'
 assert all(not p.get('artifact_failures') and not p.get('parent_progress_limit_reached') for p in finishes),'Parent/worker artifact failure or bounded progress exhaustion'
 control=json.loads((R/'stdlib-control-parent.json').read_text());assert control['status']=='MEANINGFUL_CONTROL_EXPECTED_RED' and control['group_absent']
 record['diagnostic_evidence_status']='SCOPED_OBSERVER_AND_CONTROL_EVIDENCE_COMPLETE'
except BaseException as error:
 record['diagnostic_evidence_status']='FAILED';record['errors'].append(type(error).__name__+': '+str(error))
(R/'diagnostic-evidence-validation.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps(record));raise SystemExit(0 if not record['errors'] else 1)

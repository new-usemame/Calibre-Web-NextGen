from pathlib import Path
import subprocess,json,hashlib,os,platform,sys,importlib.metadata
R=Path(os.environ['CWNG_GEVENT_DIAGNOSTIC_DIR']);R.mkdir(parents=True,exist_ok=True)
W=Path.cwd();D=Path(os.environ['GITHUB_WORKSPACE'])/'diagnostic';H='846aa4b0e537e7cd9f1f8691547299f11a598dfa'
def git(root,*args):return subprocess.check_output(['git',*args],cwd=root,text=True).strip()
assert git(W,'rev-parse','HEAD')==H
assert not git(W,'status','--porcelain','--untracked-files=no')
record={'product_head':H,'diagnostic_head':git(D,'rev-parse','HEAD'),'python':sys.version,'platform':platform.platform(),'cwd':str(W),'context_differences':['workflow_dispatch vs historical pull_request; platform GITHUB metadata unchanged and exposed by allowlist','Sibling product/diagnostic checkout paths and explicit product cwd/PYTHONPATH/cache paths','Observer wrappers, native20ms sampler/GC callback and bounded evidence writes perturb timing','Actual auto worker count recorded, never forced to2; hosted runner state/version/time not historical identity','Post-suite fresh-child stdlib control has cold imports, not actual original worker inherited state','Optional post-call Codecov upload omitted; it occurs after original full test command'],'environment_allowlist':{k:os.environ.get(k,'MISSING') for k in ['GITHUB_EVENT_NAME','GITHUB_REF','GITHUB_SHA','GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT','GITHUB_REPOSITORY','GITHUB_WORKFLOW','GITHUB_JOB','GITHUB_WORKSPACE','RUNNER_OS','RUNNER_ARCH','ImageOS','ImageVersion','PYTEST_XDIST_AUTO_NUM_WORKERS']},'source_hashes':{}}
for path in ['.github/workflows/tests.yml','pyproject.toml','tests/unit/test_request_fanout_gevent_responsiveness.py','cps/services/parallel.py']:
 record['source_hashes'][path]=hashlib.sha256((W/path).read_bytes()).hexdigest()
record['versions']={}
for name in ['pytest','pytest-xdist','pytest-cov','coverage','gevent','greenlet']:
 try:record['versions'][name]=importlib.metadata.version(name)
 except importlib.metadata.PackageNotFoundError:record['versions'][name]='MISSING'
record['no_diagnostic_files_inside_product_tree']=not (W/'diagnostic').exists()
assert record['no_diagnostic_files_inside_product_tree']
# Read installed metadata only, before the full suite. Preserve duplicate records.
installed = []
inventory_errors = []
truncated = False
try:
 for index, distribution in enumerate(importlib.metadata.distributions()):
  if index >= 2000:
   truncated = True
   break
  try:
   name = distribution.metadata.get('Name')
   version = distribution.version
   if not isinstance(name, str) or not isinstance(version, str):
    inventory_errors.append({'index': index, 'error_type': 'MissingNameOrVersion'})
    continue
   installed.append({'Name': name, 'Version': version})
  except Exception as error:
   inventory_errors.append({'index': index, 'error_type': type(error).__name__})
except Exception as error:
 inventory_errors.append({'error_type': type(error).__name__})
record['installed_distribution_inventory'] = {
 'records': sorted(installed, key=lambda entry: (entry['Name'].casefold(), entry['Name'], entry['Version'])),
 'record_limit': 2000, 'reported_count': len(installed), 'duplicates_retained': True,
 'truncated': truncated, 'complete': not truncated and not inventory_errors,
 'errors': inventory_errors,
 'scope': 'Installed Name+Version metadata only, no package paths/URLs/authors; if truncated, sorted reported prefix is not a complete environment inventory'}
(R/'context.json').write_text(json.dumps(record,indent=2)+'\n')

from pathlib import Path
import datetime,json,os,signal,subprocess,sys,time
ROOT=Path(os.environ['CWNG_GEVENT_DIAGNOSTIC_DIR']);ROOT.mkdir(parents=True,exist_ok=True)
record={'status':'FAILED','started':datetime.datetime.now(datetime.timezone.utc).isoformat(),'context':'Separate sensitivity control; never replaces original full-suite outcome'};child=None

def save(): (ROOT/'stdlib-control-parent.json').write_text(json.dumps(record,indent=2)+'\n')
def absent():
 if child is None:return True
 try:os.killpg(child.pid,0);return False
 except ProcessLookupError:return True
def cleanup():
 if child is None:return
 for signum,grace in [(signal.SIGTERM,1),(signal.SIGKILL,5)]:
  try:os.killpg(child.pid,signum)
  except ProcessLookupError:child.wait(timeout=5);return
  deadline=time.monotonic()+grace
  while time.monotonic()<deadline:
   child.poll()
   if absent():child.wait(timeout=5);return
   time.sleep(0.05)
 raise RuntimeError('owned stdlib control group remains')
def stop(signum,frame):raise InterruptedError('controlled signal '+str(signum))
for s in (signal.SIGTERM,signal.SIGINT):signal.signal(s,stop)
try:
 with (ROOT/'stdlib-control.log').open('wb') as log:
  child=subprocess.Popen([sys.executable,str(Path(__file__).with_name('cwng_gevent_diagnostic_control.py'))],cwd=Path.cwd(),env=os.environ.copy(),stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
  record['child_pid']=child.pid;save();record['child_exit']=child.wait(timeout=20)
 assert record['child_exit']==0
 data=json.loads((ROOT/'stdlib-control/stdlib-actual.json').read_text());assert data['status']=='MEANINGFUL_STDLIB_EXPECTED_RED' and data['all_five_results_correct']
 assert absent(),'Owned control group survived leader exit'
 record['status']='MEANINGFUL_CONTROL_EXPECTED_RED'
except BaseException as error:
 record['status']='FAILED';record['error']=repr(error)
finally:
 try:cleanup()
 except BaseException as error:record['status']='FAILED';record['cleanup_error']=repr(error)
 record['group_absent']=absent()
 if not record['group_absent']:record['status']='FAILED'
 record['finished']=datetime.datetime.now(datetime.timezone.utc).isoformat();save()
print(json.dumps(record));raise SystemExit(0 if record['status']=='MEANINGFUL_CONTROL_EXPECTED_RED' else 1)

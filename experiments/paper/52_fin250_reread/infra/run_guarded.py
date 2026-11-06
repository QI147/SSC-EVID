"""I/O-only resume protection. Frozen inference sources stay unchanged."""
import argparse,hashlib,json,os,runpy,subprocess,sys,time
from pathlib import Path
ROOT=Path(r'<internal-root>')
sys.path.insert(0,str(ROOT))
import followup as F
ANNEX=ROOT/'runs/infra_resume_v2.json'

def install_io_retry(tag):
 original=os.replace;log=ROOT/'runs'/('io_retry_'+tag+'.jsonl')
 def replacement(src,dst,*args,**kwargs):
  for trial in range(101):
   try:return original(src,dst,*args,**kwargs)
   except PermissionError as exc:
    if not Path(dst).resolve().is_relative_to(ROOT) or trial==100:raise
    with log.open('a',encoding='utf-8') as f:f.write(json.dumps({'destination':str(dst),'trial':trial+1,'error':str(exc),'time':time.time()},ensure_ascii=False)+'\n')
    time.sleep(.05)
 os.replace=replacement

def check_annex():
 data=json.loads(ANNEX.read_text(encoding='utf-8'))
 if F.X.sha256_file(__file__)!=data['guard_sha256']:raise ValueError('I/O guard drift')
 if F.verify()['sha256']!=data['original_freeze']:raise ValueError('Original method freeze drift')

def worker(stage,shard):
 check_annex();install_io_retry(stage+'_s'+shard.split('/')[0]);F.run(stage,shard)

def main():
 ap=argparse.ArgumentParser();ap.add_argument('command',choices=['install','all','worker']);ap.add_argument('--stage',choices=list(F.STAGES));ap.add_argument('--shard');ap.add_argument('--authorize-paid',action='store_true');a=ap.parse_args()
 if a.command=='install':
  f=F.verify();stages={}
  for stage in F.STAGES:
   folder=ROOT/'runs'/stage;quota=folder/'quota.json';used=json.loads(quota.read_text(encoding='utf-8'))['used'] if quota.exists() else 0
   ledger=[r for p in folder.glob('*_calls.jsonl') for r in F.X.rows(p) if not r.get('reused')];done=[r for p in folder.glob('results_s*.jsonl') for r in F.X.rows(p)]
   stages[stage]={'quota_reserved':used,'logged_real_calls':len(ledger),'uncertain_reservations_before_resume':used-len(ledger),'completed_rows':len(done),'prior_result_hashes':{p.name:F.X.sha256_file(p) for p in folder.glob('results_s*.jsonl')}}
  F.X.dump(ANNEX,{'version':'io_only_resume_v2','original_freeze':f['sha256'],'guard_sha256':F.X.sha256_file(__file__),'change':'Retry Windows atomic replacement PermissionError; do not kill other workers when one fails, let in-flight requests drain. No method/prompt/score/budget changes.','budget_rule':'No quota reservations removed or refunded, including uncertain interrupted requests.','prior_state':stages},exclusive=True)
  print(json.dumps(stages));return
 if not a.authorize_paid:ap.error('--authorize-paid required')
 if a.command=='worker':worker(a.stage,a.shard);return
 check_annex();install_io_retry('parent')
 with (ROOT/'runs/orchestrator.lock').open('a+b') as lock:
  F.X.locked(lock)
  try:
   for stage in F.STAGES:
    folder=ROOT/'runs'/stage;folder.mkdir(parents=True,exist_ok=True);children=[];logs=[]
    try:
     for k in range(8):
      log=(folder/f'guarded_s{k}.log').open('a',encoding='utf-8');logs.append(log)
      children.append(subprocess.Popen([sys.executable,'-u',__file__,'worker','--stage',stage,'--shard',f'{k}/8','--authorize-paid'],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT))
     while any(p.poll() is None for p in children):
      progress=0
      for k in range(8):
       # Only read append-only progress logs, never atomic quota/result files.
       candidates=[]
       for name in (f'worker_s{k}.log',f'guarded_s{k}.log'):
        p=folder/name
        if not p.exists():continue
        with p.open(encoding='utf-8',errors='replace') as log:
         for line in log:
          if not line.startswith('{'):continue
          try:q=json.loads(line)
          except ValueError:continue
          if q.get('stage')==stage:candidates.append(q.get('completed',0))
       progress+=max(candidates,default=0)
      print(json.dumps({'stage':stage,'completed_logged':progress,'n':F.STAGES[stage]['n']}),flush=True);time.sleep(10)
     if any(p.returncode!=0 for p in children):raise RuntimeError('Worker failed; remaining workers drained safely; see guarded logs for '+stage)
    finally:
     # Normal worker errors do not kill peer requests. Explicit process interruption
     # still leaves reservations in quota, and is separately audited on next resume.
     for p in children:p.wait()
     for log in logs:log.close()
    F.merge(stage,8)
   runpy.run_path(str(ROOT/'score_followups.py'),run_name='__main__')
  finally:F.X.unlock(lock)
if __name__=='__main__':main()

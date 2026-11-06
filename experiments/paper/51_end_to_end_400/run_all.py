"""One entry point. --check-only never makes HTTP/model requests."""
import argparse,json,os,subprocess,sys,time
from pathlib import Path
from urllib.parse import urlparse
import experiment as X

HOST='example.invalid'
DATASETS=('numeric400','mixed400')

def command(*args):
 subprocess.run([sys.executable,*map(str,args)],cwd=X.ROOT,check=True)

def preflight():
 # Inspect presence and destination without printing credentials or making a request.
 env=X.API.load_env()
 if not env.get('BAILIAN_API_KEY'):raise ValueError('BAILIAN_API_KEY missing')
 url=urlparse(env.get('BAILIAN_BASE_URL',''))
 if url.scheme!='https' or url.hostname!=HOST or url.username or url.password:
  raise ValueError('API destination differs from reviewed HTTPS host')
 command('-m','unittest','-q','test_protocol')
 info={'state':'READY_NO_PAID_REQUESTS_FROM_PREFLIGHT','endpoint_host':HOST,'model':X.MODEL,
       'request_hard_cap_total':32800,'money_hard_cap':False,'datasets':{}}
 for d in DATASETS:
  p=X.ROOT/'runs'/d/'freeze.json'
  if not p.exists():X.freeze(d)
  f=X.verify(d)
  info['datasets'][d]={'n':400,'freeze':f['sha256'],'mode_caps':{m:400*sum(v.values()) for m,v in X.LIMITS.items()}}
 X.dump(X.ROOT/'preflight.json',info)
 print(json.dumps(info,ensure_ascii=False),flush=True)

def phase(dataset,mode,shards):
 fd=X.folder(dataset,mode);fd.mkdir(parents=True,exist_ok=True)
 if (fd/'quarantined.json').exists():raise RuntimeError(f'{dataset}/{mode} quarantined; preserve records and review before resuming')
 layout=fd/'sharding.json';spec={'shards':shards,'rule':'global runtime index modulo shards'}
 if layout.exists() and json.loads(layout.read_text())!=spec:raise ValueError('cannot change shard layout during resume')
 if not layout.exists():X.dump(layout,spec,exclusive=True)
 children=[];streams=[]
 try:
  for k in range(shards):
   stream=(fd/f'worker_s{k}.log').open('a',encoding='utf-8');streams.append(stream)
   children.append(subprocess.Popen([sys.executable,'-u','experiment.py','run','--dataset',dataset,'--mode',mode,'--shard',f'{k}/{shards}','--authorize-paid'],cwd=X.ROOT,stdout=stream,stderr=subprocess.STDOUT))
  last=0
  while any(p.poll() is None for p in children):
   failed=[p.returncode for p in children if p.poll() not in (None,0)]
   if failed:raise RuntimeError(f'{dataset}/{mode} worker failed; see worker logs. Cache and ledgers retained.')
   if time.monotonic()-last>30:
    counts=[len(X.rows(p)) for p in fd.glob('results_s*.jsonl')]
    print(json.dumps({'dataset':dataset,'mode':mode,'completed':sum(counts),'n':400}),flush=True);last=time.monotonic()
   time.sleep(1)
  if any(p.returncode!=0 for p in children):raise RuntimeError(f'{dataset}/{mode} worker failed; see worker logs')
 finally:
  for p in children:
   if p.poll() is None:p.terminate()
  for p in children:p.wait()
  for stream in streams:stream.close()
 X.merge(dataset,mode,shards)
 if mode=='initial':
  if (fd/'initial_freeze.json').exists():X.verify_initial(dataset)
  else:X.initial_freeze(dataset)

def main():
 ap=argparse.ArgumentParser();g=ap.add_mutually_exclusive_group(required=True)
 g.add_argument('--check-only',action='store_true');g.add_argument('--authorize-paid',action='store_true')
 ap.add_argument('--shards',type=int,default=8);a=ap.parse_args()
 if not 1<=a.shards<=8:ap.error('shards must be 1..8')
 (X.ROOT/'runs').mkdir(exist_ok=True)
 with (X.ROOT/'runs/orchestrator.lock').open('a+b') as lock:
  X.locked(lock)
  try:
   preflight()
   if a.check_only:return
   # Both common initial sets are frozen before any repair. Gold is used offline only.
   for mode in ('initial','e2e','repair'):
    for dataset in DATASETS:phase(dataset,mode,a.shards)
   for dataset in DATASETS:command('evaluate.py','--dataset',dataset)
   command('collect.py')
   node=os.environ.get('EVID_NODE_BIN');marker=os.environ.get('EVID_ARTIFACT_MARKER')
   if node and marker:
    subprocess.run([node,marker,'--operation-kind','create','--expected-output-count','1','--output-format','xlsx'],check=True)
    subprocess.run([node,str(X.ROOT/'build_workbook.mjs')],cwd=X.ROOT,check=True)
   print('COMPLETE: reports/结果摘要.md, *_all.csv, all_metrics.json; all original records in runs/',flush=True)
  finally:X.unlock(lock)
if __name__=='__main__':main()

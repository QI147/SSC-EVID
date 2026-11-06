import argparse,collections,json,os,subprocess,sys,time,unicodedata
from pathlib import Path
import followup as F
X=F.X;ROOT=F.ROOT
F.STAGES={d+'_pure':{'dataset':d,'n':n,'attempt_cap':16*n} for d,n in [('fin250',250),('mixed400',400),('numeric400',400)]}
F.transient=lambda r:False
PROMPT=X.DIRECT
def vote(values):
 valid=[v for v in values if v is not None]
 if not valid:return None
 keys=[' '.join(unicodedata.normalize('NFKC',v).casefold().split()) for v in valid];counts=collections.Counter(keys);winner=max(counts,key=lambda k:counts[k])
 return valid[keys.index(winner)]
def trajectory(item,budget,invoke):
 tokens=0;answers=[];points=[{'tokens':0,'answer':None,'step':0}];responses=[];unknown=False
 for i in range(16):
  r=invoke('RR_TOKEN',f'independent_read{i+1}','',PROMPT+'\nQuestion: '+item['question'],item['image']);responses.append(r);answers.append(X.text_answer(r));u=r.get('usage') or {}
  if 'prompt_tokens' not in u or 'completion_tokens' not in u:unknown=True
  else:tokens+=u['prompt_tokens']+u['completion_tokens']
  points.append({'tokens':tokens,'answer':vote(answers),'step':i+1})
  if tokens>=budget or unknown:break
 return {'target_tokens':budget,'actual_tokens':tokens,'reached':tokens>=budget,'usage_unknown':unknown,'answer':vote(answers),'before':[p for p in points if p['tokens']<=budget][-1],'after':next((p for p in points if p['tokens']>=budget),None),'answers':answers,'checkpoints':points,'responses':responses}
def io_retry():
 old=os.replace
 def wrapped(*args,**kw):
  for i in range(101):
   try:return old(*args,**kw)
   except PermissionError:
    if i==100:raise
    time.sleep(.05)
 os.replace=wrapped
def run(stage,shard):
 f=F.verify();io_retry();k,n=map(int,shard.split('/'));fd=ROOT/'runs'/stage;fd.mkdir(parents=True,exist_ok=True);p=fd/f'results_s{k}.jsonl';budget_path=ROOT/'datasets'/F.STAGES[stage]['dataset']/'pure_budget.json';budgets=json.loads(budget_path.read_text(encoding='utf-8'))
 with (fd/f's{k}.lock').open('a+b') as lock:
  X.locked(lock)
  try:
   prior=X.rows(p) if p.exists() else [];done={r['sample_id'] for r in prior}
   if len(done)!=len(prior) or any(r['freeze_sha256']!=f['sha256'] for r in prior):raise ValueError('Result mismatch')
   transport=F.Transport(stage,f'_s{k}',f)
   for idx,item in enumerate(X.rows(ROOT/'datasets'/F.STAGES[stage]['dataset']/'runtime.jsonl')):
    sid=item['sample_id']
    if idx%n!=k or sid in done:continue
    if X.sha256_file(budget_path)!=f['files'][budget_path.relative_to(ROOT).as_posix()]:raise ValueError('Budget drift')
    result=trajectory(item,budgets[sid],transport.for_item(item));prior.append({'sample_id':sid,'freeze_sha256':f['sha256'],**result});X.save_rows(p,prior)
    print(json.dumps({'stage':stage,'completed':len(prior)}),flush=True)
  finally:X.unlock(lock)
def all_stages():
 io_retry();(ROOT/'runs').mkdir(exist_ok=True)
 with (ROOT/'runs/parent.lock').open('a+b') as lock:
  X.locked(lock)
  try:
   for stage in F.STAGES:
    fd=ROOT/'runs'/stage;fd.mkdir(exist_ok=True);children=[];logs=[]
    for k in range(8):
     log=(fd/f'worker_s{k}.log').open('a',encoding='utf-8');logs.append(log);children.append(subprocess.Popen([sys.executable,'-u',__file__,'run','--stage',stage,'--shard',f'{k}/8','--authorize-paid'],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT))
    while any(p.poll() is None for p in children):
     total=0
     for k in range(8):
      count=0
      for line in (fd/f'worker_s{k}.log').read_text(encoding='utf-8',errors='replace').splitlines():
       try:count=max(count,json.loads(line).get('completed',0))
       except ValueError:pass
      total+=count
     print(json.dumps({'stage':stage,'completed':total,'n':F.STAGES[stage]['n']}),flush=True);time.sleep(10)
    for log in logs:log.close()
    if any(p.returncode!=0 for p in children):raise RuntimeError('Worker failed; other requests drained')
    F.merge(stage,8)
   subprocess.run([sys.executable,str(ROOT/'score_pure.py')],cwd=ROOT,check=True)
  finally:X.unlock(lock)
def main():
 ap=argparse.ArgumentParser();ap.add_argument('command',choices=['freeze','run','all']);ap.add_argument('--stage',choices=list(F.STAGES));ap.add_argument('--shard');ap.add_argument('--authorize-paid',action='store_true');a=ap.parse_args()
 if a.command=='freeze':X.dump(ROOT/'freeze.json',F.snapshot(),exclusive=True);print(F.verify()['sha256']);return
 if not a.authorize_paid:ap.error('--authorize-paid required')
 if a.command=='run':run(a.stage,a.shard)
 else:all_stages()
if __name__=='__main__':main()


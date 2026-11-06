"""Continue all and only budget-unreached cases, never select by correctness."""
import argparse,collections,copy,hashlib,json,os,subprocess,sys,time
from pathlib import Path
BASE=Path(r'<internal-root>')
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(BASE))
import pure as P
X=P.X
def load(p):return json.loads(Path(p).read_text(encoding='utf-8'))
def snapshot():
 original=P.F.verify();sources={s:X.sha256_file(BASE/'runs'/s/'results.jsonl') for s in P.F.STAGES}
 used=sum(load(BASE/'runs'/s/'quota.json')['used'] for s in P.F.STAGES)
 tasks={s:[r['sample_id'] for r in X.rows(BASE/'runs'/s/'results.jsonl') if not r['reached']] for s in P.F.STAGES}
 result={'base_freeze':original['sha256'],'base_results':sources,'base_reserved':used,'extra_attempt_cap':16800-used,'max_extra_per_case':256,'selection':'all budget-unreached cases, no gold or accuracy','tasks':tasks,'source_sha256':X.sha256_file(__file__),'protocol':'resource-only extension after original16-attempt run; same image/question/prompt/vote/model; all prior outputs preserved; no retries'}
 result['sha256']=X.canonical(result);return result
def verify():
 f=load(ROOT/'freeze.json')
 if f!=snapshot():raise ValueError('Extension/base drift')
 return f
def run(stage,shard):
 f=verify();P.io_retry();k,n=map(int,shard.split('/'));cfg=P.F.STAGES[stage];d=cfg['dataset'];fd=ROOT/'runs'/stage;fd.mkdir(parents=True,exist_ok=True);(ROOT/'runs/global').mkdir(exist_ok=True)
 q=X.Quota(ROOT/'runs/global',f['extra_attempt_cap']);original=X.rows(BASE/'runs'/stage/'results.jsonl');items={r['sample_id']:r for r in X.rows(BASE/'datasets'/d/'runtime.jsonl')};outpath=fd/f'results_s{k}.jsonl'
 with (fd/f's{k}.lock').open('a+b') as lock:
  X.locked(lock)
  try:
   prior=X.rows(outpath) if outpath.exists() else [];done={r['sample_id'] for r in prior}
   api=X.API.Caller(str(fd),{'per_sample':{'RR_TOKEN_EXT':256},'batch_total':f['extra_attempt_cap']},run_prefix='extend_'+f['sha256'][:10]+f'_s{k}',response_mode='text')
   from urllib.parse import urlparse
   url=urlparse(api.env['BAILIAN_BASE_URL'])
   if url.scheme!='https' or url.hostname!=P.F.HOST:raise ValueError('Unapproved endpoint')
   if (X.API.MODEL,X.API.TEMP,X.API.THINK,X.API.MAXTOK)!=('qwen3.8-flash',0,False,1200):raise ValueError('Model configuration drift')
   for idx,old in enumerate(original):
    sid=old['sample_id']
    if old['reached'] or idx%n!=k or sid in done:continue
    item=items[sid];record=copy.deepcopy(old);answers=record['answers'];points=record['checkpoints'];tokens=record['actual_tokens'];start=len(answers)
    for step in range(256):
     scope=f'{stage}:{sid}:extension:{step}';prompt='\n\n'+P.PROMPT+'\nQuestion: '+item['question'];image=BASE/item['image'];receipt=fd/'receipts'/sid/f'{step}.json'
     if receipt.exists():r=load(receipt)
     else:
      P.F.guard(load(BASE/'freeze.json'),item,d)
      if X.sha256_file(__file__)!=f['source_sha256']:raise ValueError('Extension code drift')
      fp=api._fingerprint(item['image_sha256'],prompt,scope)
      if fp not in api.cache:q.take()
      raw,meta=api.call('RR_TOKEN_EXT',sid,str(image),prompt,purpose=f'extension{step}',request_scope=scope)
      if meta.get('abort'):raise RuntimeError(meta['abort_reason'])
      cached=api.cache.get(fp,{})
      r={'status':meta.get('status'),'content':raw if isinstance(raw,str) else '',**{key:meta.get(key,cached.get(key)) for key in ('usage','finish_reason','call_id','error')}}
      X.dump(receipt,r)
     u=r.get('usage') or {};answers.append(X.text_answer(r));record['responses'].append(r)
     if 'prompt_tokens' not in u or 'completion_tokens' not in u:record['usage_unknown']=True;break
     tokens+=u['prompt_tokens']+u['completion_tokens'];points.append({'tokens':tokens,'answer':P.vote(answers),'step':start+step+1})
     if tokens>=record['target_tokens']:break
    record.update(actual_tokens=tokens,answer=P.vote(answers),reached=tokens>=record['target_tokens'],before=[p for p in points if p['tokens']<=record['target_tokens']][-1],after=next((p for p in points if p['tokens']>=record['target_tokens']),None),extension_freeze=f['sha256'])
    prior.append(record);X.save_rows(outpath,prior);print(json.dumps({'stage':stage,'extended':len(prior),'sample':sid,'reached':record['reached']}),flush=True)
  finally:X.unlock(lock)
def main():
 ap=argparse.ArgumentParser();ap.add_argument('command',choices=['freeze','all','run']);ap.add_argument('--stage');ap.add_argument('--shard');ap.add_argument('--authorize-paid',action='store_true');a=ap.parse_args()
 if a.command=='freeze':X.dump(ROOT/'freeze.json',snapshot(),exclusive=True);print(json.dumps(verify()));return
 if not a.authorize_paid:ap.error('--authorize-paid required')
 if a.command=='run':run(a.stage,a.shard);return
 verify();P.io_retry();(ROOT/'runs').mkdir(exist_ok=True)
 with (ROOT/'runs/parent.lock').open('a+b') as lock:
  X.locked(lock)
  try:
   for stage in P.F.STAGES:
    folder=ROOT/'runs'/stage;folder.mkdir(exist_ok=True);logs=[];children=[]
    for k in range(8):
     log=(folder/f's{k}.log').open('a',encoding='utf-8');logs.append(log);children.append(subprocess.Popen([sys.executable,'-u',__file__,'run','--stage',stage,'--shard',f'{k}/8','--authorize-paid'],stdout=log,stderr=subprocess.STDOUT))
    while any(p.poll() is None for p in children):print(json.dumps({'stage':stage,'extension_running':True}),flush=True);time.sleep(10)
    for log in logs:log.close()
    if any(p.returncode!=0 for p in children):raise RuntimeError('Extension worker failed; peers drained')
    changes=[r for p in folder.glob('results_s*.jsonl') for r in X.rows(p)];lookup={r['sample_id']:r for r in changes};original=X.rows(BASE/'runs'/stage/'results.jsonl');out=[lookup.get(r['sample_id'],r) for r in original]
    if len(lookup)!=len(changes) or set(lookup)!=set(load(ROOT/'freeze.json')['tasks'][stage]):raise ValueError('Extension task denominator mismatch')
    X.save_rows(folder/'results.jsonl',out);print(json.dumps({'stage':stage,'n':len(out),'reached':sum(r['reached'] for r in out),'extended':len(changes)}),flush=True)
  finally:X.unlock(lock)
if __name__=='__main__':main()

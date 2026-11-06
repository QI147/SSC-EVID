"""Authorized paper follow-ups. No scoring modules are imported during inference."""
import argparse,collections,copy,json,os,subprocess,sys,time
from pathlib import Path
from urllib.parse import urlparse
import experiment as X
ROOT=Path(__file__).resolve().parent
STAGES={'fin250':{'dataset':'fin250','n':250,'attempt_cap':2000},'numeric400_rr':{'dataset':'numeric400','n':400,'attempt_cap':6400},'mixed400_rr':{'dataset':'mixed400','n':400,'attempt_cap':6400}}
HOST='example.invalid'
RR_PROMPT=X.REREAD_JSON+' Use only the supplied original image, question and your own previous answer. Do not output a correctness verdict, checklist, or evidence ledger. Keep reasoning to two short sentences.'

def source_paths():
 return sorted(list(ROOT.glob('*.py'))+list((ROOT/'methods').rglob('*.py'))+list((ROOT/'vendor').rglob('*.py'))+list((ROOT/'upstream').rglob('*.py'))+[ROOT/'protocol.json',ROOT/'SOURCE_PROVENANCE.json'])
def snapshot():
 paths=source_paths()+[p for base in ('datasets','references','upstream') for p in (ROOT/base).rglob('*') if p.is_file() and p.suffix in ('.json','.jsonl')]
 for stage in STAGES.values():
  for r in X.rows(ROOT/'datasets'/stage['dataset']/'runtime.jsonl'):
   p=ROOT/r['image']
   if X.sha256_file(p)!=r['image_sha256']:raise ValueError('image drift '+r['sample_id'])
   paths.append(p)
 files={p.relative_to(ROOT).as_posix():X.sha256_file(p) for p in set(paths)}
 obj={'files':files,'model':X.MODEL,'stages':STAGES,'python':sys.version,'approved_host':HOST}
 obj['sha256']=X.canonical(obj);return obj
def verify():
 f=json.loads((ROOT/'freeze.json').read_text(encoding='utf-8'))
 if f!=snapshot():raise ValueError('Follow-up freeze drift; refuse requests')
 return f
def guard(f,item,dataset):
 critical=[p.relative_to(ROOT).as_posix() for p in source_paths()]+[item['image'],f'datasets/{dataset}/runtime.jsonl']
 if dataset!='fin250':critical += [f'datasets/{dataset}/rr_budget.json',f'references/{dataset}/common.jsonl']
 for rel in critical:
  if X.sha256_file(ROOT/rel)!=f['files'][rel]:raise ValueError('Dependency or current-image drift '+rel)

def response_from_record(r):
 return {'status':r['status'],'content':r.get('response_text') or '',**{k:r.get(k) for k in ('call_id','finish_reason','usage','cost_cny','error')},'requests':1,'reused':False}
def transient(r):
 err=str(r.get('error') or '').lower()
 return r.get('status')!='ok' and any(x in err for x in ('timeout','timed out','connection','429','500','502','503','504','ssl','tls','temporarily','remote end closed'))

class Transport:
 def __init__(self,stage,tag,f):
  self.stage,self.f,self.dataset=stage,f,STAGES[stage]['dataset'];self.dir=ROOT/'runs'/stage;self.dir.mkdir(parents=True,exist_ok=True)
  env=X.API.load_env();u=urlparse(env.get('BAILIAN_BASE_URL',''))
  if u.scheme!='https' or u.hostname!=HOST or not env.get('BAILIAN_API_KEY'):raise ValueError('Missing credential or unapproved destination')
  X.API.MODEL=X.MODEL['name'];X.API.TEMP=0;X.API.THINK=False;X.API.MAXTOK=1200;X.API.RETRY=0
  limits={'EVID_FACTS':3,'EVID_INIT':3,'EVID':12,'REREAD2':6} if stage=='fin250' else {'RR_TOKEN':16}
  self.api=X.API.Caller(str(self.dir),{'per_sample':limits,'batch_total':STAGES[stage]['attempt_cap']},run_prefix=stage+'_'+f['sha256'][:10]+tag,response_mode='text')
  self.quota=X.Quota(self.dir,STAGES[stage]['attempt_cap'])
  self.existing={r['request_scope']:r for r in X.rows(self.api.calls_path) if not r.get('reused')} if Path(self.api.calls_path).exists() else {}
 def for_item(self,item):
  count=collections.Counter();attempts=0
  def invoke(arm,phase,system,prompt,image=None):
   nonlocal attempts
   if 'scoring' in sys.modules or 'upstream.official_eval' in sys.modules:raise RuntimeError('Evaluator imported during inference')
   count[arm]+=1
   if self.stage=='fin250' and count[arm]>{'EVID_FACTS':1,'EVID_INIT':1,'EVID':4,'REREAD2':2}.get(arm,0):raise X.BudgetStop('Logical method quota')
   logical=f"{item['sample_id']}:{arm}:{phase}:{count[arm]}";full=(system or '')+'\n\n'+prompt
   receipt=self.dir/'receipts'/item['sample_id']/(arm+'_'+str(count[arm])+'.json')
   identity=X.canonical({'logical':logical,'prompt':full,'image':item['image_sha256'],'freeze':self.f['sha256']})
   if receipt.exists():
    old=json.loads(receipt.read_text(encoding='utf-8'))
    if old['identity']!=identity:raise ValueError('Receipt input drift')
    attempts+=len(old['attempts']);return old['response']
   records=[];response=None
   for retry in range(3):
    if self.stage!='fin250' and attempts>=16:
     if records:break
     raise X.BudgetStop('Per-sample 16 real-attempt cap')
    scope=logical+f':transport{retry}'
    if scope in self.existing:
     r=response_from_record(self.existing[scope]);attempts+=1
    else:
     guard(self.f,item,self.dataset)
     fp=self.api._fingerprint(item['image_sha256'],full,scope)
     if fp not in self.api.cache:self.quota.take()
     raw,meta=self.api.call(arm,item['sample_id'],str(ROOT/item['image']),full,purpose=phase,request_scope=scope)
     if meta.get('abort'):raise X.BudgetStop(meta['abort_reason'])
     cached=self.api.cache.get(fp,{})
     r={'status':meta.get('status'),'content':raw if isinstance(raw,str) else '',**{k:meta.get(k,cached.get(k)) for k in ('call_id','finish_reason','usage','cost_cny','error')},'requests':meta.get('requests',0),'reused':meta.get('reused',False)}
     if not r['reused']:attempts+=1
     # Restore full cost/usage when returning a cache hit after interruption.
     if r['reused']:r['usage']=cached.get('usage');r['cost_cny']=cached.get('original_cost_cny')
    records.append(r);response=r
    if r.get('status')!='ok' and any(code in str(r.get('error') or '') for code in ('401','403','404')):raise RuntimeError('Endpoint/auth failure; stop without further requests')
    if not transient(r) or retry==2:break
    time.sleep(2)
   response={**response,'transport_attempts':len(records),'transport_usage_unknown':any(not q.get('usage') for q in records)}
   X.dump(receipt,{'identity':identity,'attempts':records,'response':response})
   return response
  def used():return attempts
  invoke.attempts_used=used
  return invoke

def fin250(item,invoke):
 first=invoke('REREAD2','read1',X.DIRECT,'Question: '+item['question'],item['image']);a=X.text_answer(first)
 second=invoke('REREAD2','read2',X.REREAD,json.dumps({'question':item['question'],'previous_answer':a},ensure_ascii=False),item['image']);b=X.text_answer(second)
 ei,er=X.F36.initial(item,invoke)
 evid=X.E36.run(item,ei,invoke) if ei else {'answer':None,'status':'INITIAL_MISSING','calls':[]}
 return {'sample_id':item['sample_id'],'dataset':item['dataset'],'initials':{'REREAD2':{'answer':a},'EVID':ei},'arms':{'REREAD2':{'answer':b if b is not None else a,'status':'REREAD' if b is not None else 'FALLBACK_READ1','calls':[first,second]},'EVID':evid},'initial_calls':{'EVID':er}}

def reread(item,common,budget,invoke):
 target=budget['target_tokens'];checkpoints=[{'step':0,'tokens':0,'answer':(common or {}).get('answer'),'reasoning':(common or {}).get('reasoning','')}]
 if common is None:return {'sample_id':item['sample_id'],'target_tokens':target,'calls':[],'checkpoints':checkpoints,'before':checkpoints[0],'after':None,'answer':None,'reached':False,'status':'INITIAL_MISSING'}
 current=copy.deepcopy(common);tokens=0;calls=[];unknown_usage=False
 for step in range(1,17):
  if invoke.attempts_used()>=16:break
  data={'question':item['question'],'common_initial':common,'previous_answer':current['answer'],'previous_reasoning':current.get('reasoning','')}
  r=invoke('RR_TOKEN',f'reread{step}','',RR_PROMPT+'\n'+json.dumps(data,ensure_ascii=False),item['image']);calls.append(r)
  u=r.get('usage') or {}
  if 'prompt_tokens' not in u or 'completion_tokens' not in u:unknown_usage=True
  else:tokens+=u['prompt_tokens']+u['completion_tokens']
  if r.get('transport_usage_unknown'):unknown_usage=True
  candidate=X.parse_common(r)
  if candidate is not None:current=candidate
  checkpoints.append({'step':step,'tokens':tokens,**copy.deepcopy(current),'valid_new_answer':candidate is not None,'finish_reason':r.get('finish_reason')})
  if tokens>=target or unknown_usage:break
 before=[x for x in checkpoints if x['tokens']<=target][-1]
 after=next((x for x in checkpoints if x['tokens']>=target),None)
 return {'sample_id':item['sample_id'],'target_tokens':target,'common_sha256':X.canonical(common),'calls':calls,'checkpoints':checkpoints,'before':before,'after':after,'answer':current['answer'],'known_tokens':tokens,'reached':after is not None,'usage_unknown':unknown_usage,'attempts':invoke.attempts_used(),'status':'BUDGET_REACHED' if after else ('USAGE_UNKNOWN' if unknown_usage else 'ATTEMPT_CAP')}

def run(stage,shard):
 f=verify();dataset=STAGES[stage]['dataset'];items=X.rows(ROOT/'datasets'/dataset/'runtime.jsonl');k,n=map(int,shard.split('/'))
 if not 0<=k<n:raise ValueError('Invalid shard')
 fd=ROOT/'runs'/stage;fd.mkdir(parents=True,exist_ok=True);p=fd/f'results_s{k}.jsonl'
 shared={r['sample_id']:r['initial'] for r in X.rows(ROOT/'references'/dataset/'common.jsonl')} if stage!='fin250' else {}
 budgets=json.loads((ROOT/'datasets'/dataset/'rr_budget.json').read_text(encoding='utf-8')) if stage!='fin250' else {}
 with (fd/f'worker_s{k}.lock').open('a+b') as lock:
  X.locked(lock)
  try:
   prior=X.rows(p) if p.exists() else [];done={r['sample_id'] for r in prior}
   if len(done)!=len(prior) or any(r['freeze_sha256']!=f['sha256'] for r in prior):raise ValueError('Duplicate/cross-version prior rows')
   transport=Transport(stage,f'_s{k}',f)
   for idx,item in enumerate(items):
    if idx%n!=k or item['sample_id'] in done:continue
    inv=transport.for_item(item)
    out=fin250(item,inv) if stage=='fin250' else reread(item,shared[item['sample_id']],budgets[item['sample_id']],inv)
    out['freeze_sha256']=f['sha256'];prior.append(out);X.save_rows(p,prior)
    print(json.dumps({'stage':stage,'shard':shard,'completed':len(prior),'last':item['sample_id']}),flush=True)
  finally:X.unlock(lock)

def merge(stage,n):
 f=verify();items=X.rows(ROOT/'datasets'/STAGES[stage]['dataset']/'runtime.jsonl');order={r['sample_id']:i for i,r in enumerate(items)};fd=ROOT/'runs'/stage
 out=[r for k in range(n) for r in X.rows(fd/f'results_s{k}.jsonl')]
 if len(out)!=len(items) or {r['sample_id'] for r in out}!=set(order) or len({r['sample_id'] for r in out})!=len(items) or any(r['freeze_sha256']!=f['sha256'] for r in out):raise ValueError('Incomplete/duplicate/cross-freeze merge')
 out.sort(key=lambda r:order[r['sample_id']]);X.save_rows(fd/'results.jsonl',out)
 print(json.dumps({'stage':stage,'merged':len(out)}),flush=True)

def main():
 ap=argparse.ArgumentParser();ap.add_argument('command',choices=['freeze','verify','run','merge','all']);ap.add_argument('--stage',choices=list(STAGES));ap.add_argument('--shard',default='0/8');ap.add_argument('--shards',type=int,default=8);ap.add_argument('--authorize-paid',action='store_true');a=ap.parse_args()
 if a.command=='freeze':X.dump(ROOT/'freeze.json',snapshot(),exclusive=True);print(verify()['sha256']);return
 if a.command=='verify':print(json.dumps({'sha256':verify()['sha256'],'drift':[]}));return
 if a.command=='merge':merge(a.stage,a.shards);return
 if not a.authorize_paid:ap.error('--authorize-paid required')
 if a.command=='run':run(a.stage,a.shard);return
 (ROOT/'runs').mkdir(exist_ok=True)
 with (ROOT/'runs/orchestrator.lock').open('a+b') as lock:
  X.locked(lock)
  try:
   for stage in STAGES:
    fd=ROOT/'runs'/stage;fd.mkdir(exist_ok=True)
    children=[];logs=[]
    try:
     for k in range(a.shards):
      log=(fd/f'worker_s{k}.log').open('a',encoding='utf-8');logs.append(log)
      children.append(subprocess.Popen([sys.executable,'-u',str(ROOT/'followup.py'),'run','--stage',stage,'--shard',f'{k}/{a.shards}','--authorize-paid'],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT))
     while any(p.poll() is None for p in children):
      if any(p.poll() not in (None,0) for p in children):raise RuntimeError('Worker failed: '+stage+'; see logs')
      print(json.dumps({'stage':stage,'completed':sum(len(X.rows(p)) for p in fd.glob('results_s*.jsonl')),'n':STAGES[stage]['n']}),flush=True);time.sleep(10)
     if any(p.returncode!=0 for p in children):raise RuntimeError('Worker failed: '+stage)
    finally:
     for p in children:
      if p.poll() is None:p.terminate()
     for p in children:p.wait()
     for log in logs:log.close()
    merge(stage,a.shards)
   subprocess.run([sys.executable,str(ROOT/'score_followups.py')],cwd=ROOT,check=True)
  finally:X.unlock(lock)
if __name__=='__main__':main()

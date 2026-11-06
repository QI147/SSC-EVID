"""Frozen end-to-end and fixed-initial repair trials. No evaluator imported in run modes."""
import argparse,collections,copy,hashlib,json,os,sys,time
from pathlib import Path
import caller as API
from common import sha256_file
import ssc_adapter as SSC
from methods.evid36 import evid_integrated as E36,fact_first as F36
from methods.evid50 import evid_integrated as E50,fact_first as F50

ROOT=Path(__file__).resolve().parent
MODEL={'name':'qwen3.8-flash','temperature':0,'thinking':False,'max_tokens':1200,'retry':0}
LIMITS={'e2e':{'DIRECT':1,'REREAD2':2,'SSC_INIT':1,'SSC':2,'EVID_FACTS':1,'EVID_INIT':1,'EVID':4},
 'initial':{'COMMON':1},'repair':{'FIX_SSC':4,'FIX_EVID':8,'FIX_E50':8,'RR_MATCH':8}}
COMMON='''Answer the image question once. Return JSON {"answer":"final answer with units/names","reasoning":"brief reasoning, at most two sentences"}. Do not review, self-check, correct a prior answer or output evidence records. If impossible answer:null and explain briefly. No Markdown.'''
DIRECT='Answer the question using only the supplied table image. Output only the final answer, including the unit when applicable. Do not output reasoning, evidence records, or a self-review.'
REREAD='Read the ORIGINAL image and question again. The previous answer may be wrong. Independently redo the requested reading/computation and output only your final answer with units/names. No correctness verdict, checklist or evidence record.'
REREAD_JSON=REREAD+' Return JSON {"answer":"...","reasoning":"brief basis for this answer"}, no Markdown.'
class BudgetStop(RuntimeError):pass
def rows(p):return [json.loads(s) for s in Path(p).read_text(encoding='utf-8').splitlines() if s.strip()]
def dump(p,obj,exclusive=False):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
 if exclusive:
  with p.open('x',encoding='utf-8') as f:json.dump(obj,f,ensure_ascii=False,indent=1)
 else:
  tmp=p.with_name(p.name+'.'+str(os.getpid())+'.tmp');tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=1),encoding='utf-8');os.replace(tmp,p)
def save_rows(p,records):
 p=Path(p);tmp=p.with_name(p.name+'.'+str(os.getpid())+'.tmp')
 tmp.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in records),encoding='utf-8');os.replace(tmp,p)
def canonical(obj):return hashlib.sha256(json.dumps(obj,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
def locked(f):
 if os.name=='nt':
  import msvcrt
  for _ in range(3000):
   try:f.seek(0);msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1);return
   except OSError:time.sleep(.002)
  raise BudgetStop('quota lock timeout')
 import fcntl;fcntl.flock(f,fcntl.LOCK_EX)
def unlock(f):
 if os.name=='nt':
  import msvcrt;f.seek(0);msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
 else:
  import fcntl;fcntl.flock(f,fcntl.LOCK_UN)
class Quota:
 # 每次 take 都 open 锁文件时，多进程并发会间歇性 EACCES（实测 5 次挂 1 次）。
 # 只对"打开"这一步加重试，事务语义与原实现完全一致；用完即关，避免占用临时目录。
 def __init__(self,folder,cap):self.folder=Path(folder);self.cap=cap
 def _open(self):
  last=None
  for _ in range(600):
   try:return (self.folder/'quota.lock').open('a+b')
   except PermissionError as exc:last=exc;time.sleep(.01)
  raise BudgetStop('quota lock unavailable: %s'%last)
 def take(self):
  f=self._open()
  try:
   locked(f)
   try:
    p=self.folder/'quota.json';used=json.loads(p.read_text())['used'] if p.exists() else 0
    if used>=self.cap:raise BudgetStop('batch request cap reached')
    dump(p,{'used':used+1,'cap':self.cap})
   finally:unlock(f)
  finally:f.close()
def dataset_path(dataset):return ROOT/'datasets'/dataset
def folder(dataset,mode):return ROOT/'runs'/dataset/mode
def source_files():
 return sorted(list(ROOT.glob('*.py'))+list((ROOT/'methods').rglob('*.py'))+list((ROOT/'vendor').rglob('*.py'))+list(ROOT.glob('*.mjs'))+list(ROOT.glob('*.cmd'))+[ROOT/'SOURCE_PROVENANCE.json',ROOT/'protocol.json',ROOT/'REBUILD_SPEC.json'])
def snapshot(dataset):
 rt=rows(dataset_path(dataset)/'runtime.jsonl')
 if len(rt)!=400 or len({r['sample_id'] for r in rt})!=400:raise ValueError('denominator or duplicate')
 files={p.relative_to(ROOT).as_posix():sha256_file(p) for p in source_files()}
 for p in list(dataset_path(dataset).glob('*.json'))+list(dataset_path(dataset).glob('*.jsonl')):
  files[p.relative_to(ROOT).as_posix()]=sha256_file(p)
 p=ROOT/'datasets/naming_and_overlap.json';files[p.relative_to(ROOT).as_posix()]=sha256_file(p)
 for r in rt:
  p=ROOT/r['image'];h=sha256_file(p)
  if h!=r['image_sha256']:raise ValueError('image drift '+r['sample_id'])
  files[r['image']]=h
 obj={'dataset':dataset,'n':400,'model':MODEL,'limits':LIMITS,'files':files,'python':sys.version,
      'protocol':'e2e independent initials; shared frozen answer+reason; max2 repairs; model judgment separate from structural outcome'}
 obj['sha256']=canonical(obj);return obj
def freeze(dataset):dump(ROOT/'runs'/dataset/'freeze.json',snapshot(dataset),exclusive=True)
def verify(dataset):
 p=ROOT/'runs'/dataset/'freeze.json';f=json.loads(p.read_text(encoding='utf-8'))
 if f!=snapshot(dataset):raise ValueError('freeze drift: refuse requests')
 return f
def initial_path(dataset):return folder(dataset,'initial')/'results.jsonl'
def initial_freeze(dataset):
 f=verify(dataset);p=initial_path(dataset);r=rows(p)
 expected={x['sample_id'] for x in rows(dataset_path(dataset)/'runtime.jsonl')}
 if len(r)!=400 or {x['sample_id'] for x in r}!=expected or any(x['freeze_sha256']!=f['sha256'] for x in r):raise ValueError('initial incomplete or wrong cohort')
 dump(folder(dataset,'initial')/'initial_freeze.json',{'master_freeze':f['sha256'],'results_sha256':sha256_file(p),'n':400},exclusive=True)
def verify_initial(dataset):
 p=folder(dataset,'initial')/'initial_freeze.json';f=json.loads(p.read_text())
 if f['results_sha256']!=sha256_file(initial_path(dataset)):raise ValueError('common initial drift')
 if f['master_freeze']!=json.loads((ROOT/'runs'/dataset/'freeze.json').read_text())['sha256']:raise ValueError('initial parent mismatch')
 return f
class Invoke:
 def __init__(self,dataset,mode,tag,freeze):
  self.dataset,self.mode,self.freeze=dataset,mode,freeze;self.dir=folder(dataset,mode);self.dir.mkdir(parents=True,exist_ok=True)
  limits=LIMITS[mode];cap=400*sum(limits.values());self.quota=Quota(self.dir,cap)
  API.MODEL,API.TEMP,API.THINK,API.MAXTOK,API.RETRY=MODEL['name'],0,False,1200,0
  self.caller=API.Caller(str(self.dir),{'per_sample':limits,'per_arm_total':{a:400*v for a,v in limits.items()},'batch_total':cap},
    run_prefix=mode+'_'+freeze['sha256'][:10]+tag,response_mode='text')
 def for_item(self,item):
  count=collections.Counter();image=ROOT/item['image'];sid=item['sample_id']
  def invoke(arm,phase,system,prompt,_image=None):
   if 'scoring' in sys.modules:raise RuntimeError('evaluator must not run in model process')
   for rel,h in self.freeze['files'].items():
    if (rel.endswith(('.py','.json','.jsonl')) or rel==item['image']) and sha256_file(ROOT/rel)!=h:raise ValueError('runtime dependency/image drift '+rel)
   if self.mode=='repair':verify_initial(self.dataset)
   count[arm]+=1
   if count[arm]>LIMITS[self.mode].get(arm,0):raise BudgetStop('per-item role limit '+arm)
   full=(system or '')+'\n\n'+prompt;scope=f'{sid}:{arm}:{phase}:{count[arm]}'
   fp=self.caller._fingerprint(item['image_sha256'],full,scope)
   if fp not in self.caller.cache:self.quota.take()
   raw,meta=self.caller.call(arm,sid,str(image),full,purpose=phase,request_scope=scope)
   if meta.get('abort'):raise BudgetStop(meta.get('abort_reason','role quota'))
   err=str(meta.get('error') or '').lower()
   if meta.get('status')!='ok' and any(s in err for s in ('429','quota','rate limit','timed out','connection','ssl','tls')):raise BudgetStop('infrastructure interruption; preserve cache')
   return {'status':meta.get('status'),'content':raw if isinstance(raw,str) else '',
           **{k:meta.get(k) for k in ('call_id','finish_reason','usage','cost_cny','requests','reused','error')}}
  return invoke
def parse_common(resp):
 obj=E36.parse(resp)
 if not obj or not E36.valid_answer(obj.get('answer')):return None
 return {'answer':obj['answer'],'reasoning':obj.get('reasoning') if isinstance(obj.get('reasoning'),str) else ''}
def text_answer(resp):return resp.get('content','').strip() or None if resp.get('status')=='ok' and resp.get('finish_reason')=='stop' else None
def call_json(item,invoke,arm,phase,prompt,data):
 resp=invoke(arm,phase,'',prompt+'\n'+json.dumps(data,ensure_ascii=False),item['image'])
 return parse_common(resp),resp
def ssc_initial(item,invoke):
 prompts,_=SSC.modules();benchmark='faketab' if item['dataset'] in ('fintabnetqa','finqa_rendered') else 'wikitq'
 system,user=prompts.get_prompts(benchmark,'step1');resp=invoke('SSC_INIT','initial',system,user.format(table='[See the supplied table image.]',question=item['question']),item['image'])
 return SSC.parse_initial(resp),resp
def run_e2e(item,invoke):
 direct=invoke('DIRECT','answer_once',DIRECT,'Question: '+item['question'],item['image']);d=text_answer(direct)
 first=invoke('REREAD2','read1',DIRECT,'Question: '+item['question'],item['image']);a=text_answer(first)
 second=invoke('REREAD2','read2',REREAD,json.dumps({'question':item['question'],'previous_answer':a},ensure_ascii=False),item['image']);b=text_answer(second)
 initial,ssc_resp=ssc_initial(item,invoke)
 ssc=SSC.finish(item,initial,invoke) if initial else {'answer':None,'status':'INITIAL_MISSING','calls':[]}
 ei,erecord=F36.initial(item,invoke);evid=E36.run(item,ei,invoke) if ei else {'answer':None,'status':'INITIAL_MISSING','calls':[]}
 return {'sample_id':item['sample_id'],'dataset':item['dataset'],'initials':{'DIRECT':{'answer':d},'REREAD2':{'answer':a},'SSC':initial,'EVID':ei},
  'arms':{'DIRECT':{'answer':d,'status':'ANSWERED' if d is not None else 'UNSUBMITTED','calls':[direct]},
          'REREAD2':{'answer':b if b is not None else a,'status':'REREAD' if b is not None else 'FALLBACK_READ1','calls':[first,second]},'SSC':ssc,'EVID':evid},
  'initial_calls':{'SSC':ssc_resp,'EVID':erecord}}
def remap_evidence(initial,prefix):
 out=copy.deepcopy(initial);pool=out.get('evidence',[]);mapping={r['id']:prefix+r['id'] for r in pool}
 for r in pool:
  r['id']=mapping[r['id']]
  for k in ('operand_ids','supersedes','contradicts','superseded_by','disputed_by'):
   if k in r:r[k]=[mapping.get(x,x) for x in r[k]]
 if 'supporting_ids' in out:out['supporting_ids']=[mapping.get(x,x) for x in out['supporting_ids']]
 return out
def carry(previous,out,prefix):
 x=copy.deepcopy(previous);x['answer']=out['answer']
 if out.get('status')=='CORRECTED_EVIDENCE_SUPPORTED':x['reasoning']=(out.get('repair') or {}).get('reasoning') or (out.get('repair') or {}).get('analysis') or x.get('reasoning','')
 versions=out.get('evidence_versions',[])
 x['evidence']=versions[-1]['evidence'] if versions else out.get('initial_evidence',x.get('evidence',[]))
 x['scope']=(out.get('final_check') or {}).get('scope') or (out.get('repair') or {}).get('scope') or x.get('scope','OPEN')
 x['unresolved']=list(out.get('unresolved',[]))
 if out.get('requirements_versions'):x['question_plan']={'needs':list(out['requirements_versions'][-1]['needs'].values())}
 return remap_evidence(x,prefix)
def verdict(out,ssc=False):
 if ssc:return (out.get('events') or [{}])[0].get('verdict','parse_error')
 return (out.get('check') or {}).get('verdict','parse_error')
def repairs(item,common,invoke):
 frozen_common=canonical(common);arms={};budgets=[]
 if common is None:return {'sample_id':item['sample_id'],'dataset':item['dataset'],'common':None,'arms':{a:{'answer':None,'rounds':[],'status':'INITIAL_MISSING'} for a in ('KEEP','SSC','EVID','RR_MATCH','EVID50')},'shared_unchanged':True}
 for name,method,role in (('SSC',None,'FIX_SSC'),('EVID',E36,'FIX_EVID'),('EVID50',E50,'FIX_E50')):
  current=copy.deepcopy(common);record=[];stopped=False
  for rd in (1,2):
   if stopped:
    record.append({'round':rd,'executed':False,'answer':current['answer'],'judgment':'not_executed','reasoning':current.get('reasoning',''),'status':'EARLY_STOP_CARRIED_FORWARD','calls':0});continue
   used=[]
   def wrapped(_arm,phase,system,prompt,image=None):
    if len(used)>= (2 if name=='SSC' else 4):raise BudgetStop('round request limit')
    r=invoke(role,f'r{rd}.'+phase,system,prompt,image);used.append(r);return r
   before=current['answer'];out=SSC.finish(item,current,wrapped) if name=='SSC' else method.run(item,current,wrapped)
   ans=out.get('answer');j=verdict(out,name=='SSC')
   record.append({'round':rd,'executed':True,'answer':ans,'judgment':j,'status':out['status'],'calls':len(used),'trace':out})
   if name=='SSC':
    analysis=(out.get('events') or [{}])[0].get('error_analysis','')
    rationale=current.get('reasoning','') if ans==before else 'No revised reasoning emitted by the author protocol. Prior error analysis (not a new rationale): '+analysis
    current={'answer':ans,'reasoning':rationale}
    stopped=j=='correct' and ans==before
   else:
    current=carry(current,out,f'prev{rd}_');stopped=out['status'] in ('KEPT_EVIDENCE_SUPPORTED','KEPT_AFTER_ACQUISITION','KEPT_AFTER_PROTOCOL_REVIEW') and ans==before
   if not E36.valid_answer(ans):stopped=True
  arms[name]={'answer':current['answer'],'rounds':record}
  if name=='EVID':budgets=[r['calls'] for r in record]
 current=copy.deepcopy(common);rec=[]
 for rd,k in enumerate(budgets,1):
  for i in range(k):
   candidate,resp=call_json(item,invoke,'RR_MATCH',f'r{rd}.reread{i+1}',REREAD_JSON,{'question':item['question'],'previous_answer':current['answer'],'previous_reasoning':current.get('reasoning','')})
   if candidate is not None:current=candidate
  rec.append({'round':rd,'executed':k>0,'answer':current['answer'],'reasoning':current.get('reasoning',''),'judgment':'not_applicable','status':'BUDGET_MATCHED_REREAD','calls':k})
 arms['RR_MATCH']={'answer':current['answer'],'rounds':rec,'matched_to':'EVID logical unique calls per round; no answer/label transfer'}
 arms['KEEP']={'answer':common['answer'],'rounds':[{'round':r,'answer':common['answer'],'executed':False,'judgment':'not_applicable','calls':0,'status':'KEEP_INITIAL'} for r in (1,2)]}
 if canonical(common)!=frozen_common:raise RuntimeError('shared initial mutated')
 return {'sample_id':item['sample_id'],'dataset':item['dataset'],'common':common,'common_sha256':frozen_common,'arms':arms,'shared_unchanged':True}
def run(dataset,mode,shard):
 fz=verify(dataset);rt=rows(dataset_path(dataset)/'runtime.jsonl');fd=folder(dataset,mode);fd.mkdir(parents=True,exist_ok=True)
 if (fd/'quarantined.json').exists():raise ValueError('quarantined run')
 k,n=map(int,shard.split('/'));tag=f'_s{k}';p=fd/('results'+tag+'.jsonl')
 if not 0<=k<n:raise ValueError('bad shard')
 originals={}
 if mode=='repair':verify_initial(dataset);originals={r['sample_id']:r['initial'] for r in rows(initial_path(dataset))}
 with (fd/('run'+tag+'.lock')).open('a+b') as lock:
  locked(lock)
  try:
   output=rows(p) if p.exists() else [];done={r['sample_id'] for r in output}
   if len(output)!=len(done) or any(r['freeze_sha256']!=fz['sha256'] for r in output):raise ValueError('mixed or duplicate result')
   transport=Invoke(dataset,mode,tag,fz)
   for idx,item in enumerate(rt):
    if idx%n!=k or item['sample_id'] in done:continue
    try:
     invoke=transport.for_item(item)
     if mode=='initial':initial,resp=call_json(item,invoke,'COMMON','generate_shared',COMMON,{'question':item['question']});rec={'sample_id':item['sample_id'],'dataset':item['dataset'],'initial':initial,'response':resp}
     elif mode=='e2e':rec=run_e2e(item,invoke)
     else:rec=repairs(item,copy.deepcopy(originals[item['sample_id']]),invoke)
    except BudgetStop as ex:print(json.dumps({'stopped':str(ex),'sample_id':item['sample_id']}),flush=True);raise
    except Exception as ex:dump(fd/'quarantined.json',{'sample_id':item['sample_id'],'error':type(ex).__name__+':'+str(ex)},exclusive=True);raise
    rec['freeze_sha256']=fz['sha256'];output.append(rec);save_rows(p,output)
   print(json.dumps({'mode':mode,'dataset':dataset,'shard':shard,'completed':len(output)}),flush=True)
  finally:unlock(lock)
def merge(dataset,mode,shards):
 f=verify(dataset);fd=folder(dataset,mode);rt=rows(dataset_path(dataset)/'runtime.jsonl');order={r['sample_id']:i for i,r in enumerate(rt)}
 out=[r for k in range(shards) for r in rows(fd/f'results_s{k}.jsonl')]
 if len(out)!=400 or len({r['sample_id'] for r in out})!=400 or any(r['sample_id'] not in order or r['freeze_sha256']!=f['sha256'] for r in out):raise ValueError('incomplete/duplicate/cross-version merge')
 out.sort(key=lambda r:order[r['sample_id']]);save_rows(fd/'results.jsonl',out)
 print(json.dumps({'merged':len(out),'mode':mode,'dataset':dataset}))
def main():
 ap=argparse.ArgumentParser();ap.add_argument('command',choices=['budget','freeze','verify','run','merge','freeze-initial']);ap.add_argument('--dataset',choices=['numeric400','mixed400'],required=True);ap.add_argument('--mode',choices=list(LIMITS),default='e2e');ap.add_argument('--shard',default='0/1');ap.add_argument('--shards',type=int,default=8);ap.add_argument('--authorize-paid',action='store_true');a=ap.parse_args()
 if a.command=='budget':print(json.dumps({'dataset':a.dataset,'caps':{m:400*sum(v.values()) for m,v in LIMITS.items()},'total_per_dataset':16400,'money_hard_cap':False}))
 elif a.command=='freeze':freeze(a.dataset);print(json.dumps({'sha256':verify(a.dataset)['sha256']}))
 elif a.command=='verify':print(json.dumps({'sha256':verify(a.dataset)['sha256'],'drift':[]}))
 elif a.command=='freeze-initial':initial_freeze(a.dataset)
 elif a.command=='merge':merge(a.dataset,a.mode,a.shards)
 elif a.command=='run':
  if not a.authorize_paid:ap.error('explicit --authorize-paid required')
  run(a.dataset,a.mode,a.shard)
if __name__=='__main__':main()

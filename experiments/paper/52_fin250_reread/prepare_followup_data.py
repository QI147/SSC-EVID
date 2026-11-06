"""Full-split native FinTabNetQA and read-only copies of existing repair inputs."""
import collections,hashlib,json,re,shutil
from pathlib import Path
import pyarrow.parquet as pq
import experiment as X
OLD=Path(r'<internal-root>')
PROJECT=OLD.parents[1]
ROOT=Path(__file__).resolve().parent
def sha(p):return X.sha256_file(p)
def main():
 if (ROOT/'datasets').exists():raise FileExistsError('Refuse overwrite datasets')
 sources=[]
 for d in ('numeric400','mixed400'):
  # Verify original version before copying it; no original run or ledger is edited.
  import importlib.util,sys
  spec=importlib.util.spec_from_file_location('old_frozen_experiment',OLD/'experiment.py');old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
  old.verify(d);old.verify_initial(d)
  shutil.copytree(OLD/'datasets'/d,ROOT/'datasets'/d)
  ref=ROOT/'references'/d;ref.mkdir(parents=True)
  for original,name in ((OLD/'runs'/d/'initial/results.jsonl','common.jsonl'),(OLD/'runs'/d/'initial/initial_freeze.json','initial_freeze.json'),(OLD/'runs'/d/'repair2/results.jsonl','repair2_results.jsonl'),(OLD/'runs'/d/'repair2/freeze_v2.json','repair2_freeze.json'),(OLD/'runs'/d/'freeze.json','master_freeze.json'),(OLD/'reports'/d/'metrics.json','old_metrics.json')):
   shutil.copyfile(original,ref/name);sources.append({'original':str(original),'copy':str((ref/name).relative_to(ROOT)),'sha256':sha(original)})
  calls=[r for p in (OLD/'runs'/d/'repair2').glob('*_calls.jsonl') for r in X.rows(p)]
  totals=collections.defaultdict(lambda:{'input_tokens':0,'output_tokens':0,'requests':0})
  for r in calls:
   if r.get('reused') or r['arm']!='FIX_EVID':continue
   u=r.get('usage') or {}
   if 'prompt_tokens' not in u or 'completion_tokens' not in u:raise ValueError('Target EVID usage missing')
   t=totals[r['sid']];t['input_tokens']+=u['prompt_tokens'];t['output_tokens']+=u['completion_tokens'];t['requests']+=1
  common={r['sample_id']:r['initial'] for r in X.rows(ref/'common.jsonl')}
  budget={}
  for r in X.rows(ROOT/'datasets'/d/'runtime.jsonl'):
   sid=r['sample_id'];t=totals[sid];budget[sid]={**t,'target_tokens':t['input_tokens']+t['output_tokens']}
   if common[sid] is not None and not budget[sid]['target_tokens']:raise ValueError('Missing positive target')
  X.dump(ROOT/'datasets'/d/'rr_budget.json',budget)
 raw=PROJECT/'07_基线数据与重构/原始资料/TableVQA数据/fintabnetqa-00000-of-00001-c337fe9eb7a70460.parquet'
 data=pq.read_table(raw).to_pylist();assert len(data)==250 and len({r['qa_id'] for r in data})==250
 rt=[];gold=[];dest=ROOT/'datasets/fin250';(dest/'images').mkdir(parents=True)
 for i,r in enumerate(data):
  sid=f'FTB{i+1:03d}';p=dest/'images'/(sid+'.jpg');p.write_bytes(r['image']['bytes'])
  group=hashlib.sha256(re.sub(r'\s+',' ',r.get('text_html_table') or '').strip().encode()).hexdigest()
  rt.append({'sample_id':sid,'dataset':'fintabnetqa','question':r['question'],'image':p.relative_to(ROOT).as_posix(),'image_sha256':sha(p),'source':{'qa_id':r['qa_id'],'pool':'TableVQA-Bench','shard':'fintabnetqa'},'source_group':group,'source_group_basis':'normalized_table_html_sha256','order':i})
 X.save_rows(dest/'runtime.jsonl',rt)
 # Selection is the entire input split and is saved before the evaluation projection.
 for item,r in zip(rt,data):
  answer=r['gt'];answer=answer[0] if isinstance(answer,list) and answer else answer
  try:exe=float(str(answer).replace(',',''))
  except (TypeError,ValueError):exe=None
  gold.append({'sample_id':item['sample_id'],'qa_id':r['qa_id'],'answer':answer,'exe_ans':exe})
 X.save_rows(dest/'eval.jsonl',gold)
 X.dump(dest/'manifest.json',{'n':250,'selection':'all upstream split; no score-based filter','source_sha256':sha(raw),'runtime_sha256':sha(dest/'runtime.jsonl'),'eval_sha256':sha(dest/'eval.jsonl'),'tables':len({r['source_group'] for r in rt}),'images':'native bytes unchanged'})
 X.dump(ROOT/'references/provenance.json',{'read_only_reused':sources,'source_parquet':str(raw),'source_sha256':sha(raw),'method':'51 directory evid36 bytes copied unchanged','notes':'No old model request or gold file used to select examples'})
 print(json.dumps({'fin250':250,'rr_cohorts':[400,400],'new_paid_calls':0}))
if __name__=='__main__':main()

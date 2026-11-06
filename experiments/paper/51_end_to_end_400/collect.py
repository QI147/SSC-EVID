"""Collect original per-sample tables; Holm primary comparisons only."""
import json,csv,collections
from pathlib import Path
import experiment as X
from evaluate import write_csv,usage,ledger
ROOT=X.ROOT
def main():
 reports={d:json.loads((ROOT/'reports'/d/'metrics.json').read_text(encoding='utf-8')) for d in ('numeric400','mixed400')}
 prim=[(d,k,v['mcnemar_raw']) for d,r in reports.items() for k,v in r['comparisons'].items() if k.startswith('primary_')]
 adjusted={};last=0
 for rank,(d,k,p) in enumerate(sorted(prim,key=lambda x:x[2])):
  last=max(last,min(1,p*(len(prim)-rank)));adjusted[d+':'+k]=last
 for d,r in reports.items():
  for k,v in r['comparisons'].items():
   if d+':'+k in adjusted:v['holm_primary_p']=adjusted[d+':'+k]
 all_tables={key:[] for key in ('summary','end_to_end','repair_rounds')};samples=[];calls=[]
 for d in reports:
  t=json.loads((ROOT/'reports'/d/'tables.json').read_text(encoding='utf-8'))
  for key in all_tables:all_tables[key]+=t[key]
  samples.extend(dict(cohort=d,**r) for r in X.rows(X.dataset_path(d)/'runtime.jsonl'))
  calls.extend(dict(cohort=d,mode=m,**r) for m in ('initial','e2e','repair') for r in ledger(d,m))
 out=ROOT/'reports';X.dump(out/'all_metrics.json',{'datasets':reports,'total_costs':usage(calls),'n_executions':800,'unique_qa_ids':len({r['sample_id'] for r in samples}),'overlap':json.loads((ROOT/'datasets/naming_and_overlap.json').read_text(encoding='utf-8'))})
 for key,values in all_tables.items():write_csv(out/(key+'_all.csv'),values)
 write_csv(out/'samples_all.csv',samples);write_csv(out/'calls_full_all.csv',calls)
 compact_calls=[{k:r.get(k) for k in ('cohort','mode','sid','arm','purpose','call_id','status','finish_reason','reused','usage','cost_cny_estimated','cost_cny','latency_s','image_sha256')} for r in calls]
 X.dump(out/'workbook_data.json',{**all_tables,'samples':samples,'calls':compact_calls})
 lines=['# 数字400与混合400实验结果','', '固定分母400；开发实验。共同初态费用单列；金额为token费率估算。','', '|题集|方法|初态正确|最终正确|准确率|错→对|对→错|对→未提交|','|---|---|---:|---:|---:|---:|---:|---:|']
 for d,r in reports.items():
  for arm,s in r['e2e'].items():lines.append(f"|{d}|{arm}|{s['initial_counts'].get('C',0)}|{s['final_counts'].get('C',0)}|{s['accuracy']:.2%}|{s['corrected']}|{s['corrupted']}|{s['correct_lost_to_unsubmitted']}|")
 lines+=['','修复实验各轮、检错指标、完整转移矩阵、分来源结果与显著性见 all_metrics.json；逐题结果和完整调用保留在CSV及runs。']
 (out/'结果摘要.md').write_text('\n'.join(lines),encoding='utf-8')
 print(json.dumps({'requests':usage(calls)['requests'],'cost_cny_estimated':usage(calls)['cost_cny_estimated'],'sample_executions':800,'unique_qa_ids':len({r['sample_id'] for r in samples})}))
if __name__=='__main__':main()

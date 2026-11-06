"""Offline scoring. Keeps official benchmark and historical scoring distinct."""
import collections,csv,hashlib,json,statistics
from pathlib import Path
import followup as F
import evaluate as E
from upstream import official_eval as O
ROOT=F.ROOT
def official(answer,gt):
 p,ps=O.fintabnet_normalize(str(answer) if answer is not None else 'none');g,gs=O.fintabnet_normalize(str(gt))
 return {'relieved':int(any(x==y for x in ps for y in gs)) if answer is not None else 0,'exact':int(p==g) if answer is not None else 0}
def entries(stage):return [r for p in (ROOT/'runs'/stage).glob('*_calls.jsonl') for r in F.X.rows(p)]
def holm(comparisons):
 last=0
 for rank,(key,v) in enumerate(sorted(comparisons.items(),key=lambda x:x[1]['mcnemar_raw'])):
  last=max(last,min(1,v['mcnemar_raw']*(len(comparisons)-rank)));v['holm_p']=last
def summarize_fin():
 rt={r['sample_id']:r for r in F.X.rows(ROOT/'datasets/fin250/runtime.jsonl')};gold={r['sample_id']:r for r in F.X.rows(ROOT/'datasets/fin250/eval.jsonl')}
 outputs=F.X.rows(ROOT/'runs/fin250/results.jsonl');assert len(outputs)==len(rt)==250 and len({r['sample_id'] for r in outputs})==250
 log=entries('fin250');report={'n':250,'metrics':'official relieved + official exact; historical display/exec separately','arms':{},'literature_reference':{},'new_call_usage':E.usage(log)};rows=[];labels={}
 roles={'REREAD2':['REREAD2'],'EVID':['EVID_FACTS','EVID_INIT','EVID']}
 for arm in roles:
  scores=[];truth={};hist={};initial_truth={};per=[]
  for r in outputs:
   sid=r['sample_id'];answer=r['arms'][arm].get('answer');score=official(answer,gold[sid]['answer']);scores.append(score)
   truth[sid]='A' if answer is None else 'C' if score['relieved'] else 'W';hist[sid]=E.label(sid,answer,gold)
   initial=(r['initials'][arm] or {}).get('answer');initial_score=official(initial,gold[sid]['answer']);initial_truth[sid]='A' if initial is None else 'C' if initial_score['relieved'] else 'W'
   rows.append({'sample_id':sid,'qa_id':rt[sid]['source']['qa_id'],'question':rt[sid]['question'],'arm':arm,'initial_answer':initial,'answer':answer,'gold':gold[sid]['answer'],'official_relief':score['relieved'],'official_exact':score['exact'],'historical_label':hist[sid],'status':r['arms'][arm]['status']})
  report['arms'][arm]={'official_correct':sum(x['relieved'] for x in scores),'official_accuracy':sum(x['relieved'] for x in scores)/250,'official_exact_correct':sum(x['exact'] for x in scores),'historical_counts':dict(collections.Counter(hist.values())),'official_transition':E.transitions(initial_truth,truth),'cost':E.usage([r for r in log if r['arm'] in roles[arm]])};labels[arm]=truth
 report['paired_EVID_minus_REREAD2']=E.paired(labels['REREAD2'],labels['EVID'],rt)
 original=json.loads((ROOT/'upstream/gpt4v_official_evaluated.json').read_text(encoding='utf-8'));ref=original['instances']
 assert len(ref)==250 and {x['qa_id'] for x in ref}=={r['source']['qa_id'] for r in rt.values()}
 expected=original['evaluation_meta']['average_scores'];calc=[official(r['pred'],r['gt']) for r in ref]
 rc=sum(r['relieved'] for r in calc);ec=sum(r['exact'] for r in calc)
 if abs(rc/250*100-expected[0])>1e-6 or abs(ec/250*100-expected[1])>1e-6:raise ValueError('Official reference score regression')
 report['literature_reference']['GPT4V_upstream']={'n':250,'correct':rc,'accuracy':rc/250,'exact_correct':ec,'kind':'official historical released predictions; not rerun; different backbone/time','source':'https://github.com/naver-ai/tablevqabench/tree/master/outputs/gpt4v_evaluated'}
 return report,rows
def summarize_rr(dataset):
 stage=dataset+'_rr';rt={r['sample_id']:r for r in F.X.rows(ROOT/'datasets'/dataset/'runtime.jsonl')};gold={r['sample_id']:r for r in F.X.rows(ROOT/'datasets'/dataset/'eval.jsonl')}
 commons={r['sample_id']:r['initial'] for r in F.X.rows(ROOT/'references'/dataset/'common.jsonl')};old={r['sample_id']:r for r in F.X.rows(ROOT/'references'/dataset/'repair2_results.jsonl')}
 new=F.X.rows(ROOT/'runs'/stage/'results.jsonl');assert len(new)==len(rt)==400 and {r['sample_id'] for r in new}==set(rt)
 initial={sid:E.label(sid,(v or {}).get('answer'),gold) for sid,v in commons.items()};labels={a:{} for a in ('SSC','EVID','RR_LEQ','RR_GEQ','RR_FINAL')};long=[];sources=collections.defaultdict(list);deviations=[]
 for r in new:
  sid=r['sample_id'];original=commons[sid]
  if original is not None and r['common_sha256']!=F.X.canonical(original):raise ValueError('Common mismatch')
  for arm in ('SSC','EVID'):
   out=old[sid]['arms'][arm];labels[arm][sid]=E.label(sid,out['answer'],gold)
  variants={'RR_LEQ':r['before']['answer'],'RR_GEQ':r['after']['answer'] if r['after'] else r['answer'],'RR_FINAL':r['answer']}
  for arm,answer in variants.items():labels[arm][sid]=E.label(sid,answer,gold)
  if r['target_tokens']>0 and r.get('reached'):deviations.append((r['known_tokens']-r['target_tokens'])/r['target_tokens'])
  sources[rt[sid]['dataset']].append(sid)
  long.append({'dataset':dataset,'source':rt[sid]['dataset'],'sample_id':sid,'common_answer':(original or {}).get('answer'),'common_label':initial[sid],'SSC_answer':old[sid]['arms']['SSC']['answer'],'SSC_label':labels['SSC'][sid],'EVID_answer':old[sid]['arms']['EVID']['answer'],'EVID_label':labels['EVID'][sid],**variants,'RR_LEQ_label':labels['RR_LEQ'][sid],'RR_GEQ_label':labels['RR_GEQ'][sid],'RR_FINAL_label':labels['RR_FINAL'][sid],'reached':r['reached'],'target_tokens':r['target_tokens'],'actual_known_tokens':r.get('known_tokens',0),'attempts':r.get('attempts',0),'status':r['status'],'checkpoints':r['checkpoints']})
 eligible=sum(v is not None for v in commons.values());reached=sum(r['reached'] for r in new if commons[r['sample_id']] is not None)
 report={'n':400,'common_initial':dict(collections.Counter(initial.values())),'arms':{a:E.transitions(initial,v) for a,v in labels.items()},'budget':{'eligible_common':eligible,'reached':reached,'reached_fraction':reached/eligible,'target_token_total':sum(r['target_tokens'] for r in new),'actual_known_token_total':sum(r.get('known_tokens',0) for r in new),'median_relative_overshoot_reached':statistics.median(deviations) if deviations else None,'p90_relative_overshoot_reached':sorted(deviations)[int(.9*(len(deviations)-1))] if deviations else None,'max_relative_overshoot_reached':max(deviations) if deviations else None,'usage_unknown_rows':sum(r.get('usage_unknown',False) for r in new)},'new_call_usage':E.usage(entries(stage)),'by_source':{source:{a:E.transitions({sid:initial[sid] for sid in ids},{sid:v[sid] for sid in ids}) for a,v in labels.items()} for source,ids in sources.items()},'comparison_note':'RR_GEQ uses capped last state on unreached rows and is not an all-rows >=budget policy unless reached_fraction=1; RR_FINAL identical to RR_GEQ except unavailable initial'}
 report['EVID_minus_RR_GEQ']=E.paired(labels['RR_GEQ'],labels['EVID'],rt)
 return report,long
def main():
 f=F.verify();out=ROOT/'reports';out.mkdir(exist_ok=True);a,arows=summarize_fin();rr={};rrows=[]
 for d in ('numeric400','mixed400'):
  rr[d],rows=summarize_rr(d);rrows+=rows
 comparisons={d:r['EVID_minus_RR_GEQ'] for d,r in rr.items()};holm(comparisons)
 allcalls=[dict(stage=s,**r) for s in F.STAGES for r in entries(s)]
 results={'freeze_sha256':f['sha256'],'fin250':a,'repair_reread':rr,'new_followup_comparisons':comparisons,'total_new_usage':E.usage(allcalls),'raw_result_sha256':{s:F.X.sha256_file(ROOT/'runs'/s/'results.jsonl') for s in F.STAGES}}
 F.X.dump(out/'results.json',results);E.write_csv(out/'fin250_predictions.csv',arows);E.write_csv(out/'rr_predictions.csv',rrows);E.write_csv(out/'calls_full.csv',allcalls)
 print(json.dumps({'fin250':{a:v['official_correct'] for a,v in a['arms'].items()},'rr':{d:{a:v['final_counts'] for a,v in r['arms'].items()} for d,r in rr.items()},'usage':results['total_new_usage']},ensure_ascii=False))
if __name__=='__main__':main()

"""Offline only: fixed denominators, UNKNOWN separate, shared-cost accounting."""
import argparse,collections,csv,json,math,random
from pathlib import Path
import experiment as X
import scoring
ROOT=X.ROOT
def label(sid,answer,gold):
 if answer is None or str(answer).strip()=='':return 'A'
 result=scoring.eval_one(sid,answer,gold[sid])
 return 'C' if result['display_match'] or result['exec_match'] else 'W'
def ratio(a,b):return a/b if b else None
def mcnemar(gain,loss):
 n=gain+loss
 return min(1.,2*sum(math.comb(n,k) for k in range(min(gain,loss)+1))/(2**n)) if n else 1.
def paired(a,b,runtime):
 ids=list(runtime);gain=sum(a[s]!='C' and b[s]=='C' for s in ids);loss=sum(a[s]=='C' and b[s]!='C' for s in ids)
 groups=collections.defaultdict(lambda:[0,0])
 for s in ids:groups[(runtime[s]['dataset'],runtime[s]['source_group'])][0]+=int(b[s]=='C')-int(a[s]=='C');groups[(runtime[s]['dataset'],runtime[s]['source_group'])][1]+=1
 strata=collections.defaultdict(list)
 for (source,group),values in groups.items():strata[source].append(values)
 rng=random.Random(20261030);boot=[]
 for _ in range(2000):
  drawn=[rng.choice(g) for g in strata.values() for _ in g];boot.append(sum(x[0] for x in drawn)/sum(x[1] for x in drawn))
 boot.sort()
 return {'n':len(ids),'gained':gain,'lost':loss,'net':gain-loss,'mcnemar_raw':mcnemar(gain,loss),'source_table_bootstrap95':[boot[50],boot[1949]]}
def ledger(dataset,mode):return [r for p in X.folder(dataset,mode).glob('*_calls.jsonl') for r in X.rows(p)]
def usage(entries):
 unique=[r for r in entries if not r.get('reused')]
 missing=sum('prompt_tokens' not in (r.get('usage') or {}) or 'completion_tokens' not in (r.get('usage') or {}) for r in unique)
 known=round(sum(float(r.get('cost_cny') or 0) for r in unique),6)
 return {'requests':len(unique),'input_tokens':sum((r.get('usage') or {}).get('prompt_tokens',0) for r in unique),
  'output_tokens':sum((r.get('usage') or {}).get('completion_tokens',0) for r in unique),
  'cost_cny_estimated':None if missing else known,'known_cost_subtotal_cny':known,'usage_missing_requests':missing,
  'finish':dict(collections.Counter(r.get('finish_reason') for r in unique)),
  'http_status':dict(collections.Counter(r.get('status') for r in unique))}
def transitions(initial,final):
 matrix=collections.Counter(initial[s]+'->'+final[s] for s in initial);nc=sum(v=='C' for v in initial.values());nw=sum(v=='W' for v in initial.values())
 fixed=len(initial);g=matrix['W->C'];cw=matrix['C->W'];ca=matrix['C->A']
 return {'n':fixed,'initial_counts':dict(collections.Counter(initial.values())),'final_counts':dict(collections.Counter(final.values())),
  'matrix':dict(matrix),'accuracy':sum(v=='C' for v in final.values())/fixed,
  'corrected':g,'corrupted':cw,'correct_lost_to_unsubmitted':ca,'repair_rate':ratio(g,nw),'corruption_rate':ratio(cw,nc),
  'correct_loss_rate':ratio(cw+ca,nc),'correct_retention_rate':ratio(matrix['C->C'],nc),'net_repair_per_all':(g-cw-ca)/fixed}
def detection(starts,records):
 c=collections.Counter();allcw=sum(x in ('C','W') for x in starts.values());allw=sum(x=='W' for x in starts.values())
 for sid,r in records.items():
  if starts[sid]=='A':c['initial_unsubmitted']+=1;continue
  if not r.get('executed'):c['not_executed']+=1;continue
  j=r.get('judgment')
  if j in ('correct','incorrect'):
   c['classified']+=1
   if j=='incorrect':c['TP' if starts[sid]=='W' else 'FP']+=1
   else:c['TN' if starts[sid]=='C' else 'FN']+=1
  elif j=='unknown':c['unknown']+=1
  elif j=='not_applicable':c['no_detection_protocol']+=1
  else:c['parse_or_method_failure']+=1
 return {**dict(c),'error_recall':ratio(c['TP'],allw),'detection_precision':ratio(c['TP'],c['TP']+c['FP']),
  'selective_accuracy':ratio(c['TP']+c['TN'],c['classified']),'coverage':ratio(c['classified'],allcw),
  'unknown_rate':ratio(c['unknown'],allcw),'note':'UNKNOWN/unexecuted not classified as right/wrong; recall denominator all current W'}
def write_csv(path,records):
 if not records:return
 fields=list(dict.fromkeys(k for r in records for k in r))
 with path.open('w',newline='',encoding='utf-8-sig') as f:
  writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
  for r in records:writer.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in r.items()})
def evaluate(dataset):
 X.verify(dataset);X.verify_initial(dataset);rt={r['sample_id']:r for r in X.rows(X.dataset_path(dataset)/'runtime.jsonl')}
 gold={r['sample_id']:r for r in X.rows(X.dataset_path(dataset)/'eval.jsonl')}
 init=X.rows(X.initial_path(dataset));e2e=X.rows(X.folder(dataset,'e2e')/'results.jsonl');repair=X.rows(X.folder(dataset,'repair')/'results.jsonl')
 for records in (init,e2e,repair):
  if len(records)!=400 or {r['sample_id'] for r in records}!=set(rt):raise ValueError('incomplete or mismatched denominator')
 commons={r['sample_id']:r['initial'] for r in init};initial_labels={sid:label(sid,(v or {}).get('answer'),gold) for sid,v in commons.items()}
 common_cost=usage(ledger(dataset,'initial'));e2elog=ledger(dataset,'e2e');fixlog=ledger(dataset,'repair')
 report={'dataset':dataset,'n':400,'common_initial':dict(collections.Counter(initial_labels.values())),'common_cost':common_cost,'e2e':{},'repair':{},'comparisons':{},'scope':'development; main natural errors only; shared initial costs not summed across arms'}
 end_rows=[];fix_rows=[];labels_e2e={};labels_fix={};summary=[]
 rolemap={'DIRECT':['DIRECT'],'REREAD2':['REREAD2'],'SSC':['SSC_INIT','SSC'],'EVID':['EVID_FACTS','EVID_INIT','EVID']}
 for arm,roles in rolemap.items():
  final={};first={}
  for r in e2e:
   sid=r['sample_id'];out=r['arms'][arm];first[sid]=label(sid,(r['initials'][arm] or {}).get('answer'),gold);final[sid]=label(sid,out.get('answer'),gold)
   end_rows.append({'dataset':dataset,'source':rt[sid]['dataset'],'sample_id':sid,'question':rt[sid]['question'],'arm':arm,
     'initial_answer':(r['initials'][arm] or {}).get('answer'),'answer':out.get('answer'),'initial_label':first[sid],'label':final[sid],
     'status':out['status'],'trace_file':str(X.folder(dataset,'e2e')/'results.jsonl')})
  cost=usage([r for r in e2elog if r['arm'] in roles]);s=transitions(first,final);s['cost']=cost
  s['by_source']={source:dict(collections.Counter(final[sid] for sid in rt if rt[sid]['dataset']==source)) for source in {r['dataset'] for r in rt.values()}}
  report['e2e'][arm]=s;labels_e2e[arm]=final
  summary.append({'dataset':dataset,'experiment':'end_to_end','arm':arm,'round':0,**{k:v for k,v in s.items() if not isinstance(v,dict)},**{k:v for k,v in cost.items() if not isinstance(v,dict)}})
 for arm,role in [('KEEP',None),('SSC','FIX_SSC'),('EVID','FIX_EVID'),('RR_MATCH','RR_MATCH'),('EVID50','FIX_E50')]:
  rounds={};previous=initial_labels
  for rd in (1,2):
   current={};roundrecs={}
   for r in repair:
    sid=r['sample_id'];common=commons[sid]
    if r.get('common_sha256') and r['common_sha256']!=X.canonical(common):raise ValueError('common mismatch')
    if not r['shared_unchanged']:raise ValueError('shared initial modified')
    records=r['arms'][arm].get('rounds',[]);rec=records[rd-1] if len(records)>=rd else {'answer':None,'executed':False,'judgment':'not_executed','status':'INITIAL_MISSING','calls':0}
    current[sid]=label(sid,rec.get('answer'),gold);roundrecs[sid]=rec
    trace=rec.get('trace',{});proposal=(trace.get('repair') or {}).get('answer')
    fix_rows.append({'dataset':dataset,'source':rt[sid]['dataset'],'sample_id':sid,'question':rt[sid]['question'],'arm':arm,'round':rd,
      'common_answer':(common or {}).get('answer'),'common_reasoning':(common or {}).get('reasoning'),'answer':rec.get('answer'),
      'initial_label':initial_labels[sid],'round_start_label':previous[sid],'label':current[sid],'executed':rec.get('executed'),
      'model_judgment':rec.get('judgment'),'status':rec['status'],'calls':rec.get('calls',0),'proposal':proposal,
      'proposal_label':label(sid,proposal,gold) if proposal is not None else None,'unresolved':trace.get('unresolved',[]),
      'error_type':(trace.get('check') or (trace.get('events') or [{}])[0]).get('error_type'),
      'trace_file':str(X.folder(dataset,'repair')/'results.jsonl')})
   stat=transitions(initial_labels,current);stat['increment_from_previous_round']=transitions(previous,current)
   stat['detection_current_round']=detection(previous,roundrecs)
   stat['changed_wrong_to_wrong']=sum(initial_labels[sid]=='W' and current[sid]=='W' and str(roundrecs[sid].get('answer'))!=str((commons[sid] or {}).get('answer')) for sid in rt)
   cost=usage([r for r in fixlog if r['arm']==role and r['purpose'].startswith(f'r{rd}.')]) if role else usage([])
   stat['exclusive_round_cost']=cost;rounds['r'+str(rd)]=stat
   summary.append({'dataset':dataset,'experiment':'shared_initial_repair','arm':arm,'round':rd,
     **{k:v for k,v in stat.items() if not isinstance(v,dict)},
     **{k:v for k,v in stat['detection_current_round'].items() if not isinstance(v,(dict,list))},
     **{k:v for k,v in cost.items() if not isinstance(v,dict)}})
   previous=current
  report['repair'][arm]=rounds;labels_fix[arm]=previous
 report['comparisons']['primary_e2e_EVID_minus_SSC']=paired(labels_e2e['SSC'],labels_e2e['EVID'],rt)
 report['comparisons']['primary_repair_r2_EVID_minus_SSC']=paired(labels_fix['SSC'],labels_fix['EVID'],rt)
 report['comparisons']['repair_r2_EVID_minus_RR_MATCH']=paired(labels_fix['RR_MATCH'],labels_fix['EVID'],rt)
 report['comparisons']['repair_r2_EVID50_minus_EVID']=paired(labels_fix['EVID'],labels_fix['EVID50'],rt)
 output=ROOT/'reports'/dataset;output.mkdir(parents=True,exist_ok=True)
 report['input_freeze']={'master':X.verify(dataset)['sha256'],'results':{m:X.sha256_file(X.folder(dataset,m)/'results.jsonl') for m in ('initial','e2e','repair')}}
 X.dump(output/'metrics.json',report);write_csv(output/'summary.csv',summary);write_csv(output/'end_to_end.csv',end_rows);write_csv(output/'repair_rounds.csv',fix_rows)
 allcalls=[dict(dataset=dataset,mode=mode,**r) for mode in ('initial','e2e','repair') for r in ledger(dataset,mode)]
 write_csv(output/'calls_full.csv',allcalls)
 X.dump(output/'tables.json',{'summary':summary,'end_to_end':end_rows,'repair_rounds':fix_rows})
 print(json.dumps({'dataset':dataset,'common_initial':report['common_initial'],'e2e':{a:v['final_counts'] for a,v in report['e2e'].items()},'repair_r2':{a:v['r2']['final_counts'] for a,v in report['repair'].items()}},ensure_ascii=False))
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('--dataset',choices=['numeric400','mixed400'],required=True);a=ap.parse_args();evaluate(a.dataset)

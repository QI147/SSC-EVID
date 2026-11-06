import collections,json,statistics
import pure as P
import evaluate as E
from upstream import official_eval as O
def label_official(answer,reference):
 if answer is None:return 'A'
 _,pred=O.fintabnet_normalize(answer);_,gold=O.fintabnet_normalize(reference)
 return 'C' if any(x==y for x in pred for y in gold) else 'W'
def main():
 f=P.F.verify();report={};long=[];calls=[]
 for stage,cfg in P.F.STAGES.items():
  d=cfg['dataset'];rt={r['sample_id']:r for r in P.X.rows(P.ROOT/'datasets'/d/'runtime.jsonl')};gold={r['sample_id']:r for r in P.X.rows(P.ROOT/'datasets'/d/'eval.jsonl')};old={r['sample_id']:r for r in P.X.rows(P.ROOT/'references'/d/'e2e_reference.jsonl')};new=P.X.rows(P.ROOT/'runs'/stage/'results.jsonl');assert len(new)==len(rt) and {r['sample_id'] for r in new}==set(rt)
  labels={a:{} for a in ('EVID','VOTE_LEQ','VOTE_GEQ')};history={a:{} for a in labels}
  for r in new:
   sid=r['sample_id'];answers={'EVID':old[sid]['arms']['EVID']['answer'],'VOTE_LEQ':r['before']['answer'],'VOTE_GEQ':r['answer']}
   for a,v in answers.items():
    history[a][sid]=E.label(sid,v,gold);labels[a][sid]=label_official(v,gold[sid]['answer']) if d=='fin250' else history[a][sid]
   long.append({'dataset':d,'sample_id':sid,**answers,**{a+'_label':v[sid] for a,v in labels.items()},'target_tokens':r['target_tokens'],'actual_tokens':r['actual_tokens'],'reached':r['reached'],'unique_valid_answers':len(set(v for v in r['answers'] if v)),'all_answers':r['answers']})
  logs=[dict(dataset=d,**r) for p in (P.ROOT/'runs'/stage).glob('*_calls.jsonl') for r in P.X.rows(p)];calls+=logs
  report[d]={'n':len(rt),'counts':{a:dict(collections.Counter(v.values())) for a,v in labels.items()},'historical_counts':{a:dict(collections.Counter(v.values())) for a,v in history.items()},'comparison':E.paired(labels['VOTE_GEQ'],labels['EVID'],rt),'budget':{'target':sum(r['target_tokens'] for r in new),'actual':sum(r['actual_tokens'] for r in new),'reached':sum(r['reached'] for r in new),'median_overshoot':statistics.median((r['actual_tokens']-r['target_tokens'])/r['target_tokens'] for r in new if r['reached'])},'usage':E.usage(logs)}
 last=0
 for rank,(d,r) in enumerate(sorted(report.items(),key=lambda x:x[1]['comparison']['mcnemar_raw'])):
  last=max(last,min(1,r['comparison']['mcnemar_raw']*(3-rank)));r['comparison']['holm_p']=last
 out=P.ROOT/'reports';out.mkdir(exist_ok=True);P.X.dump(out/'results.json',{'freeze':f['sha256'],'datasets':report,'usage':E.usage(calls)});E.write_csv(out/'per_sample.csv',long);E.write_csv(out/'calls_full.csv',calls);print(json.dumps(report,ensure_ascii=False))
if __name__=='__main__':main()

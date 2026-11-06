import collections,json,shutil
from pathlib import Path
import experiment as X
ROOT=Path(__file__).resolve().parent
OLD=Path(r'<internal-root>')
def main():
 provenance=[]
 for d in ('fin250','mixed400','numeric400'):
  base=OLD/('52_论文实验一与预算重读' if d=='fin250' else '51_SSC_EVID_400统一对照');folder=base/'runs'/('fin250' if d=='fin250' else d+'/e2e')
  calls=[r for p in folder.glob('*_calls.jsonl') for r in X.rows(p) if not r.get('reused') and r['arm'] in ('EVID_FACTS','EVID_INIT','EVID')]
  budget=collections.Counter()
  for r in calls:
   u=r.get('usage') or {};assert 'prompt_tokens' in u and 'completion_tokens' in u;budget[r['sid']]+=u['prompt_tokens']+u['completion_tokens']
  rt=X.rows(ROOT/'datasets'/d/'runtime.jsonl');assert set(budget)=={r['sample_id'] for r in rt}
  X.dump(ROOT/'datasets'/d/'pure_budget.json',dict(budget))
  dest=ROOT/'references'/d;dest.mkdir(parents=True,exist_ok=True);shutil.copyfile(folder/'results.jsonl',dest/'e2e_reference.jsonl')
  provenance.append({'dataset':d,'baseline':str(folder/'results.jsonl'),'sha256':X.sha256_file(folder/'results.jsonl'),'target_tokens':sum(budget.values()),'n':len(rt)})
 X.dump(ROOT/'budget_provenance.json',provenance);print(json.dumps(provenance,ensure_ascii=False))
if __name__=='__main__':main()

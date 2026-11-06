import copy,hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import followup as F

class FollowupTests(unittest.TestCase):
 def test01_real_caller_timeout_resume_is_auditable(self):
  class Response:
   def __enter__(self):return self
   def __exit__(self,*args):pass
   def read(self):return json.dumps({'model':'qwen3.8-flash','choices':[{'message':{'content':'7'},'finish_reason':'stop'}],'usage':{'prompt_tokens':100,'completion_tokens':2}}).encode()
  with tempfile.TemporaryDirectory() as d,patch.object(F,'ROOT',Path(d)),patch.object(F.X.API,'load_env',return_value={'BAILIAN_BASE_URL':'https://'+F.HOST+'/v1','BAILIAN_API_KEY':'test-only'}),patch.object(F,'guard'),patch.object(F.time,'sleep'):
   image=Path(d)/'x.jpg';image.write_bytes(b'image')
   item={'sample_id':'q','image':'x.jpg','image_sha256':F.X.sha256_file(image)};fz={'sha256':'f'}
   with patch.object(F.X.API.urllib.request,'urlopen',side_effect=[TimeoutError('timed out'),Response()]) as http:
    t=F.Transport('numeric400_rr','_s0',fz);r=t.for_item(item)('RR_TOKEN','reread1','','q')
    self.assertEqual(http.call_count,2);self.assertEqual(r['content'],'7');self.assertTrue(r['transport_usage_unknown'])
   records=F.X.rows(t.api.calls_path);self.assertEqual([r['status'] for r in records],['error','ok']);self.assertIsNone(records[0]['cost_cny'])
   with patch.object(F.X.API.urllib.request,'urlopen',side_effect=AssertionError('must not make another HTTP request')):
    r2=F.Transport('numeric400_rr','_s0',fz).for_item(item)('RR_TOKEN','reread1','','q');self.assertEqual(r,r2)
   quota=json.loads((Path(d)/'runs/numeric400_rr/quota.json').read_text());self.assertEqual(quota['used'],2)
 def test02_data_fixed_and_method_unchanged(self):
  old=Path(r'<internal-root>')
  for filename in ('fact_first.py','evid_integrated.py','fact_protocol.py'):
   self.assertEqual((F.ROOT/'methods/evid36'/filename).read_bytes(),(old/'methods/evid36'/filename).read_bytes())
  for dataset,n in (('fin250',250),('numeric400',400),('mixed400',400)):
   rows=F.X.rows(F.ROOT/'datasets'/dataset/'runtime.jsonl');self.assertEqual(len(rows),n);self.assertEqual(len({r['sample_id'] for r in rows}),n)
   for r in rows:
    self.assertFalse({'answer','gt','exe_ans','gold'} & set(r));self.assertEqual(F.X.sha256_file(F.ROOT/r['image']),r['image_sha256'])
 def test03_target_budgets(self):
  totals=[]
  for d in ('numeric400','mixed400'):
   budget=json.loads((F.ROOT/'datasets'/d/'rr_budget.json').read_text(encoding='utf-8'));self.assertEqual(len(budget),400);totals.append(sum(x['target_tokens'] for x in budget.values()))
  self.assertEqual(totals,[1678621,1010867])
 def test04_reread_budget_brackets_and_no_foreign_answer(self):
  used=[];initial={'answer':'old','reasoning':'common'};before=copy.deepcopy(initial)
  def inv(arm,phase,system,prompt,image):
   used.append(prompt);payload=json.loads(prompt.split('\n')[-1]);self.assertEqual(set(payload),{'question','common_initial','previous_answer','previous_reasoning'})
   return {'status':'ok','finish_reason':'stop','content':json.dumps({'answer':str(len(used)),'reasoning':'own'}),'usage':{'prompt_tokens':500,'completion_tokens':50}}
  inv.attempts_used=lambda:len(used)
  r=F.reread({'sample_id':'q','question':'q','image':'x'},initial,{'target_tokens':1000},inv)
  self.assertEqual(r['before']['answer'],'1');self.assertEqual(r['after']['answer'],'2');self.assertEqual(r['known_tokens'],1100);self.assertEqual(initial,before)
 def test05_cap_does_not_reselect_best_answer(self):
  calls=[]
  def inv(*args):
   calls.append(1);return {'status':'ok','finish_reason':'stop','content':json.dumps({'answer':str(len(calls)),'reasoning':'own'}),'usage':{'prompt_tokens':1,'completion_tokens':1}}
  inv.attempts_used=lambda:len(calls)
  r=F.reread({'sample_id':'q','question':'q','image':'x'},{'answer':'old','reasoning':'x'},{'target_tokens':1000},inv)
  self.assertEqual(len(calls),16);self.assertFalse(r['reached']);self.assertEqual(r['answer'],'16');self.assertEqual(r['status'],'ATTEMPT_CAP')
 def test06_length_and_missing_initial(self):
  def inv(*args):return {'status':'ok','finish_reason':'length','content':'{"answer":"new","reasoning":"x"}','usage':{'prompt_tokens':500,'completion_tokens':1200}}
  inv.attempts_used=lambda:0
  r=F.reread({'sample_id':'q','question':'q','image':'x'},{'answer':'old','reasoning':'x'},{'target_tokens':1000},inv)
  self.assertEqual(r['answer'],'old');self.assertTrue(r['reached']);self.assertFalse(r['checkpoints'][-1]['valid_new_answer'])
  r=F.reread({'sample_id':'q'},None,{'target_tokens':0},lambda *a:(_ for _ in ()).throw(AssertionError('no HTTP')))
  self.assertEqual(r['status'],'INITIAL_MISSING');self.assertIsNone(r['answer'])
 def test07_current_image_drift_blocks_request(self):
  with tempfile.TemporaryDirectory() as d,patch.object(F,'ROOT',Path(d)),patch.object(F,'source_paths',return_value=[]):
   p=Path(d);(p/'datasets/fin250').mkdir(parents=True);(p/'datasets/fin250/runtime.jsonl').write_text('x');(p/'x.jpg').write_bytes(b'first')
   frozen={'files':{n:F.X.sha256_file(p/n) for n in ('x.jpg','datasets/fin250/runtime.jsonl')}};(p/'x.jpg').write_bytes(b'changed')
   with self.assertRaisesRegex(ValueError,'drift'):F.guard(frozen,{'image':'x.jpg'},'fin250')
 def test08_protocol_hard_attempt_caps(self):
  obj=json.loads((F.ROOT/'protocol.json').read_text(encoding='utf-8'));self.assertEqual(sum(x['attempt_cap'] for x in F.STAGES.values()),obj['total_real_attempt_cap']);self.assertEqual(obj['total_real_attempt_cap'],14800)
 def test09_shared_quota_native_processes(self):
  import subprocess,sys
  with tempfile.TemporaryDirectory() as d:
   code="import experiment as X; from pathlib import Path; import sys; q=X.Quota(Path(sys.argv[1]),100)\nfor i in range(40):\n try:q.take()\n except X.BudgetStop:break"
   procs=[subprocess.Popen([sys.executable,'-c',code,d],cwd=F.ROOT) for _ in range(8)]
   for p in procs:self.assertEqual(p.wait(timeout=30),0)
   self.assertEqual(json.loads((Path(d)/'quota.json').read_text())['used'],100)
 def test10_official_reference_regression(self):
  from upstream import official_eval as O
  source=json.loads((F.ROOT/'upstream/gpt4v_official_evaluated.json').read_text(encoding='utf-8'));counts=[0,0]
  for r in source['instances']:
   p,ps=O.fintabnet_normalize(r['pred']);g,gs=O.fintabnet_normalize(r['gt']);counts[0]+=int(any(x==y for x in ps for y in gs));counts[1]+=int(p==g)
  self.assertEqual(counts,[199,141]);self.assertEqual(len(source['instances']),250)
if __name__=='__main__':unittest.main()

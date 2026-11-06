import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import experiment as X
import scoring

class ProtocolTests(unittest.TestCase):
 def test_data400_and_retained_image_hashes(self):
  for dataset in ('numeric400','mixed400'):
   rt=X.rows(X.dataset_path(dataset)/'runtime.jsonl');self.assertEqual(len(rt),400);self.assertEqual(len({r['sample_id'] for r in rt}),400)
   for r in rt:self.assertEqual(X.sha256_file(X.ROOT/r['image']),r['image_sha256'])
 def test_no_gold_in_runtime(self):
  for dataset in ('numeric400','mixed400'):
   for r in X.rows(X.dataset_path(dataset)/'runtime.jsonl'):
    self.assertFalse({'answer','exe_ans','gold','program','gold_inds'}&set(r))
 def test_methods_are_isolated(self):
  self.assertIsNot(X.E36,X.E50);self.assertNotEqual(X.E36.CHECK,X.E50.CHECK)
 def test_common_initial_missing_reason_not_guessed(self):
  p=X.parse_common({'status':'ok','finish_reason':'stop','content':'{"answer":"7"}'})
  self.assertEqual(p,{'answer':'7','reasoning':''})
  self.assertIsNone(X.parse_common({'status':'ok','finish_reason':'length','content':'{"answer":"7"}'}))
 def test_round_id_namespace_preserves_values_and_operands(self):
  x={'answer':'2','evidence':[{'id':'i1','content':'1'},{'id':'it1','content':'2','operand_ids':['i1']}],'supporting_ids':['it1']}
  y=X.remap_evidence(x,'prev1_');self.assertEqual(y['evidence'][1]['operand_ids'],['prev1_i1']);self.assertEqual(y['evidence'][0]['content'],'1');self.assertEqual(x['evidence'][0]['id'],'i1')
 def test_shared_initial_budgets_early_stop_and_no_answer_leak(self):
  common={'answer':'1','reasoning':'given reasoning'};saved=copy.deepcopy(common);calls=[]
  def invoke(arm,phase,system,prompt,image=None):
   calls.append((arm,phase,prompt));return {'status':'ok','finish_reason':'stop','content':'{"answer":"3","reasoning":"new reading"}'}
  def ssc(item,current,fn):
   fn('SSC','review','','review',None)
   return {'answer':current['answer'],'status':'REVIEW_ACCEPTED','events':[{'verdict':'correct'}],'calls':[]}
  def evid(item,current,fn):
   fn('EVID','evidence_check','','q',None);changed=current['answer']=='1'
   return {'answer':'2','status':'CORRECTED_EVIDENCE_SUPPORTED' if changed else 'KEPT_EVIDENCE_SUPPORTED',
    'check':{'verdict':'incorrect' if changed else 'correct'},'repair':{'reasoning':'new evidence reason'},'evidence_versions':[],'calls':[]}
  def fifty(item,current,fn):
   fn('EVID','evidence_check','','q',None);return {'answer':current['answer'],'status':'KEPT_EVIDENCE_SUPPORTED','check':{'verdict':'correct'},'calls':[]}
  with patch.object(X.SSC,'finish',side_effect=ssc),patch.object(X.E36,'run',side_effect=evid),patch.object(X.E50,'run',side_effect=fifty):out=X.repairs({'sample_id':'q','dataset':'vwtq','question':'q','image':'x'},common,invoke)
  self.assertEqual(common,saved);self.assertTrue(out['shared_unchanged']);self.assertEqual(out['arms']['EVID']['answer'],'2');self.assertEqual(out['arms']['RR_MATCH']['answer'],'3')
  self.assertEqual([r['calls'] for r in out['arms']['RR_MATCH']['rounds']],[1,1]);self.assertFalse(out['arms']['SSC']['rounds'][1]['executed'])
  self.assertEqual(sum(a=='FIX_SSC' for a,_,_ in calls),1)
 def test_initial_unsubmitted_kept_for_all_methods(self):
  o=X.repairs({'sample_id':'q','dataset':'vwtq'},None,lambda *a:(_ for _ in ()).throw(AssertionError('no call')))
  self.assertTrue(all(r['answer'] is None for r in o['arms'].values()))
 def test_budget_sum_is_fixed(self):
  self.assertEqual(sum(400*sum(v.values()) for v in X.LIMITS.values())*2,32800)
 def test_quota_no_extra_reservation_after_cap(self):
  with tempfile.TemporaryDirectory() as d:
   q=X.Quota(d,2);q.take();q.take()
   with self.assertRaises(X.BudgetStop):q.take()
   self.assertEqual(json.loads((Path(d)/'quota.json').read_text())['used'],2)
 def test_caller_resume_retains_finish_reason_and_single_charge(self):
  class HTTP:
   def __enter__(self):return self
   def __exit__(self,*a):pass
   def read(self):return json.dumps({'choices':[{'message':{'content':'{"answer":"7"}'},'finish_reason':'length'}],'usage':{'prompt_tokens':1,'completion_tokens':1}}).encode()
  with tempfile.TemporaryDirectory() as d,patch.object(X.API,'load_env',return_value={'BAILIAN_BASE_URL':'https://invalid.test','BAILIAN_API_KEY':'TEST'}),patch.object(X.API.urllib.request,'urlopen',return_value=HTTP()) as http:
   limits={'per_sample':{'COMMON':1},'batch_total':1};caller=X.API.Caller(d,limits,'TEST',response_mode='text')
   _,a=caller.call('COMMON','q',None,'p','test',request_scope='q');other=X.API.Caller(d,limits,'TEST',response_mode='text');_,b=other.call('COMMON','q',None,'p','test',request_scope='q')
   self.assertEqual(a['finish_reason'],b['finish_reason']);self.assertEqual(http.call_count,1)
   self.assertTrue(other.call('COMMON','q',None,'new','test',request_scope='new')[1]['abort'])
 def test_frozen_image_and_dependency_drift_precede_request(self):
  with tempfile.TemporaryDirectory() as d,patch.object(X.API,'load_env',return_value={'BAILIAN_BASE_URL':'https://invalid.test','BAILIAN_API_KEY':'TEST'}),patch.object(X.API.urllib.request,'urlopen',side_effect=AssertionError('no network')) as http:
   p=Path(d)/'x.png';p.write_bytes(b'a');rel=p.relative_to(X.ROOT).as_posix() if p.is_relative_to(X.ROOT) else str(p)
   freeze={'sha256':'f','files':{rel:X.sha256_file(p)}}
   with patch.object(X,'folder',return_value=Path(d)),patch.dict(X.sys.modules):
    X.sys.modules.pop('scoring',None)
    transport=X.Invoke('numeric400','initial','',freeze);p.write_bytes(b'b')
    with self.assertRaisesRegex(ValueError,'drift'):transport.for_item({'sample_id':'q','image':rel,'image_sha256':freeze['files'][rel]})('COMMON','x','','p')
   self.assertEqual(http.call_count,0)
 def test_actual_end_to_end_protocols_without_network(self):
  seen=[]
  def response(obj):return {'status':'ok','finish_reason':'stop','content':obj if isinstance(obj,str) else json.dumps(obj)}
  def invoke(arm,phase,system,prompt,image=None):
   seen.append((arm,phase,prompt));self.assertNotIn('GOLD_SECRET',prompt)
   if arm in ('DIRECT','REREAD2'):return response('1')
   if arm=='SSC_INIT':return response('Reasoning: read cell\nAnswer: 1')
   if arm=='SSC':return response('Verdict: correct\nError Type: none\nError Analysis: No errors found\nChecks: checked')
   if phase=='observe_facts':return response({'question_plan':{},'scope':'cell','unresolved':[],'rows':[['r1','c1','1']],'operations':[]})
   if arm=='EVID_INIT':return response({'answer':'1','reasoning':'read','supporting_ids':['i1'],'scope':'cell','unresolved':[]})
   return response({'verdict':'correct','analysis':'read cell','evidence':[],'supporting_ids':['i1'],'scope':'cell','unresolved':[]})
  out=X.run_e2e({'sample_id':'q','dataset':'finqa_rendered','question':'q','image':'x','gold':'GOLD_SECRET'},invoke)
  self.assertTrue(all(str(r['answer'])=='1' for r in out['arms'].values()));self.assertEqual(len(seen),8)
 def test_actual_both_evidence_methods_accept_common_without_own_initial(self):
  seen=[]
  def invoke(arm,phase,system,prompt,image=None):
   seen.append((arm,phase));base={'status':'ok','finish_reason':'stop'}
   if arm=='FIX_SSC':return dict(base,content='Verdict: correct\nError Type: none\nError Analysis: none')
   if arm=='RR_MATCH':return dict(base,content='{"answer":"1","reasoning":"reread"}')
   payload=json.loads(prompt.split('\n')[-1]);obj={'verdict':'correct','analysis':'reread cell','evidence':[{'id':'c1','location':'r1/c1','content':'1','source':'image'}],
    'supporting_ids':['c1'],'scope':'cell','unresolved':[]}
   if arm=='FIX_E50':obj.update(reviews=[{'id':'n1','kind':'VALUE','target':'cell','necessity':'NECESSARY','necessity_reason':'question asks value','reliability':'SUPPORTED','evidence_ids':['c1'],'reason':'reread image'}],
      audit_target={'answer':'1','context_id':payload['review_context']['context_id']},countercheck={'target':'neighbor column','result':'NONE_FOUND','evidence_ids':['c1'],'reason':'header checked'})
   return dict(base,content=json.dumps(obj))
  out=X.repairs({'sample_id':'q','dataset':'vwtq','question':'q','image':'x'},{'answer':'1','reasoning':'read once'},invoke)
  self.assertEqual(out['arms']['EVID']['rounds'][0]['status'],'KEPT_EVIDENCE_SUPPORTED')
  self.assertEqual(out['arms']['EVID50']['rounds'][0]['status'],'KEPT_EVIDENCE_SUPPORTED')
  self.assertEqual(len(seen),4)
 def test_historical_scoring_regression(self):
  fixtures=json.loads((X.ROOT/'scoring_regression.json').read_text(encoding='utf-8'))
  self.assertEqual(len(fixtures),400)
  for r in fixtures:
   q=scoring.eval_one(r['sample_id'],r['prediction'],r['reference']);actual='A' if r['prediction'] in (None,'') else 'C' if q['display_match'] or q['exec_match'] else 'W'
   self.assertEqual(actual,r['expected'])
 def test_unknown_judgment_is_not_forced_into_binary_accuracy(self):
  from evaluate import detection,transitions
  truth={'c':'C','w':'W','u':'W','a':'A'}
  d=detection(truth,{'c':{'executed':True,'judgment':'correct'},'w':{'executed':True,'judgment':'incorrect'},'u':{'executed':True,'judgment':'unknown'},'a':{'executed':False}})
  self.assertEqual(d['selective_accuracy'],1);self.assertEqual(d['coverage'],2/3);self.assertEqual(d['error_recall'],.5);self.assertEqual(d['unknown'],1)
  t=transitions(truth,{'c':'A','w':'C','u':'W','a':'A'})
  self.assertEqual(t['n'],4);self.assertEqual(t['corrected'],1);self.assertEqual(t['corrupted'],0);self.assertEqual(t['correct_lost_to_unsubmitted'],1);self.assertEqual(t['accuracy'],.25)
 def test_shared_initial_freeze_detects_content_drift(self):
  rt=X.rows(X.dataset_path('numeric400')/'runtime.jsonl')
  with tempfile.TemporaryDirectory() as d,patch.object(X,'verify',return_value={'sha256':'f'}),patch.object(X,'folder',return_value=Path(d)),patch.object(X,'initial_path',return_value=Path(d)/'results.jsonl'):
   records=[{'sample_id':r['sample_id'],'freeze_sha256':'f','initial':{'answer':'1','reasoning':'given'}} for r in rt]
   X.save_rows(Path(d)/'results.jsonl',records);X.initial_freeze('numeric400')
   records[0]['initial']['answer']='2';X.save_rows(Path(d)/'results.jsonl',records)
   with self.assertRaisesRegex(ValueError,'drift'):X.verify_initial('numeric400')
 def test_native_shared_quota_eight_processes(self):
  import subprocess,sys
  with tempfile.TemporaryDirectory() as d:
   code="import experiment as X; from pathlib import Path; import sys; q=X.Quota(Path(sys.argv[1]),100)\nfor i in range(40):\n try:q.take()\n except X.BudgetStop:break"
   procs=[subprocess.Popen([sys.executable,'-c',code,d],cwd=X.ROOT) for _ in range(8)]
   for p in procs:self.assertEqual(p.wait(timeout=30),0)
   self.assertEqual(json.loads((Path(d)/'quota.json').read_text())['used'],100)
 def test_source_freeze_excludes_external_dependencies(self):
  files=X.source_files()
  self.assertIn(X.ROOT/'run_all.py',files)
  self.assertIn(X.ROOT/'build_workbook.mjs',files)
  self.assertTrue(all('node_modules' not in p.parts for p in files))
if __name__=='__main__':unittest.main()

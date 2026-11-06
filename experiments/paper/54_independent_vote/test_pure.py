import unittest
import pure as P
class Tests(unittest.TestCase):
 def test_vote(self):
  self.assertEqual(P.vote(['1 million','1','1 million']),'1 million');self.assertEqual(P.vote(['B','A']),'B');self.assertIsNone(P.vote([None]))
 def test_payload_never_contains_previous_response(self):
  prompts=[]
  def call(*args):
   prompts.append(args[3]);return {'status':'ok','finish_reason':'stop','content':'SECRET_OWN_ANSWER','usage':{'prompt_tokens':100,'completion_tokens':1}}
  r=P.trajectory({'question':'QUESTION','image':'IMAGE'},150,call)
  self.assertEqual(len(prompts),2);self.assertEqual(prompts[0],prompts[1]);self.assertNotIn('SECRET_OWN_ANSWER',prompts[1]);self.assertEqual(r['before']['tokens'],101)
 def test_caps(self):self.assertEqual(sum(v['attempt_cap'] for v in P.F.STAGES.values()),16800)
if __name__=='__main__':unittest.main()

"""LLM judges semantic support. Code tracks evidence and checks references, not truth."""
import copy
import json
from . import fact_protocol as P

ACTIONS={'VALUE':'A_VALUE','BINDING':'A_BIND','TIME':'A_TIME','UNIT':'A_UNIT','COVERAGE':'A_SCOPE','CALCULATION':'A_CALC','QUESTION':'A_INTERPRET'}
CHECK='''Inspect the image, question, YOUR OWN initial answer, reasoning and evidence.
Initial evidence is a fallible observation, not ground truth.
Some initial records may be complete fragments of a truncated response, so range
remains OPEN. source=unverified means the legacy response did not declare a source;
it is a clue, not verified support. Add explicit image observations before relying
on such a clue to judge correct. Never infer missing rows from recovered fragments. Check it against the image.
Use c1,c2,... for NEW observations; existing initial IDs i1,i2,... may be cited but not overwritten.
If existing evidence suffices, evidence may be [], but supporting_ids must cite it. Check cells,
arithmetic, question meaning and completeness. Judge from explicit observations,
not confidence or agreement. Compare raw facts, arithmetic auxiliaries and all
qualifiers before deciding. Search explicitly for an excluded row, a lower/higher
counterexample or a unit/header contradiction. Never label correct when your own
analysis says an observation defeats the candidate. Initial scope claims are not
proof. An auxiliary result applies only to its listed operands. If operands/range
are in doubt, diagnose the gap rather than treating a computed value as the truth.
Make the finding first, then give the verdict. Return ONE compact JSON object:
{"analysis":"brief fact comparison","error_type":"...","verdict":"correct|incorrect|unknown",
"evidence":[{"id":"c1","location":"row/column/header","content":"raw observation","source":"image|calculation"}],
"supporting_ids":["c1"],"scope":"checked range or unresolved range",
"unresolved":[],"gap":null,"action":null}.
correct requires observations supporting the WHOLE requested answer and no unresolved
obligation. Both keeping and changing require evidence. If incorrect or unknown,
provide gap={"type":"VALUE|BINDING|TIME|UNIT|COVERAGE|CALCULATION|QUESTION",
"target":"specific cell/condition/range","why":"missing or contradictory support"}
and its corresponding action A_VALUE/A_BIND/A_TIME/A_UNIT/A_SCOPE/A_CALC/A_INTERPRET.
Do not generate a corrected answer yet. Missing information is unknown, not correct.
Counting rows, entities, and returning entity names are different question meanings.
Do not force lists, dates or extrema labels into counts. A claimed complete scan is
a model assertion, never a completeness certificate. Keep strings short, no prose outside JSON.'''
DIRECTIVES={
'A_VALUE':'Reread the specified cell and its nearby row/column labels; preserve raw characters and alternatives.',
'A_BIND':'Trace the specified value to its physical row and full column/header. Distinguish entity identity from repeated event rows.',
'A_TIME':'Read the specified date/interval/status and its headers. Resolve current versus historical status and inclusive boundaries.',
'A_UNIT':'Read unit/header/footnote and give any explicit conversion with operands. Do not change counting objects by a unit rule.',
'A_SCOPE':'Inspect the required candidate range, recording qualifying AND excluded observations compactly. State the unit of selection and omissions.',
'A_CALC':'Retrieve raw operands at their locations, then redo the specified arithmetic or comparison explicitly. Preserve signs and units.',
'A_INTERPRET':'Compare the question wording to table headers. Resolve selection versus count, row versus entity, negation and thresholds; keep unresolved alternatives.'}
ACQUIRE='''Execute ONLY the specified evidence action on the image, using its target
and diagnostic reason. Return ONE JSON object:
{"evidence":[{"id":"a1","location":"row/column/header","content":"observation or explicit calculation","source":"image|calculation"}],
"scope":"range inspected and limits","unresolved":[]}.
Use new IDs a1,a2,...; do not repeat c IDs. Do not output an answer or a verdict.
Do not invent missing source information. Use compact relevant records, not an entire table transcription.'''
REPAIR='''Use the question, image and updated evidence ledger to reassess the INITIAL
answer. LLM owns semantic judgment; do not rely on program source labels.
Return ONE compact JSON object:
{"verdict":"correct|incorrect|unknown","error_type":"...","analysis":"specific evidence-based finding",
"supporting_ids":["c1","a1"],"scope":"support and limits","unresolved":[],
"answer":"final candidate","reasoning":"brief evidence-based explanation"}.
If initial answer is correct, keep it. ONLY if incorrect, generate the corrected
answer based on that error and the observations. If unknown, keep initial answer
and disclose unresolved obligations. Selection/list answers must stay names/lists,
not counts. Do not let missing schema be a reason to invent a different answer.
Do not add new observations outside the ledger; any new observation needs another
acquisition which is outside this call's permission. No Markdown or long narrative.'''
RECHECK='''Check the candidate against the question, image and supplied evidence ledger.
Return ONE compact JSON object:
{"verdict":"correct|incorrect|unknown","analysis":"brief finding",
"supporting_ids":["a1"],"scope":"checked obligation/range","unresolved":[]}.
correct requires evidence supporting the entire requested answer, including
counting/selection unit, binding, time, units and relevant range as applicable.
Do not trust agreement or declared completeness. Do not propose another answer.
If you discover a new fact not in the ledger, disclose it as unresolved: this call
does not silently acquire or replace evidence. Missing support means unknown.'''

def parse(resp):
    return P.single_object(resp)


def valid_answer(x):return isinstance(x,(str,int,float)) and not isinstance(x,bool) and str(x).strip()!=''

def add_evidence(pool,records):
    if not isinstance(records,list) or not records:return 'missing_evidence'
    staged={}
    for e in records:
        if not isinstance(e,dict) or not all(isinstance(e.get(k),str) and e[k].strip() for k in ('id','location','content','source')):return 'invalid_evidence'
        if e['id'] in pool or e['id'] in staged:return 'duplicate_evidence_id'
        if e['source'] not in ('image','calculation','unverified'):return 'invalid_evidence_source'
        staged[e['id']]=copy.deepcopy(e)
    pool.update(staged)
    return None

def judgment_issue(obj,pool):
    if not isinstance(obj,dict) or obj.get('verdict') not in ('correct','incorrect','unknown'):return 'invalid_verdict'
    if not isinstance(obj.get('analysis'),str) or not obj['analysis'].strip():return 'missing_analysis'
    refs=obj.get('supporting_ids')
    if not isinstance(refs,list) or not refs or any(not isinstance(r,str) or r not in pool for r in refs):return 'invalid_support_reference'
    if not isinstance(obj.get('unresolved'),list) or not str(obj.get('scope') or '').strip():return 'missing_scope_or_uncertainty'
    if obj['verdict']=='correct' and obj['unresolved']:return 'correct_with_unresolved'
    if obj['verdict']=='correct' and any(pool[r]['source']=='unverified' for r in refs):return 'unverified_source_support'
    return None

def run(item,initial,invoke):
    old=initial.get('answer');pool={}
    out={'answer':old,'status':'FALLBACK_UNVERIFIED','calls':[],'evidence_versions':[],
         'events':[],'unresolved':[],'strict':False,'candidates':[{'source':'initial','answer':old}]}
    if not valid_answer(old):out.update(status='INITIAL_MISSING',unresolved=['initial_missing']);return out
    initial_evidence=initial.get('evidence',[])
    if initial_evidence:
        issue=add_evidence(pool,initial_evidence)
        if issue:out['unresolved'].append('initial_'+issue);return out
    out['initial_evidence']=copy.deepcopy(list(pool.values()))
    def call(phase,prompt,data):
        r=invoke('EVID',phase,'',prompt+'\n'+json.dumps(data,ensure_ascii=False),item['image'])
        out['calls'].append(r);return parse(r)
    def fail(reason):out['unresolved'].append(reason);return out
    ctx={'question':item['question'],'initial_answer':old,'initial_reasoning':initial.get('reasoning',''),'initial_evidence':copy.deepcopy(list(pool.values())),'initial_limits':{'scope':initial.get('scope'),'unresolved':initial.get('unresolved',[])},'question_plan':initial.get('question_plan'),'computed_aux':initial.get('computed_aux',[])}
    check=call('evidence_check',CHECK,ctx);out['check']=check
    if not check:return fail('check_unparseable')
    issue=None if check.get('evidence')==[] and pool else add_evidence(pool,check.get('evidence'))
    # unknown may legitimately have no observation yet, but cannot claim correct.
    if issue and not (check.get('verdict')=='unknown' and check.get('evidence')==[]):return fail(issue)
    if check.get('verdict')=='unknown' and not pool:
        issue=None if check.get('analysis') and isinstance(check.get('unresolved'),list) else 'invalid_unknown'
    else:issue=judgment_issue(check,pool)
    if issue:return fail(issue)
    out['evidence_versions'].append({'version':0,'evidence':copy.deepcopy(list(pool.values()))})
    if check['verdict']=='correct':out.update(status='KEPT_EVIDENCE_SUPPORTED');return out
    gap=check.get('gap')
    if not isinstance(gap,dict) or not isinstance(gap.get('type'),str) or gap['type'] not in ACTIONS:return fail('unknown_gap_type')
    if not all(isinstance(gap.get(k),str) and gap[k].strip() for k in ('target','why')):return fail('unspecified_gap')
    action=ACTIONS[gap['type']]
    if check.get('action')!=action:out['events'].append({'phase':'action_normalized','requested_action':check.get('action'),'selected_action':action,'basis':'declared_gap_type'})
    out['events'].append({'phase':'action_selected','gap':copy.deepcopy(gap),'action':action,'basis_version':0})
    acquired=call('acquire_'+action,ACQUIRE+'\nACTION: '+DIRECTIVES[action],{'question':item['question'],'action':action,'target':gap['target'],'question_plan':initial.get('question_plan')})
    if not acquired:return fail('acquisition_unparseable')
    issue=add_evidence(pool,acquired.get('evidence'))
    if issue:return fail(issue)
    if not isinstance(acquired.get('unresolved'),list) or not str(acquired.get('scope') or '').strip():return fail('acquisition_scope_missing')
    out['events'].append({'phase':'checks_invalidated','checks':['answer_support','scope_support'],'reason':'new_evidence','version':1})
    out['evidence_versions'].append({'version':1,'evidence':copy.deepcopy(list(pool.values())),'scope':acquired['scope'],'unresolved':acquired['unresolved']})
    ctx.update(evidence=list(pool.values()),diagnosis=check,acquisition_limits={'scope':acquired['scope'],'unresolved':acquired['unresolved']})
    repair=call('evidence_reassess_and_correct',REPAIR,ctx);out['repair']=repair
    issue=judgment_issue(repair,pool)
    if issue:return fail(issue)
    if not valid_answer(repair.get('answer')) or not str(repair.get('reasoning') or '').strip():return fail('invalid_repair_answer')
    changed=str(repair['answer'])!=str(old)
    if changed and repair['verdict']!='incorrect':return fail('changed_without_incorrect')
    if repair['verdict']=='unknown':return fail('repair_unknown')
    if repair['unresolved']:return fail('repair_unresolved')
    out['candidates'].append({'source':'evidence_correction','answer':repair['answer'],'evidence_version':1})
    final=call('recheck',RECHECK,{'question':item['question'],'candidate':repair['answer'],'reasoning':repair['reasoning'],'evidence':list(pool.values()),'acquisition_limits':ctx['acquisition_limits']})
    out['final_check']=final
    issue=judgment_issue(final,pool)
    if issue:return fail(issue)
    if final['verdict']!='correct':return fail('final_not_supported')
    out.update(answer=repair['answer'],status='CORRECTED_EVIDENCE_SUPPORTED' if changed else 'KEPT_AFTER_ACQUISITION')
    return out

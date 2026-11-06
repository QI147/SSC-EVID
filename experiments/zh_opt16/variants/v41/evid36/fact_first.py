"""Evidence-first initial answering. Arithmetic is auxiliary, not a semantic judge."""
import json,re
from decimal import Decimal,InvalidOperation
from . import evid_integrated as E
from . import fact_protocol as P

OBSERVE='''Read the question and image BEFORE answering. Return ONE compact JSON object.
Every rows entry below is an IMAGE OBSERVATION. Format:
{"question_plan":{"answer_type":"number|entity|entities|date|comparison|other",
"condition":"short predicate","selection_unit":"rows/entities/events if relevant"},
"scope":"range and limits","unresolved":[],
"rows":[["physical row","column/header","raw text",null,null]],"operations":[]}.
Each row tuple is [physical_row,column,raw_text,optional_pure_numeric_value,optional_entity].
Last two entries may be omitted. IDs i1,i2,... are assigned by tuple position;
source=image and locations are assigned by this explicit format, so do not repeat
id/location/content/source keys for every cell. Use physical row identities rather
than deduplicating equal printed labels. Record unit/header/date evidence too.
Use only relevant columns, short raw labels and raw values; no long explanations.
For scans, include relevant positive and negative observations compactly. Never
claim omitted rows complete. Prefer one row tuple per needed cell, not a transcript.
At most TWO OPTIONAL operations over the ENTIRE listed subset, never one per row.
Allowed op=sum|sub|count|count_distinct|compare|argmin|argmax|filter_gt|filter_ge,
with id=it1, evidence_ids=[i1,i2,...], threshold only for filters. Operations may be [].
Place plan/scope first and facts before optional operations. Do not give final answer.
If budget is insufficient disclose remaining range in scope/unresolved; do not invent.'''


ANSWER='''Use your OWN observed facts and auxiliary computations to produce an initial
answer. The facts may be mistaken or incomplete; inspect the original image when
needed. Computed results are conditional on operands and range, not truth labels.
Return ONE compact JSON object:
{"answer":"...","reasoning":"brief fact-based explanation",
"supporting_ids":["i1","it1"],"scope":"coverage and remaining limits","unresolved":[]}.
Include the full requested answer: observed unit scale/currency, complete names,
qualifiers and date ranges as applicable. For example, a cell under an 'in millions'
header must preserve that magnitude in the answer; do not infer units from gold
or domain stereotypes. Cite the header fact supporting the unit. A numeric lookup
and listing matching entities are different tasks. Do not substitute counts for lists.
Do not copy a computational error from prose if auxiliary arithmetic contradicts it.
Use existing IDs. A candidate may be unverified: if the observation protocol failed,
read the original image directly and use supporting_ids=[] rather than inventing facts
or IDs. Missing metadata is not a reason to invent or withhold an otherwise defensible
candidate. If no defensible candidate exists, answer:null and disclose why.
Null/unverified/unbound records are clues only; blank is never numeric zero.
If support is insufficient keep uncertainty explicit, no fabrication.
Return the JSON directly, no preamble or Markdown. Keep reasoning to two short sentences.
Do not repeat the evidence list; cite IDs. Keep the complete requested answer and units.'''

# ---------------------------------------------------------------- variant 37
# Same information, different packaging.  The 36 answer field invites
# "23.13% increase (from $60,786m in 2008 to $74,843m in 2009)"; a backbone that
# complies then fails an exact-match scorer even though the value is right.
# 37 keeps the printed unit (the table's own scale words) and drops everything
# else, and it makes the answer name the row it used.
from .variant import VARIANT

OBSERVE_V37 = OBSERVE + '''
Return at most 10 row tuples. Keep only the cells the answer depends on and put
the rest of the range in scope/unresolved. If the question's condition selects one
row, list that row FIRST; do not list unrelated rows as context.'''

ANSWER_V37 = ANSWER.split('Include the full requested answer:')[0] + '''Answer with the value exactly as the table prints it: the number with the unit or
scale word shown in its header (for example 124 or 23.13%), the entity name, or a
comma-separated list for a list question. Nothing else in answer: no sentence, no
parenthesis, no calculation steps, no "approximately", and do not restate the
condition. Put the qualifiers you would otherwise append into basis_row/reasoning.
Name the physical row or header the value comes from in basis_row, and make
supporting_ids point at that row. A numeric lookup and listing matching entities
are different tasks. Do not substitute counts for lists.
Do not copy a computational error from prose if auxiliary arithmetic contradicts it.
Use existing IDs. A candidate may be unverified: if the observation protocol failed,
read the original image directly and use supporting_ids=[] rather than inventing facts
or IDs. If no defensible candidate exists, answer:null and disclose why.
Null/unverified/unbound records are clues only; blank is never numeric zero.
Return the JSON directly, no preamble or Markdown. Keep reasoning to one short sentence.'''

ANSWER_V37 = ANSWER_V37.replace(
    '{"answer":"...","reasoning":"brief fact-based explanation",',
    '{"answer":"value only","basis_row":"physical row or header used",'
    '"reasoning":"one short sentence",')

if VARIANT == '37':
    OBSERVE, ANSWER = OBSERVE_V37, ANSWER_V37

# ---------------------------------------------------------------- variant 38
# 37 removed the 36 unit/magnitude clause along with the narrative and lost the
# scale words the gold answers carry ("$2,749.3 million" -> "$2,749.3",
# "-124" -> "124").  38 keeps 36's unit requirement verbatim and bans only the
# packaging: sentence, parentheses, working, restated condition.
ANSWER_V38 = ANSWER.replace(
    '{"answer":"...","reasoning":"brief fact-based explanation",',
    '{"answer":"...","basis_row":"physical row or header used",'
    '"reasoning":"brief fact-based explanation",') + '''
The answer string itself carries the value and its printed unit only: no sentence,
no parenthesis, no calculation steps, no restatement of the condition, and no
"approximately". Write a decrease, loss or drop as a negative number instead of the
word. Anything else you would have appended belongs in basis_row or reasoning.'''

if VARIANT == '38':
    ANSWER = ANSWER_V38

# ---------------------------------------------------------------- variant 39
# 38 left two error families on the second backbone: answering a different thing
# than asked ("above or below" -> "172,000"; "which season" -> "2005"; an office
# -> the person holding it) and silently rounding (4.74% -> 4.7%).  39 adds the
# answer-type contract, the row-consistency requirement and a precision rule.
ANSWER_V39 = ANSWER_V38 + '''
Respect question_plan.answer_type. A comparison answers exactly one of
yes/no/above/below/more/less/equal and never the number being compared. An entity
answer is the entity itself, not the office it holds, the year it refers to or
another cell of the same row. A count answer is the count, not the listed items.
The answer must be a value that appears in the row named by basis_row; if it does
not appear there, choose again before answering.
Copy the precision printed in the table: do not round 4.74 to 4.7 or 627,442,961
to 627,443,000.'''

if VARIANT == '39':
    ANSWER = ANSWER_V39

def number(value):
    if isinstance(value,bool):raise ValueError('boolean is not numeric')
    s=str(value).strip()
    if not re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)',s):raise ValueError('not an explicit pure numeric operand')
    n=Decimal(s)
    if not n.is_finite():raise ValueError('non-finite')
    return n

def compute(facts,operations):
    pool={e['id']:e for e in facts};derived=[];reports=[]
    if not isinstance(operations,list):return [],[{'status':'INVALID','reason':'operations not list'}]
    for op in operations:
        try:
            if not isinstance(op,dict):raise ValueError('invalid operation')
            oid=op.get('id');ids=op.get('evidence_ids');kind=op.get('op')
            if not isinstance(oid,str) or not oid.startswith('it') or oid in pool:raise ValueError('invalid calculation ID')
            if not isinstance(ids,list) or not ids or any(not isinstance(i,str) or i not in pool for i in ids):raise ValueError('missing operand')
            if len(ids)!=len(set(ids)):raise ValueError('duplicate operand reference')
            operands=[pool[i] for i in ids]
            if any(e['source']!='image' for e in operands):raise ValueError('only explicit raw observations as operands')
            if kind=='count':
                keys=[e['row_key'] for e in operands]
                if any(not isinstance(k,str) or not k.strip() for k in keys) or len(set(keys))!=len(keys):raise ValueError('ambiguous or duplicated physical row')
                result=str(len(keys))
            elif kind=='count_distinct':
                keys=[e['entity'] for e in operands]
                if any(not isinstance(k,str) or not k.strip() for k in keys):raise ValueError('missing explicit entity')
                result=str(len(set(keys)))
            else:
                values=[number(e['value']) for e in operands]
                if kind=='sum':result=str(sum(values,Decimal(0)))
                elif kind=='sub':
                    if len(values)!=2:raise ValueError('sub needs two operands')
                    result=str(values[0]-values[1])
                elif kind=='compare':
                    if len(values)!=2:raise ValueError('compare needs two operands')
                    result='greater' if values[0]>values[1] else 'less' if values[0]<values[1] else 'equal'
                elif kind in ('argmin','argmax'):
                    extreme=(min if kind=='argmin' else max)(values)
                    result=[ids[i] for i,v in enumerate(values) if v==extreme]
                elif kind in ('filter_gt','filter_ge'):
                    threshold=number(op.get('threshold'))
                    result=[ids[i] for i,v in enumerate(values) if (v>threshold if kind=='filter_gt' else v>=threshold)]
                else:raise ValueError('unsupported operation')
            ev={'id':oid,'location':'calculation over '+','.join(ids),'content':json.dumps({'op':kind,'result':result},ensure_ascii=False),
                'source':'calculation','operand_ids':ids,'result':result,'range_status':'LISTED_OPERANDS_ONLY'}
            derived.append(ev);pool[oid]=ev;reports.append({'id':oid,'status':'COMPUTED','result':result,'operand_ids':ids,'range_status':'LISTED_OPERANDS_ONLY'})
        except (ValueError,KeyError,TypeError,InvalidOperation) as exc:
            reports.append({'id':op.get('id') if isinstance(op,dict) else None,'status':'INVALID','reason':str(exc)})
    return derived,reports

def initial(item,invoke):
    observation=invoke('EVID_FACTS','observe_facts','',OBSERVE+'\nQuestion: '+item['question'],item['image'])
    obj,normalization=P.observation(observation);record={'observation_call':observation,'facts':obj,'observation_normalization':normalization}
    if not obj:
        if observation.get('status')!='ok':return None,record
        obj={'facts':[],'question_plan':{'answer_type':'unknown'},'scope':'OPEN: observation protocol unavailable','unresolved':['observation_protocol_unavailable'],'operations':[]}
        record['facts']=obj
    record['usable_observation_count']=len(obj['facts'])
    pool={};issue=E.add_evidence(pool,obj.get('facts')) if obj.get('facts') else None
    if issue:record['issue']=issue;return None,record
    if any(e['source'] not in ('image','unverified') or not e['id'].startswith('i') or e['id'].startswith('it') for e in pool.values()):record['issue']='invalid_observed_fact';return None,record
    if not isinstance(obj.get('question_plan'),dict) or not str(obj.get('scope') or '').strip() or not isinstance(obj.get('unresolved'),list):record['issue']='missing_observation_context';return None,record
    derived,reports=compute(list(pool.values()),obj.get('operations',[]))
    for e in derived:pool[e['id']]=e
    record['computed_aux']=reports
    answer_call=invoke('EVID_INIT','initial_answer','',ANSWER+'\n'+json.dumps({'question':item['question'],'facts':list(pool.values()),'question_plan':obj['question_plan'],
                    'computed_aux':reports,'scope':obj['scope'],'unresolved':obj['unresolved']},ensure_ascii=False),item['image'])
    record['answer_call']=answer_call;answer=E.parse(answer_call)
    if not answer or not E.valid_answer(answer.get('answer')):record['issue']='invalid_initial_answer';return None,record
    answer=copy_answer_metadata(answer)
    refs=answer.get('supporting_ids')
    if not isinstance(refs,list):refs=[]
    valid_refs=[r for r in refs if isinstance(r,str) and r in pool]
    if not valid_refs or len(valid_refs)!=len(refs):answer['unresolved'].append('initial_support_not_verified')
    answer['supporting_ids']=valid_refs
    return {**answer,'evidence':list(pool.values()),'question_plan':obj['question_plan'],'computed_aux':reports,
            'observation_meta':normalization,'unresolved':list(dict.fromkeys(obj['unresolved']+answer['unresolved']))},record


def copy_answer_metadata(answer):
    # Preserve a model-authored answer; absent metadata stays explicitly unknown.
    answer=dict(answer)
    unresolved=answer.get('unresolved')
    answer['unresolved']=list(unresolved) if isinstance(unresolved,list) else ['initial_uncertainty_not_emitted']
    if not isinstance(answer.get('reasoning'),str) or not answer['reasoning'].strip():
        answer['reasoning']='';answer['unresolved'].append('initial_reasoning_not_emitted')
    if not isinstance(answer.get('scope'),str) or not answer['scope'].strip():
        answer['scope']='OPEN: initial scope not emitted';answer['unresolved'].append('initial_scope_not_emitted')
    return answer


# Variant 40: task-aware evidence coverage; preserve variant 36 unchanged.
if VARIANT in ('40','41'):
    OBSERVE += """
For Chinese questions, interpret the requested object before recording facts.
Keep Chinese row/column names, nested header paths, printed units and signs.
For counts, rankings, extrema and thresholds, examine the full relevant range;
record boundary cases and exclusions. Never infer a group from a blank cell
without visible header/group evidence. If a range is incomplete, state it in
unresolved. For trend, difference and rate questions, record both periods and
their units; percentage change and percentage-point difference are distinct.
For explanation questions, separate visible table evidence from conjectured causes.
Do not invent external causes or treat correlation as causation.
"""
    ANSWER += """
Respond in the question's language, using the requested answer type.
Chinese count questions require the count and requested classifier; entity queries
require the actual row/entity label, not its associated numeric value.
For numeric answers preserve the sign, printed scale and requested precision.
For trend/explanation questions supply a concise complete answer grounded in
specific observed comparisons; a naked number is insufficient for such questions.
Reasoning stays separate from answer; never include alternative guesses.
"""


if VARIANT == '41':
    ANSWER += """
A verified empty matching set is a complete, defensible answer, not missing
evidence: state that no item satisfies the condition in the question's language.
Evaluate AND/OR and inclusive boundaries exactly as asked. Never substitute
'closest match', speculate that a question has a typo, relax a condition or
guess an intended answer to force a match. Distinguish verified absence from
insufficient visual evidence; only the latter warrants uncertainty/null.
Keep reasoning to at most two short sentences as already required. Do not
repeatedly reconsider a resolved predicate or speculate about dataset intentions.
"""

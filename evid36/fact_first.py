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

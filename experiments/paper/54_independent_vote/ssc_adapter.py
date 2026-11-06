"""Author prompts/parsers/three-step branching, with image transport only.

No author source is edited. This is a visual-input adaptation, not the author's
original text-table experimental condition or original model reproduction.
"""
import importlib.util
from pathlib import Path

AUTHOR_SRC=Path(__file__).resolve().parent/'vendor/ssc_author'

def modules():
    out=[]
    for name in ('prompts','parser'):
        spec=importlib.util.spec_from_file_location('ssc_author_original_'+name,AUTHOR_SRC/(name+'.py'))
        mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);out.append(mod)
    return out

def parse_initial(resp):
    if resp.get('status')!='ok' or not resp.get('content'):return None
    parsed=modules()[1].parse_step1(resp['content'])
    return parsed if parsed.get('answer') else None

def finish(item,initial,invoke):
    prompts,parser=modules();benchmark='faketab' if item.get('dataset') in ('fintabnetqa','finqa_rendered') else 'wikitq'
    table='[See the supplied table image.]'
    out={'answer':initial['answer'],'status':'INITIAL','events':[],'calls':[],
         'reasoning_source':'model','adaptation':'author prompts/parsers/branching, visual transport'}
    system,user=prompts.get_prompts(benchmark,'step2',variant='ssc')
    resp=invoke('SSC','review',system,user.format(table=table,question=item['question'],answer=initial['answer'],reasoning=initial['reasoning']),item['image'])
    out['calls'].append(resp)
    if resp.get('status')!='ok':out['status']='REVIEW_FAILED';return out
    parsed=parser.parse_step2(resp.get('content',''))
    out['events'].append({'stage':'review','raw':resp.get('content',''),**parsed})
    if parsed['verdict']!='incorrect':
        out['status']='REVIEW_ACCEPTED' if parsed['verdict']=='correct' else 'REVIEW_UNKNOWN';return out
    system,user=prompts.get_prompts(benchmark,'step3')
    resp=invoke('SSC','correction',system,user.format(table=table,question=item['question'],answer=initial['answer'],error_analysis=parsed['error_analysis']),item['image'])
    out['calls'].append(resp)
    if resp.get('status')!='ok':out['status']='CORRECTION_FAILED';return out
    corrected=parser.parse_step3(resp.get('content',''))
    out['events'].append({'stage':'correction','raw':resp.get('content',''),**corrected})
    out.update(answer=corrected['corrected_answer'],status='CORRECTION_PROPOSED')
    return out

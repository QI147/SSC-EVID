"""Compact observations and loss-aware recovery of COMPLETE records only."""
import copy,json,re

def text_payload(response):
    if not isinstance(response,dict) or response.get('status')!='ok':return None
    text=response.get('content','')
    if not isinstance(text,str):return None
    text=text.strip()
    if text.startswith('```'):
        text='\n'.join(text.splitlines()[1:])
        if text.endswith('```'):text=text[:-3].rstrip()
    return text

def single_object(response):
    text=text_payload(response)
    if text is None or response.get('finish_reason')=='length':return None
    decoder=json.JSONDecoder();found=[];cursor=text.find('{')
    while cursor>=0:
        try:obj,n=decoder.raw_decode(text[cursor:])
        except ValueError:return None
        if isinstance(obj,dict):found.append(obj)
        cursor=text.find('{',cursor+n)
    return found[0] if len(found)==1 else None

def prefix_object(text):
    # Traverse an actual top-level object; never search for a 'facts' substring
    # inside an observation string, and never add missing braces or values.
    decoder=json.JSONDecoder();i=0;result={};partial=False
    def space(i):
        while i<len(text) and text[i].isspace():i+=1
        return i
    i=space(i)
    if i>=len(text) or text[i]!='{':return None
    i+=1
    while True:
        i=space(i)
        if i>=len(text):break
        if text[i]=='}':break
        try:key,n=decoder.raw_decode(text[i:])
        except ValueError:break
        if not isinstance(key,str) or key in result:return None
        i=space(i+n)
        if i>=len(text) or text[i]!=':':return None
        i=space(i+1)
        if key in ('facts','rows') and i<len(text) and text[i]=='[':
            records=[];i+=1
            while True:
                i=space(i)
                if i>=len(text):partial=True;break
                if text[i]==']':i+=1;break
                try:record,n=decoder.raw_decode(text[i:])
                except ValueError:partial=True;break
                if key=='facts' and not isinstance(record,dict):return None
                if key=='rows' and not isinstance(record,list):return None
                records.append(record);i=space(i+n)
                if i<len(text) and text[i]==',':i+=1;continue
                if i<len(text) and text[i]==']':i+=1;break
                partial=True;break
            result[key]=records
            if partial:break
        else:
            try:value,n=decoder.raw_decode(text[i:])
            except ValueError:break
            result[key]=value;i+=n
        i=space(i)
        if i<len(text) and text[i]==',':i+=1;continue
        break
    return result if result.get('facts') or result.get('rows') else None

def observation(response):
    raw=text_payload(response)
    if raw is None:return None,{'status':'SERVICE_FAILED'}
    obj=single_object(response);recovered=False
    if obj is None:
        obj=prefix_object(raw);recovered=True
    if obj is None:return None,{'status':'NO_COMPLETE_OBSERVATION'}
    facts=[];issues=[]
    if 'rows' in obj:
        if not isinstance(obj['rows'],list):return None,{'status':'INVALID_ROWS'}
        rows=obj['rows']
        prefixed=bool(rows) and any(isinstance(r,list) and len(r)==6 for r in rows) and all(isinstance(r,list) and r and isinstance(r[0],str) and re.fullmatch(r'i[0-9]+',r[0]) for r in rows)
        for index,original in enumerate(rows,1):
            row=original[1:] if prefixed else original
            known=isinstance(row,list) and 3<=len(row)<=5 and isinstance(row[0],(str,int,float)) and not isinstance(row[0],bool) and isinstance(row[1],(str,int)) and not isinstance(row[1],bool) and str(row[0]).strip() and str(row[1]).strip()
            if not known or isinstance(row[2],(dict,list)):
                facts.append({'id':'i'+str(index),'location':'unbound record '+str(index),'content':json.dumps(original,ensure_ascii=False),
                              'source':'unverified','raw_record':original,'binding_unresolved':True})
                issues.append('unbound_record:'+str(index));continue
            raw=row[2]
            e={'id':'i'+str(index),'location':'row='+str(row[0])+'; column='+str(row[1]),
               'row_key':str(row[0]),'content':raw if isinstance(raw,str) else json.dumps(raw,ensure_ascii=False),
               'source':'image' if raw is not None else 'unverified','raw_content':raw,
               'provenance':{'format':'compact_image_row_with_id' if prefixed else 'compact_image_row','call_id':response.get('call_id'),'record_index':index}}
            if raw is None:e['null_reading_unresolved']=True;issues.append('null_reading:'+str(index))
            if len(row)>3 and row[3] is not None:e['value']=row[3]
            if len(row)>4 and row[4] is not None:e['entity']=row[4]
            facts.append(e)
    else:
        if not isinstance(obj.get('facts'),list):return None,{'status':'INVALID_FACTS'}
        for index,record in enumerate(obj['facts'],1):
            if not isinstance(record,dict):
                facts.append({'id':'i'+str(index),'location':'unbound record '+str(index),'content':json.dumps(record,ensure_ascii=False),'source':'unverified','binding_unresolved':True})
                issues.append('unbound_record:'+str(index));continue
            e=copy.deepcopy(record)
            if not e.get('source'):
                e['source']='unverified';e['source_missing']=True;issues.append('source_not_declared:'+str(e.get('id','?')))
            elif e['source'] not in ('image','calculation','unverified'):
                e['raw_source']=e['source'];e['source']='unverified';issues.append('source_not_recognized:'+str(e.get('id','?')))
            if not isinstance(e.get('id'),str) or not e['id'].strip() or not isinstance(e.get('location'),str) or not e['location'].strip() or 'content' not in e:
                e={'id':'i'+str(index),'location':'unbound record '+str(index),'content':json.dumps(record,ensure_ascii=False),'source':'unverified','raw_record':record,'binding_unresolved':True}
                issues.append('unbound_record:'+str(index))
            elif not isinstance(e['content'],str):
                e['raw_content']=e['content'];e['content']=json.dumps(e['content'],ensure_ascii=False)
                if e['raw_content'] is None:e['source']='unverified';e['null_reading_unresolved']=True
            facts.append(e)
    duplicates={e['id'] for e in facts if sum(x['id']==e['id'] for x in facts)>1}
    if duplicates:
        for index,e in enumerate(facts,1):
            if e['id'] in duplicates:
                e['raw_id']=e['id'];e['id']='i_recovered_'+str(index);e['source']='unverified';e['identity_unresolved']=True
                issues.append('duplicate_id:'+e['raw_id'])
    for e in facts:
        if e.get('content')=='':
            e['raw_content']=''
            e['content']='[EXPLICIT_EMPTY_CELL]'
            e['empty_cell_observation']=True
            # This marker represents exactly an empty string in the model's
            # response, not proof that the image cell is empty and never zero.
    if not facts:return None,{'status':'NO_COMPLETE_OBSERVATION'}
    plan=obj.get('question_plan')
    if not isinstance(plan,dict):
        plan={'answer_type':'unknown','metadata_status':'not_emitted'}
        issues.append('question_plan_not_emitted')
    scope=obj.get('scope')
    if not isinstance(scope,str) or not scope.strip():
        scope='OPEN: scope declaration not emitted';issues.append('scope_not_emitted')
    unresolved=obj.get('unresolved',[])
    if not isinstance(unresolved,list):unresolved=[];issues.append('uncertainty_field_invalid')
    if recovered:
        issues.append('observation_output_incomplete')
        scope='OPEN: incomplete response; '+scope
    unresolved=list(dict.fromkeys(unresolved+issues))
    operations=obj.get('operations',[])
    if not isinstance(operations,list):operations=[];issues.append('operations_not_complete')
    normalized={'facts':facts,'question_plan':plan,'scope':scope,'unresolved':unresolved,'operations':operations}
    return normalized,{'status':'RECOVERED_COMPLETE_RECORDS' if recovered else 'COMPLETE',
                       'record_count':len(facts),'source_missing_count':sum(e.get('source_missing',False) for e in facts),
                       'range_status':'OPEN' if recovered else 'MODEL_DECLARED_ONLY','issues':issues}

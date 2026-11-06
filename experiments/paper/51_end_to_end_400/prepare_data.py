"""Build fixed cohorts from input-only projections; labels are adapted after IDs freeze."""
import argparse,collections,hashlib,json,re,shutil,textwrap
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
ROOT=Path(__file__).resolve().parent
SEED='numeric400_finqa_dev_20261030_v1'
def sha(x):return hashlib.sha256(x).hexdigest()
def load(p):return [json.loads(s) for s in p.read_text(encoding='utf-8').splitlines() if s.strip()]
def write(p,obj):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(obj,ensure_ascii=False,indent=1),encoding='utf-8')
def lines(p,rows):p.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')
def nq(q):return re.sub(r'\W+',' ',q.lower()).strip()
def project(row):
 return {'id':row['id'],'question':row['qa']['question'],'table':row['table'],
         'pre_text':row['pre_text'],'post_text':row['post_text'],'filename':row.get('filename')}
def choose(pool,old):
 used={nq(r['question']) for r in old};groups=set();selected=[];excluded=collections.Counter()
 for r in sorted(pool,key=lambda r:sha((SEED+':'+r['id']).encode())):
  tab=r['table'];group=sha(json.dumps(tab,ensure_ascii=False,separators=(',',':')).encode())
  if not tab or not 2<=len(tab)<=40 or not 2<=max(map(len,tab))<=8:excluded['table_shape']+=1;continue
  if sum(len(str(s)) for s in r['pre_text']+r['post_text'])>18000:excluded['context_chars']+=1;continue
  if len(r['question'])>1500:excluded['question_chars']+=1;continue
  if nq(r['question']) in used:excluded['duplicate_question']+=1;continue
  if group in groups:excluded['duplicate_table']+=1;continue
  selected.append(dict(r,group=group));groups.add(group);used.add(nq(r['question']))
  if len(selected)==200:break
 if len(selected)!=200:raise ValueError('Not enough eligible FinQA records: '+str(len(selected)))
 return selected,dict(excluded)
def render(record,path,font_path):
 width=1600;pad=30;font=ImageFont.truetype(font_path,21);bold=ImageFont.truetype(font_path,23)
 scratch=ImageDraw.Draw(Image.new('RGB',(width,20),'white'));items=[];y=pad
 def wrap(s,maxw):
  output=[]
  for para in str(s).split('\n'):
   current=''
   for word in para.split(' '):
    proposal=(current+' '+word).strip()
    if current and scratch.textlength(proposal,font=font)>maxw:output.append(current);current=word
    else:current=proposal
   output.append(current)
  return output
 def paragraph(s):
  nonlocal y
  for line in wrap(s,width-2*pad):items.append(('text',pad,y,line));y+=29
  y+=7
 for p in record['pre_text']:paragraph(p)
 table=record['table'];cols=max(map(len,table));first=480;other=(width-2*pad-first)/(cols-1)
 widths=[first]+[other]*(cols-1)
 for ri,row in enumerate(table):
  cells=[wrap(row[j] if j<len(row) else '',int(widths[j]-16)) for j in range(cols)]
  h=max(map(len,cells))*29+16;items.append(('row',y,h,cells,ri));y+=h
 y+=15
 for p in record['post_text']:paragraph(p)
 if y+pad>12000:raise ValueError('Rendered image exceeds fixed canvas limit')
 image=Image.new('RGB',(width,int(y+pad)),'white');draw=ImageDraw.Draw(image)
 for item in items:
  if item[0]=='text':draw.text((item[1],item[2]),item[3],font=font,fill='#17212f')
  else:
   _,top,h,cells,ri=item;x=pad
   for j,ls in enumerate(cells):
    draw.rectangle((int(x),int(top),int(x+widths[j]),int(top+h)),fill='#e8edf4' if ri==0 else '#ffffff',outline='#9ba8b5')
    for k,line in enumerate(ls):draw.text((int(x+8),int(top+7+29*k)),line,font=font,fill='#17212f')
    x+=widths[j]
 path.parent.mkdir(parents=True,exist_ok=True);image.save(path,format='PNG')
 return {'width':width,'height':image.height,'font_sha256':sha(Path(font_path).read_bytes()),'font_px':21}
def build(project_root,finqa_path,font_path):
 droot=ROOT/'datasets';numeric=droot/'numeric400';mixed=droot/'mixed400'
 if numeric.exists() or mixed.exists():raise FileExistsError('Dataset already built; no overwrite')
 numeric.mkdir(parents=True);mixed.mkdir()
 financial=load(project_root/'02_数据/21_金融200对照/runtime.jsonl')
 mixed_old=load(project_root/'02_数据/19_SSC_EVID_独立对照/runtime.jsonl')
 raw=json.loads(finqa_path.read_text(encoding='utf-8'))
 # The chooser receives no answers, programs, gold_inds, retrieval rankings or model results.
 chosen,excluded=choose([project(r) for r in raw],financial)
 write(numeric/'selection_before_eval.json',{'seed':SEED,'ids':[r['id'] for r in chosen],
  'rule':'retain finance200 + input-only fixed hash200; unique new tables/questions; full pre/table/post; no performance selection',
  'source_sha256':sha(finqa_path.read_bytes()),'excluded_before_first200':excluded})
 def retain(rows,dest,cohort):
  out=[]
  for r in rows:
   x=dict(r);image=project_root/r['image'];data=image.read_bytes()
   if sha(data)!=r['image_sha256']:raise ValueError('Old image drift '+r['sample_id'])
   target=dest/'images'/(r['sample_id']+image.suffix);target.parent.mkdir(exist_ok=True)
   target.write_bytes(data);x['image']=target.relative_to(ROOT).as_posix()
   x['cohort']=cohort;x['input_condition']='native_table_image'
   x['experiment_family']='text_wikipedia_source' if r['dataset']=='vwtq' else 'numeric_financial_source'
   out.append(x)
  return out
 nr=retain(financial,numeric,'retained_financial200');mr=retain(mixed_old,mixed,'retained_mixed400')
 render_meta=[]
 for i,r in enumerate(chosen,1):
  sid=f'FQADEV{i:03d}';p=numeric/'images'/(sid+'.png');meta=render(r,p,font_path)
  render_meta.append({'sample_id':sid,'source_id':r['id'],**meta})
  nr.append({'sample_id':sid,'dataset':'finqa_rendered','question':r['question'],
   'image':p.relative_to(ROOT).as_posix(),'image_sha256':sha(p.read_bytes()),
   'source':{'pool':'FinQA','split':'dev','qa_id':r['id'],'filename':r['filename']},
   'source_group':r['group'],'source_group_basis':'exact_input_table_json_sha256',
   'cohort':'added_finqa200','input_condition':'rendered_pretext_table_posttext',
   'experiment_family':'numeric_financial_source'})
 for dest,records in ((numeric,nr),(mixed,mr)):
  records.sort(key=lambda r:sha(('cohort400_order_v1:'+r['sample_id']).encode()))
  for i,r in enumerate(records):r['order']=i
  assert len(records)==len({r['sample_id'] for r in records})==400
  lines(dest/'runtime.jsonl',records)
 # IDs and all model inputs are now frozen; only here adapt evaluation fields.
 old_gold={r['sample_id']:r for r in load(project_root/'02_数据/21_金融200对照/eval.jsonl')}
 mixed_gold={r['sample_id']:r for r in load(project_root/'02_数据/19_SSC_EVID_独立对照/eval.jsonl')}
 lookup={r['id']:r['qa'] for r in raw}
 ngold=dict(old_gold)
 for i,r in enumerate(chosen,1):
  qa=lookup[r['id']];ngold[f'FQADEV{i:03d}']={'sample_id':f'FQADEV{i:03d}','answer':qa['answer'],'exe_ans':qa.get('exe_ans')}
 for dest,records,gold in ((numeric,nr,ngold),(mixed,mr,mixed_gold)):
  lines(dest/'eval.jsonl',[gold[r['sample_id']] for r in records])
  write(dest/'manifest.json',{'n':400,'source_counts':dict(collections.Counter(r['dataset'] for r in records)),
   'tables':len({r['source_group'] for r in records}),
   'runtime_sha256':sha((dest/'runtime.jsonl').read_bytes()),'eval_sha256':sha((dest/'eval.jsonl').read_bytes()),
   'scope':'development; source/rendering strata mandatory; not a claim every answer is a number or word',
   'historical_labels_unchanged':True})
 write(numeric/'rendering.json',{'version':'finqa_full_context_pil_v1','records':render_meta})
 write(droot/'naming_and_overlap.json',{'numeric400':'原金融200+FinQA dev渲染200','mixed400':'原174金融+226百科不变',
  'text_label':'百科来源显示别名，不等于所有任务都是文本答案','shared_qa_ids':len({r['sample_id'] for r in nr}&{r['sample_id'] for r in mr}),
  'unique_qa_ids':len({r['sample_id'] for r in nr}|{r['sample_id'] for r in mr}),
  'historical_finance200_not_renamed_or_overwritten':True})
 print(json.dumps({x.name:json.loads((x/'manifest.json').read_text(encoding='utf-8')) for x in (numeric,mixed)},ensure_ascii=False,indent=1))
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('--project-root',type=Path,required=True);ap.add_argument('--finqa-dev',type=Path,required=True);ap.add_argument('--font',required=True);args=ap.parse_args()
 build(args.project_root,args.finqa_dev,args.font)

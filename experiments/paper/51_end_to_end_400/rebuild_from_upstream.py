"""Rebuild model inputs from frozen source IDs without the historical D10 checkout."""
import argparse,json,re,hashlib
from pathlib import Path
import prepare_data as P
ROOT=Path(__file__).resolve().parent
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--tablevqa-directory',type=Path,required=True);ap.add_argument('--finqa-dev',type=Path,required=True);ap.add_argument('--font',required=True);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
 if a.output.exists():raise FileExistsError('Use a fresh output directory')
 import pyarrow.parquet as pq
 spec=json.loads((ROOT/'REBUILD_SPEC.json').read_text(encoding='utf-8'))
 raw={}
 for source in ('fintabnetqa','vwtq'):
  p=next(a.tablevqa_directory.glob(source+'-*.parquet'))
  if P.sha(p.read_bytes())!=spec['source_sha256'][source]:raise ValueError('upstream parquet drift')
  raw[source]={r['qa_id']:r for r in pq.read_table(p,columns=['qa_id','question','image','gt']).to_pylist()}
 if P.sha(a.finqa_dev.read_bytes())!=spec['source_sha256']['finqa_dev']:raise ValueError('FinQA dev drift')
 finqa={r['id']:r for r in json.loads(a.finqa_dev.read_text(encoding='utf-8'))}
 for dataset,records in spec['records'].items():
  rt=[];gold=[];dest=a.output/dataset;dest.mkdir(parents=True)
  for frozen in records:
   r=dict(frozen);p=dest/'images'/Path(r['image']).name
   if r['dataset']=='finqa_rendered':
    source=finqa[r['source']['qa_id']];P.render(P.project(source),p,a.font)
    g={'sample_id':r['sample_id'],'answer':source['qa']['answer'],'exe_ans':source['qa'].get('exe_ans')}
   else:
    source=raw[r['dataset']][r['source']['qa_id']];p.parent.mkdir(exist_ok=True);p.write_bytes(source['image']['bytes'])
    answer=source['gt'];answer=answer[0] if isinstance(answer,list) and answer else answer
    try:exe=float(str(answer).replace(',',''))
    except (ValueError,TypeError):exe=None
    g={'sample_id':r['sample_id'],'answer':answer,'exe_ans':exe}
   if P.sha(p.read_bytes())!=r['image_sha256']:raise ValueError('Rebuilt image differs; verify font/platform: '+r['sample_id'])
   rt.append(r);gold.append(g)
  P.lines(dest/'runtime.jsonl',rt);P.lines(dest/'eval.jsonl',gold)
 print('Rebuilt and checked all image bytes; point a new experiment root at the rebuilt datasets.')
if __name__=='__main__':main()

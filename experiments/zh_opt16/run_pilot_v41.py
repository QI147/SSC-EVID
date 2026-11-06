from pathlib import Path
import os,sys,subprocess,json
root=Path(__file__).resolve().parents[2]  # repository root
os.chdir(root)
env=os.environ.copy()
env['PYTHONIOENCODING']='utf-8'
cred=Path(env.get('EVID_CRED_FILE') or (root/'.env'))
for line in (cred.read_text(encoding='utf-8-sig').splitlines() if cred.exists() else []):
    if line.strip() and not line.lstrip().startswith('#') and '=' in line:
        key,value=line.split('=',1);env[key.strip()]=value.strip().strip('"').strip("'")
if not (env.get('EVID_API_KEY') or env.get('BAILIAN_API_KEY')):
    raise SystemExit('Missing credentials; zero requests')
env.update(EVID_MODEL='qwen3.8-flash',EVID_BASE_URL='https://dashscope.aliyuncs.com/compatible-mode/v1',EVID_ALLOWED_HOST='dashscope.aliyuncs.com',EVID_ENABLE_THINKING='0',EVID_SEND_THINKING='1',EVID_TRANSPORT_ATTEMPTS='1',EVID_E2E_ARMS='ssc,evid',EVID_MAX_TOKENS='1200')
env.pop('EVID_ROOT',None)
for variant in ['41']:
    env['EVID_PROMPT_VARIANT']=variant
    dataset='zh_opt16_v'+variant
    for command,extra in [('freeze',[]),('run',['--shard','0/1','--authorize-paid']),('merge',['--shards','1']),('verify',[])]:
        if command=='freeze' and (root/'runs'/dataset/'freeze.json').exists():continue
        result=subprocess.run([sys.executable,'-m','repro.runner',command,'--dataset',dataset]+extra,env=env)
        if result.returncode:raise SystemExit(result.returncode)
    result=subprocess.run([sys.executable,'scripts/zh_robust_score.py',dataset,'--by-task'],env=env)
    if result.returncode:raise SystemExit(result.returncode)
print('PAID PILOT COMPLETE',flush=True)

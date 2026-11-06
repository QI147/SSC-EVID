# -*- coding: utf-8 -*-
"""实验四：完整流程的端到端模块消融（五臂）。

五臂
----
FULL           完整 EVID36（原样调用冻结方法）
NO_FACT_FIRST  事实先行初态 → GENERIC2 两次读初态
GENERIC_ACQ    定向动作提示 → 通用重新观察提示
NO_ACQ         跳过新增取证
NO_GATE        跳过最终语义复核（保留候选结构性校验）

纪律
----
* 新批次、新冻结、新账本；不写 51/53/57/58 的任何文件（见 history_lock.json）。
* 缓存身份 = sha256(模型 | 完整提示文本 | 图像 sha | max_tokens | temperature | thinking | response_mode)，
  **不含实验臂**，因此相同前缀跨臂共享；分支不同则自动不再命中。
* 全批硬顶走文件锁配额，与分片数无关；每次真实尝试计数，失败不退还。
* 端点与模型从实际配置读取；冻结时记录，运行时逐字比对，漂移即拒绝发请求。
* 运行进程不导入 scoring/evaluate。
"""
import argparse
import collections
import glob
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
PKG = ROOT.parent / '51_SSC_EVID_400统一对照'
sys.path.insert(0, str(PKG))
sys.path.insert(0, str(ROOT))

import caller as API  # noqa: E402
import experiment as X  # noqa: E402
from common import sha256_file  # noqa: E402
import arms  # noqa: E402

DATASETS = ('mixed400', 'numeric400')
PROTOCOL = ROOT / 'protocol.json'
RUNS = ROOT / 'runs' / 'main'
CAP_DEFAULT = 9000
SHARDS = 8


def rows(p):
    return [json.loads(s) for s in Path(p).read_text(encoding='utf-8').splitlines() if s.strip()]


def canonical(obj):
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def design_protocol():
    """冻结用的协议视图：剔除授权字段，避免"授权即漂移"。"""
    d = json.loads(PROTOCOL.read_text(encoding='utf-8'))
    for k in ('authorized', 'authorized_at', 'authorization', 'status', 'paid_requests_made',
              'authorization_note', 'cap_rationale'):
        d.pop(k, None)
    return d


# ---------------------------------------------------------------- 样本
def items():
    """626 个唯一 (图像 sha, 问题)；members 记录 800 条逻辑样本的映射。"""
    out, index = [], {}
    for ds in DATASETS:
        for r in rows(PKG / 'datasets' / ds / 'runtime.jsonl'):
            key = canonical({'image_sha256': r['image_sha256'], 'question': r['question']})
            member = {'dataset': ds, 'sample_id': r['sample_id'], 'source': r['dataset'],
                      'source_group': r['source_group']}
            if key in index:
                index[key]['members'].append(member)
                continue
            rec = {'key': key, 'image': r['image'], 'image_sha256': r['image_sha256'],
                   'question': r['question'], 'members': [member], 'first_dataset': ds,
                   'order': len(out)}
            index[key] = rec
            out.append(rec)
    return out


def logical_count():
    return sum(len(r['members']) for r in items())


# ---------------------------------------------------------------- 冻结
def sources():
    # protocol.json 不按文件字节冻结：授权开关会改动它，但方法设计不变。
    # 设计视图已作为 snapshot()['protocol'] 整体进入冻结值。
    files = [ROOT / 'arms.py', ROOT / 'run59.py', ROOT / 'score59.py', ROOT / 'tests59.py',
             ROOT / 'history_lock.json',
             PKG / 'experiment.py', PKG / 'caller.py', PKG / 'common.py',
             PKG / 'scoring.py', PKG / 'numeric_helpers.py', PKG / 'display_scoring.py',
             PKG / 'json_helpers.py']
    files += [p for p in (PKG / 'methods' / 'evid36').glob('*.py')]
    files += [PKG / 'datasets' / ds / f for ds in DATASETS for f in ('runtime.jsonl', 'eval.jsonl')]
    return [p for p in files if p.exists()]


def endpoint():
    env = API.load_env()
    u = urlparse(env.get('BAILIAN_BASE_URL', ''))
    return {'scheme': u.scheme, 'host': u.hostname, 'path': u.path,
            'source': 'BAILIAN_CRED_FILE / 环境变量', 'key_present': bool(env.get('BAILIAN_API_KEY'))}


def phase_cost_basis():
    """各阶段单价，直接从既有账本算，不手写。"""
    acc = collections.defaultdict(lambda: {'n': 0, 'cost': 0.0, 'pt': 0, 'ct': 0})
    for pattern in (str(PKG / 'runs' / '*' / 'e2e' / '*_calls.jsonl'),
                    str(ROOT.parent / '58_实验四端到端消融' / 'runs' / 'e2e' / '*_calls.jsonl')):
        for p in glob.glob(pattern):
            for r in rows(p):
                if r.get('reused'):
                    continue
                u = r.get('usage') or {}
                if 'prompt_tokens' not in u:
                    continue
                b = acc[r['purpose']]
                b['n'] += 1
                b['cost'] += float(r.get('cost_cny') or 0)
                b['pt'] += u.get('prompt_tokens', 0)
                b['ct'] += u.get('completion_tokens', 0)
    out = {}
    for k, b in acc.items():
        if not b['n']:
            continue
        out[k] = {'n': b['n'], 'cost_per_call': round(b['cost'] / b['n'], 6),
                  'prompt_tokens_mean': round(b['pt'] / b['n'], 1),
                  'completion_tokens_mean': round(b['ct'] / b['n'], 1)}
    return out


def snapshot():
    files = {}
    for p in sources():
        files[p.relative_to(ROOT.parent).as_posix()] = sha256_file(p)
    its = items()
    for it in its:
        img = PKG / it['image']
        if sha256_file(img) != it['image_sha256']:
            raise ValueError('image drift ' + it['members'][0]['sample_id'])
        files[img.relative_to(ROOT.parent).as_posix()] = it['image_sha256']
    for ds in DATASETS:
        for p in glob.glob(str(PKG / 'datasets' / ds / 'images' / '*')):
            rel = Path(p).relative_to(ROOT.parent).as_posix()
            if rel not in files:
                files[rel] = sha256_file(Path(p))
    cfg = json.loads(PROTOCOL.read_text(encoding='utf-8'))
    obj = {'files': files,
           'model': {'name': X.MODEL['name'], 'temperature': X.MODEL['temperature'],
                     'thinking': X.MODEL['thinking'], 'max_tokens': X.MODEL['max_tokens'],
                     'retry': X.MODEL['retry'],
                     'price_cny_per_mtok': API.PRICE},
           'endpoint': endpoint(),
           'datasets': list(DATASETS),
           'arms': list(arms.ARMS),
           'max_calls_per_item': arms.MAX_CALLS,
           'cap': cfg.get('cap', CAP_DEFAULT),
           'items': {'unique': len(its), 'logical': logical_count(),
                     'sha256': canonical([{'key': i['key'], 'image_sha256': i['image_sha256'],
                                           'question': i['question'],
                                           'members': i['members']} for i in its])},
           'cache_identity': 'sha256(model|prompt_text|image_sha256|max_tokens|temperature|thinking|response_mode); no arm label',
           'phase_cost_basis': phase_cost_basis(),
           'protocol': design_protocol()}
    obj['sha256'] = canonical(obj)
    return obj


def freeze(reason):
    if (ROOT / 'freeze.json').exists():
        old = json.loads((ROOT / 'freeze.json').read_text(encoding='utf-8'))['sha256']
    else:
        old = None
    obj = snapshot()
    X.dump(ROOT / 'freeze.json', obj)
    with (ROOT / 'freeze_log.jsonl').open('a', encoding='utf-8') as fh:
        fh.write(json.dumps({'at': X.time.strftime('%Y-%m-%d %H:%M:%S'), 'sha256': obj['sha256'],
                             'previous': old, 'reason': reason}, ensure_ascii=False) + '\n')
    return obj


def verify():
    p = ROOT / 'freeze.json'
    if not p.exists():
        raise ValueError('no freeze')
    f = json.loads(p.read_text(encoding='utf-8'))
    if f != snapshot():
        raise ValueError('freeze drift; refuse to spend')
    return f


# ---------------------------------------------------------------- 运输
class Transport:
    def __init__(self, shard, freeze_obj):
        self.f = freeze_obj
        RUNS.mkdir(parents=True, exist_ok=True)
        e = endpoint()
        if e['scheme'] != 'https' or not e['host'] or not e['key_present']:
            raise ValueError('missing credential or unapproved destination')
        if {'scheme': e['scheme'], 'host': e['host'], 'path': e['path']} != \
                {k: self.f['endpoint'][k] for k in ('scheme', 'host', 'path')}:
            raise ValueError('endpoint drift vs freeze: %s' % e)
        API.MODEL = X.MODEL['name']
        API.TEMP = X.MODEL['temperature']
        API.THINK = X.MODEL['thinking']
        API.MAXTOK = X.MODEL['max_tokens']
        API.RETRY = X.MODEL['retry']
        self.cap = self.f['cap']
        self.api = API.Caller(str(RUNS), {'per_sample': {}, 'batch_total': self.cap},
                              run_prefix='m5_' + self.f['sha256'][:10] + '_s%d' % shard,
                              response_mode='text')
        self.quota = X.Quota(RUNS, self.cap)
        self.log = RUNS / ('requests_s%d.jsonl' % shard)
        self.count = collections.Counter()
        self.count_real = collections.Counter()
        self.first_arm = {}

    def invoke_for(self, item, arm):
        def invoke(role, phase, system, prompt, image=None):
            return self.call(item, arm, role, phase, system, prompt, image)
        return invoke

    def call(self, item, arm, role, phase, system, prompt, image=None):
        self.count[arm] += 1
        if self.count[arm] > arms.MAX_CALLS[arm]:
            raise X.BudgetStop('per-item arm cap %s (%s)' % (arms.MAX_CALLS[arm], arm))
        text = (system or '') + '\n\n' + prompt
        img_path = PKG / item['image']
        image_sha = item['image_sha256']
        fp = self.api._fingerprint(image_sha, text, None)
        hit = fp in self.api.cache
        if not hit:
            self.quota.take()
            self.count_real[arm] += 1
            self.first_arm[fp] = arm
        raw, meta = self.api.call(arm, item['members'][0]['sample_id'], str(img_path),
                                  text, purpose=phase, request_scope=None)
        if meta.get('abort'):
            raise X.BudgetStop(meta.get('abort_reason', 'role quota'))
        err = str(meta.get('error') or '').lower()
        if meta.get('status') != 'ok' and any(s in err for s in ('429', 'quota', 'rate limit',
                                                                 'timed out', 'connection', 'ssl', 'tls')):
            with (RUNS / 'infra_incidents.jsonl').open('a', encoding='utf-8') as fh:
                fh.write(json.dumps({'at': X.time.strftime('%Y-%m-%d %H:%M:%S'), 'arm': arm,
                                     'phase': phase, 'item': item['key'], 'error': meta.get('error'),
                                     'call_id': meta.get('call_id')}, ensure_ascii=False) + '\n')
            raise X.BudgetStop('infrastructure interruption; cache preserved')
        u = meta.get('usage') or {}
        rec = {'item': item['key'], 'dataset': item['first_dataset'], 'arm': arm, 'role': role,
               'phase': phase, 'content_key': fp, 'hit': hit,
               'call_id': meta.get('call_id'), 'first_call_id': meta.get('first_call_id'),
               'first_arm': self.first_arm.get(fp),
               'requests': meta.get('requests', 0), 'reused': bool(meta.get('reused')),
               'status': meta.get('status'), 'error': meta.get('error'),
               'finish_reason': meta.get('finish_reason'),
               'cost_cny': meta.get('cost_cny'), 'prompt_tokens': u.get('prompt_tokens'),
               'completion_tokens': u.get('completion_tokens'),
               'prompt_chars': len(text)}
        with self.log.open('a', encoding='utf-8') as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + '\n')
        return {'status': meta.get('status'),
                'content': raw if isinstance(raw, str) else '',
                **{k: meta.get(k) for k in ('call_id', 'finish_reason', 'usage', 'cost_cny', 'error')},
                'requests': meta.get('requests', 0), 'reused': bool(meta.get('reused')),
                'content_key': fp, 'hit': hit}


def guard_item(f, item):
    img = PKG / item['image']
    rel = img.relative_to(ROOT.parent).as_posix()
    if not img.exists():
        raise ValueError('image missing ' + rel)
    if rel not in f['files']:
        raise ValueError('image not in freeze ' + rel)
    if sha256_file(img) != f['files'][rel]:
        raise ValueError('image drift ' + rel)


def run_one(tp, item):
    guard_item(tp.f, item)
    rec = {'item': item['key'], 'image_sha256': item['image_sha256'],
           'question': item['question'], 'members': item['members'],
           'freeze_sha256': tp.f['sha256'], 'arms': {}}
    for arm in arms.ARMS:
        tp.count.clear()
        tp.count_real.clear()
        init, init_rec, out = arms.run_arm(arm, item, tp.invoke_for(item, arm))
        arm_rec = {
            'answer': out.get('answer'),
            'status': out.get('status'),
            'initial_answer': (init or {}).get('answer'),
            'initial_ok': bool(init and arms.E36.valid_answer(init.get('answer'))),
            'logical_calls': sum(tp.count.values()),
            'real_calls': sum(tp.count_real.values()),
            'role_calls': dict(tp.count),
            'chain_calls': len(out.get('calls') or []),
            'check_verdict': (out.get('check') or {}).get('verdict'),
            'gap_type': ((out.get('check') or {}).get('gap') or {}).get('type'),
            'action': ((out.get('check') or {}).get('action')),
            'final_verdict': (out.get('final_check') or {}).get('verdict'),
            'gate_skipped': bool(out.get('gate_skipped')),
            'evidence_versions': len(out.get('evidence_versions') or []),
            'unresolved': out.get('unresolved') or [],
            'events': [e for e in (out.get('events') or []) if e.get('phase') in
                       ('action_normalized', 'action_selected', 'acquisition_skipped',
                        'final_gate_skipped', 'check_without_cited_observation')],
        }
        if arm == 'NO_FACT_FIRST':
            arm_rec['initial_issue'] = init_rec.get('issue')
            arm_rec['second_receives'] = init_rec.get('second_receives')
        rec['arms'][arm] = arm_rec
    rec['elapsed_at'] = X.time.strftime('%Y-%m-%d %H:%M:%S')
    return rec


# ---------------------------------------------------------------- 预算
def budget():
    its = items()
    n_u, n_l = len(its), logical_count()
    mx = arms.MAX_CALLS
    logical_max = n_u * sum(mx.values())
    structural_max = n_u * (mx['FULL'] + mx['NO_FACT_FIRST'] + 3 + 2 + 0)
    pc = phase_cost_basis()

    def c(name, default=0.0):
        return pc.get(name, {}).get('cost_per_call', default)

    per_item_full = (c('observe_facts') + c('initial_answer') + c('evidence_check')
                     + c('acquire_A_VALUE') + c('evidence_reassess_and_correct') + c('recheck'))
    per_item_nf = (c('common_once') + c('reread') + c('evidence_check')
                   + c('acquire_A_VALUE') + c('evidence_reassess_and_correct') + c('recheck'))
    step = (c('acquire_GENERIC', c('acquire_A_VALUE')) + c('evidence_reassess_and_correct') + c('recheck'))
    noacq = c('evidence_reassess_and_correct') + c('recheck')
    logical_cost = n_u * (per_item_full + per_item_nf + step + noacq)
    # 早期停止比例：51 e2e 的 evidence_check 有 797 次、acquire 共 37 次
    proceed = 37 / 797
    expected_calls = n_u * (3 + proceed * 3) * 2 + n_u * proceed * 3 + n_u * proceed * 2
    expected_cost = (n_u * (c('observe_facts') + c('initial_answer') + c('evidence_check'))
                     + n_u * (c('common_once') + c('reread') + c('evidence_check'))
                     + n_u * proceed * step + n_u * proceed * noacq)
    return {'unique_items': n_u, 'logical_items': n_l,
            'max_calls_per_item': mx,
            'logical_request_max': logical_max,
            'structural_request_max': structural_max,
            'expected_calls': int(round(expected_calls)),
            'expected_cost_cny': round(expected_cost, 2),
            'logical_cost_ceiling_cny': round(logical_cost, 2),
            'cap': json.loads(PROTOCOL.read_text(encoding='utf-8')).get('cap', CAP_DEFAULT),
            'proceed_rate_basis': {'acquire_calls': 37, 'evidence_checks': 797,
                                   'rate': round(proceed, 4),
                                   'source': '51 e2e 2026-11-02 账本'},
            'phase_cost_basis': pc}


# ---------------------------------------------------------------- 命令
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=('freeze', 'verify', 'dry', 'budget', 'items', 'run', 'all', 'merge'))
    ap.add_argument('--shard', default=None)
    ap.add_argument('--authorize-paid', action='store_true')
    ap.add_argument('--reason', default='initial')
    a = ap.parse_args()

    if a.cmd == 'freeze':
        obj = freeze(a.reason)
        print(obj['sha256'])
        return
    if a.cmd == 'verify':
        f = verify()
        print(json.dumps({'ok': True, 'sha256': f['sha256'], 'files': len(f['files']),
                          'items': f['items']}, ensure_ascii=False))
        return
    if a.cmd == 'dry':
        print(json.dumps(budget(), ensure_ascii=False, indent=1))
        return
    if a.cmd == 'budget':
        b = budget()
        print(json.dumps({k: b[k] for k in ('logical_request_max', 'structural_request_max',
                                            'expected_calls', 'expected_cost_cny',
                                            'logical_cost_ceiling_cny', 'cap')}, ensure_ascii=False))
        return
    if a.cmd == 'items':
        its = items()
        print(json.dumps({'unique': len(its), 'logical': logical_count(),
                          'mixed_only': sum(1 for i in its if len(i['members']) == 1 and i['first_dataset'] == 'mixed400'),
                          'shared': sum(1 for i in its if len(i['members']) > 1)}, ensure_ascii=False))
        return

    f = verify()
    its = items()
    if a.cmd == 'merge':
        out = []
        for i in range(SHARDS):
            p = RUNS / ('results_s%d.jsonl' % i)
            if p.exists():
                out += rows(p)
        keys = [r['item'] for r in out]
        if len(keys) != len(set(keys)):
            raise ValueError('duplicate items in merge')
        order = {it['key']: it['order'] for it in its}
        out.sort(key=lambda r: order[r['item']])
        X.dump(RUNS / 'results.json', out)
        print(json.dumps({'items': len(out), 'missing': len(set(order) - set(keys))}, ensure_ascii=False))
        return
    if not json.loads(PROTOCOL.read_text(encoding='utf-8')).get('authorized'):
        raise SystemExit('protocol not authorized; refusing to spend')
    if not a.authorize_paid:
        raise SystemExit('missing --authorize-paid')

    if a.cmd == 'run':
        if not a.shard:
            raise SystemExit('need --shard i/n')
        i, n = (int(x) for x in a.shard.split('/'))
        out_path = RUNS / ('results_s%d.jsonl' % i)
        tp = Transport(i, f)
        done = {r['item'] for r in rows(out_path)} if out_path.exists() else set()
        with out_path.open('a', encoding='utf-8') as fh:
            for k, item in enumerate(its):
                if k % n != i or item['key'] in done:
                    continue
                rec = run_one(tp, item)
                fh.write(json.dumps(rec, ensure_ascii=False) + '\n')
                fh.flush()
        print('shard', a.shard, 'done')
        return

    if a.cmd == 'all':
        RUNS.mkdir(parents=True, exist_ok=True)
        procs = []
        for i in range(SHARDS):
            log = open(RUNS / ('worker_s%d.log' % i), 'w', encoding='utf-8')
            procs.append(subprocess.Popen([sys.executable, str(ROOT / 'run59.py'), 'run',
                                           '--shard', '%d/%d' % (i, SHARDS), '--authorize-paid'],
                                          stdout=log, stderr=subprocess.STDOUT))
        code = 0
        for i, p in enumerate(procs):
            rc = p.wait()
            if rc:
                code = rc
                print('worker', i, 'exit', rc)
        print('all workers done', code)
        if code == 0:
            subprocess.run([sys.executable, str(ROOT / 'run59.py'), 'merge'], check=False)
        raise SystemExit(code)


if __name__ == '__main__':
    main()

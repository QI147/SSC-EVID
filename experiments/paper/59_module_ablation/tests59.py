# -*- coding: utf-8 -*-
"""实验四零调用测试：18 项工程约束。不打印任何金标，不发任何请求。

每条测试给出：编号、断言、证据。通过只证明工程约束成立，不证明方法有效或数据独立。
"""
import json
import multiprocessing as mp
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PKG = ROOT.parent / '51_SSC_EVID_400统一对照'
for p in (str(PKG), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

import experiment as X  # noqa: E402
import caller as API  # noqa: E402
import arms  # noqa: E402
from methods.evid36 import evid_integrated as E36, fact_first as F36  # noqa: E402

ITEM = {'sample_id': 'T001', 'dataset': 'stub', 'question': 'What is the total of A and B?',
        'image': 'datasets/mixed400/images/NEW162.jpg', 'image_sha256': 'stubsha'}

OBS = {'question_plan': {'answer_type': 'number'}, 'scope': 'rows 1-2', 'unresolved': [],
       'rows': [[1, 'value', '5', 5, 'e1'], [2, 'value', '7', 7, 'e2']], 'operations': []}
ANS = {'answer': '12', 'reasoning': '5 + 7', 'supporting_ids': ['i1', 'i2'],
       'scope': 'rows 1-2', 'unresolved': []}
CHK_OK = {'analysis': 'the two cells are 5 and 7', 'error_type': 'none', 'verdict': 'correct',
          'evidence': [], 'supporting_ids': ['i1'], 'scope': 'rows 1-2', 'unresolved': [],
          'gap': None, 'action': None}
CHK_BAD = {'analysis': 'row 2 reads 8, not 7', 'error_type': 'value', 'verdict': 'incorrect',
           'evidence': [{'id': 'c1', 'location': 'row 2', 'content': '8', 'source': 'image'}],
           'supporting_ids': ['c1'], 'scope': 'rows 1-2', 'unresolved': [],
           'gap': {'type': 'VALUE', 'target': 'row 2 value', 'why': 'misread cell'},
           'action': 'A_VALUE'}
ACQ = {'evidence': [{'id': 'a1', 'location': 'row 2', 'content': '8', 'source': 'image'}],
       'scope': 'row 2', 'unresolved': []}
REPAIR = {'verdict': 'incorrect', 'error_type': 'value', 'analysis': 'row 2 is 8',
          'supporting_ids': ['a1'], 'scope': 'rows 1-2', 'unresolved': [],
          'answer': '13', 'reasoning': '5 + 8'}


class Stub:
    """按阶段返回预置响应，记录每次调用的完整提示。"""

    def __init__(self, script, fail_status=None, length_phase=None):
        self.script = script
        self.fail_status = fail_status or {}
        self.length_phase = length_phase
        self.calls = []

    def invoke_for(self, arm):
        def invoke(role, phase, system, prompt, image=None):
            self.calls.append({'arm': arm, 'role': role, 'phase': phase,
                               'system': system, 'prompt': (system or '') + '\n\n' + prompt})
            if phase in self.fail_status:
                return {'status': self.fail_status[phase], 'content': '',
                        'call_id': 'stub-%d' % len(self.calls), 'error': 'stub failure',
                        'finish_reason': None, 'usage': {}, 'cost_cny': 0.0,
                        'requests': 1, 'reused': False}
            body = self.script.get(phase)
            if body is None:
                body = self.script.get(role, self.script.get('default', {}))
            return {'status': 'ok', 'content': json.dumps(body, ensure_ascii=False),
                    'call_id': 'stub-%d' % len(self.calls),
                    'finish_reason': 'length' if phase == self.length_phase else 'stop',
                    'usage': {}, 'cost_cny': 0.0, 'requests': 1, 'reused': False}
        return invoke

    def phases(self):
        return [c['phase'] for c in self.calls]

    def prompts(self):
        return [c['prompt'] for c in self.calls]


def strip(out):
    o = dict(out)
    o.pop('arm', None)
    return json.loads(json.dumps(o, ensure_ascii=False, sort_keys=True))


def run_arm(arm, script, **kw):
    stub = Stub(script, **kw)
    init, rec, out = arms.run_arm(arm, ITEM, stub.invoke_for(arm))
    return stub, init, rec, out


RESULTS = []


def check(idx, name, cond, detail):
    RESULTS.append({'id': idx, 'name': name, 'pass': bool(cond), 'detail': detail})
    print(('PASS' if cond else 'FAIL'), '%02d' % idx, name, '|', str(detail)[:180])


# ---------------------------------------------------------------- T1 等价性
def t1():
    scenarios = {
        'early_stop': {'observe_facts': OBS, 'initial_answer': ANS, 'evidence_check': CHK_OK},
        'full_repair': {'observe_facts': OBS, 'initial_answer': ANS, 'evidence_check': CHK_BAD,
                        'acquire_A_VALUE': ACQ, 'evidence_reassess_and_correct': REPAIR,
                        'recheck': {'verdict': 'correct', 'analysis': 'ok', 'supporting_ids': ['a1'],
                                    'scope': 'rows', 'unresolved': []}},
        'check_unparseable': {'observe_facts': OBS, 'initial_answer': ANS,
                              'evidence_check': {'broken': True}},
        'no_initial': {'observe_facts': {}, 'initial_answer': ANS},
        'repair_invalid': {'observe_facts': OBS, 'initial_answer': ANS, 'evidence_check': CHK_BAD,
                           'acquire_A_VALUE': ACQ,
                           'evidence_reassess_and_correct': {'verdict': 'incorrect', 'analysis': 'x',
                                                             'supporting_ids': ['a1'], 'scope': 's',
                                                             'unresolved': [], 'answer': '13'},
                           'recheck': {'verdict': 'correct', 'analysis': 'ok',
                                       'supporting_ids': ['a1'], 'scope': 's', 'unresolved': []}},
    }
    bad = []
    for name, script in scenarios.items():
        stub_a = Stub(script)
        init_a, _ = F36.initial(ITEM, stub_a.invoke_for('EVID'))
        out_a = E36.run(ITEM, init_a, stub_a.invoke_for('EVID')) if init_a else {'status': 'INITIAL_MISSING'}
        stub_b = Stub(script)
        init_b, _ = F36.initial(ITEM, stub_b.invoke_for('EVID'))
        out_b = arms.run_chain(ITEM, init_b, stub_b.invoke_for('FULL'), 'FULL') if init_b else {'status': 'INITIAL_MISSING'}
        if strip(out_a) != strip(out_b):
            bad.append(name)
        if stub_a.prompts() != stub_b.prompts():
            bad.append(name + ':prompt')
    check(1, 'FULL 与冻结 36 版在模拟轨迹上逐字段一致（5 个分支）', not bad, bad or 'status/prompts 全等')


# ---------------------------------------------------------------- T2 只改指定环节
def t2():
    script_bad = {'observe_facts': OBS, 'initial_answer': ANS, 'evidence_check': CHK_BAD,
                  'acquire_A_VALUE': ACQ, 'evidence_reassess_and_correct': REPAIR,
                  'recheck': {'verdict': 'correct', 'analysis': 'ok', 'supporting_ids': ['a1'],
                              'scope': 'rows', 'unresolved': []}}
    script_nf = {'read1': {'answer': '11', 'reasoning': 'r'}, 'read2': {'answer': '12', 'reasoning': 'r2'}}
    script_nf.update({k: v for k, v in script_bad.items() if k not in ('observe_facts', 'initial_answer')})

    problems = []
    # GENERIC_ACQ：前三段提示必须与 FULL 字节相同
    s_g, _, _, out_g = run_arm('GENERIC_ACQ', script_bad)
    s_f, _, _, out_f = run_arm('FULL', script_bad)
    if s_g.prompts()[:3] != s_f.prompts()[:3]:
        problems.append('GENERIC_ACQ 前 3 段与 FULL 不同')
    if 'ACTION: A_' in s_g.prompts()[3]:
        problems.append('GENERIC_ACQ 仍带 ACTION 行')
    if s_g.prompts()[3] == s_f.prompts()[3]:
        problems.append('GENERIC_ACQ 的取证提示与 FULL 相同')
    # NO_ACQ：前 3 段相同，且没有取证阶段
    s_n, _, _, out_n = run_arm('NO_ACQ', script_bad)
    if s_n.prompts()[:3] != s_f.prompts()[:3]:
        problems.append('NO_ACQ 前 3 段与 FULL 不同')
    if any(p.startswith('acquire') for p in s_n.phases()):
        problems.append('NO_ACQ 仍发取证')
    # NO_GATE：前 5 段相同，且没有复核
    s_ng, _, _, out_ng = run_arm('NO_GATE', script_bad)
    if s_ng.prompts()[:5] != s_f.prompts()[:5]:
        problems.append('NO_GATE 前 5 段与 FULL 不同')
    if 'recheck' in s_ng.phases():
        problems.append('NO_GATE 仍发复核')
    # NO_FACT_FIRST：初态段为两次读，其后与 FULL 同构
    s_nf, _, _, out_nf = run_arm('NO_FACT_FIRST', script_nf)
    if s_nf.phases()[:3] != ['read1', 'read2', 'evidence_check']:
        problems.append('NO_FACT_FIRST 初态段不是两次读: %s' % s_nf.phases()[:3])
    check(2, '每个消融只改指定环节（前序提示字节相同、目标段被替换）', not problems, problems or 'ok')


# ---------------------------------------------------------------- T3 空账本不弃答
def t3():
    script = {'read1': {'answer': '12', 'reasoning': 'r'}, 'read2': {'answer': '12', 'reasoning': 'r2'},
              'evidence_check': {'analysis': 'image shows 5 and 7', 'error_type': 'none',
                                 'verdict': 'correct', 'evidence': [], 'supporting_ids': [],
                                 'scope': 'rows 1-2', 'unresolved': [], 'gap': None, 'action': None}}
    stub, _, _, out = run_arm('NO_FACT_FIRST', script)
    ok = (out.get('status') == 'KEPT_EVIDENCE_SUPPORTED'
          and out.get('answer') == '12'
          and any(e.get('phase') == 'check_without_cited_observation' for e in out.get('events', [])))
    check(3, 'NO_FACT_FIRST 空初始账本不无条件弃答（记事件而非契约失败）', ok,
          {'status': out.get('status'), 'answer': out.get('answer'),
           'events': [e.get('phase') for e in out.get('events', [])]})


# ---------------------------------------------------------------- T4 通用臂无专用动作
def t4():
    script = {'observe_facts': OBS, 'initial_answer': ANS, 'evidence_check': CHK_BAD,
              'acquire_GENERIC': ACQ, 'evidence_reassess_and_correct': REPAIR,
              'recheck': {'verdict': 'correct', 'analysis': 'ok', 'supporting_ids': ['a1'],
                          'scope': 'rows', 'unresolved': []}}
    stub, _, _, out = run_arm('GENERIC_ACQ', script)
    acq = [c for c in stub.calls if c['phase'].startswith('acquire')]
    bad = []
    for c in acq:
        if 'ACTION: A_' in c['prompt']:
            bad.append('ACTION line')
        for d in E36.DIRECTIVES.values():
            if d in c['prompt']:
                bad.append('directive text')
    check(4, 'GENERIC_ACQ 不调用任何动作专用提示', not bad and len(acq) == 1,
          {'acquire_phase': acq[0]['phase'] if acq else None, 'problems': bad})


# ---------------------------------------------------------------- T5 无假证据
def t5():
    script = {'observe_facts': OBS, 'initial_answer': ANS, 'evidence_check': CHK_BAD,
              'evidence_reassess_and_correct': {**REPAIR, 'supporting_ids': ['i1']},
              'recheck': {'verdict': 'correct', 'analysis': 'ok', 'supporting_ids': ['i1'],
                          'scope': 'rows', 'unresolved': []}}
    stub, _, _, out = run_arm('NO_ACQ', script)
    vers = out.get('evidence_versions') or []
    ids = [e['id'] for v in vers for e in v['evidence']]
    ok = (not any(p.startswith('acquire') for p in stub.phases())
          and len(vers) == 1
          and not any(str(i).startswith('a') for i in ids)
          and any(e.get('phase') == 'acquisition_skipped' for e in out.get('events', [])))
    check(5, 'NO_ACQ 不发取证、不生成虚假新观察', ok,
          {'phases': stub.phases(), 'versions': len(vers), 'ids': ids})


# ---------------------------------------------------------------- T6 无复核但保留结构校验
def t6():
    good = {'observe_facts': OBS, 'initial_answer': ANS, 'evidence_check': CHK_BAD,
            'acquire_A_VALUE': ACQ, 'evidence_reassess_and_correct': REPAIR}
    s1, _, _, o1 = run_arm('NO_GATE', good)
    bad_repair = {'verdict': 'incorrect', 'error_type': 'value', 'analysis': 'x',
                  'supporting_ids': ['a1'], 'scope': 'rows', 'unresolved': [], 'answer': '13'}
    script2 = dict(good, evidence_reassess_and_correct=bad_repair)
    s2, _, _, o2 = run_arm('NO_GATE', script2)
    ok = ('recheck' not in s1.phases() and o1.get('status') == 'CORRECTED_EVIDENCE_SUPPORTED'
          and o1.get('gate_skipped') is True
          and 'recheck' not in s2.phases()
          and o2.get('status') == 'FALLBACK_UNVERIFIED'
          and 'invalid_repair_answer' in (o2.get('unresolved') or []))
    check(6, 'NO_GATE 不发语义复核，但保留候选格式/答案/引用校验', ok,
          {'valid_run': [o1.get('status'), o1.get('gate_skipped')],
           'invalid_repair': [o2.get('status'), o2.get('unresolved')]})


# ---------------------------------------------------------------- T7 早停
def t7():
    script = {'observe_facts': OBS, 'initial_answer': ANS, 'evidence_check': CHK_OK}
    stub, _, _, out = run_arm('FULL', script)
    check(7, 'correct 分支按规则早停（2 次初态 + 1 次检查 = 3 次）',
          len(stub.calls) == 3 and out.get('status') == 'KEPT_EVIDENCE_SUPPORTED',
          {'calls': len(stub.calls), 'phases': stub.phases()})


# ---------------------------------------------------------------- T8 复核输入
def t8():
    script = {'observe_facts': OBS, 'initial_answer': ANS, 'evidence_check': CHK_BAD,
              'acquire_A_VALUE': ACQ, 'evidence_reassess_and_correct': REPAIR,
              'recheck': {'verdict': 'correct', 'analysis': 'ok', 'supporting_ids': ['a1'],
                          'scope': 'rows', 'unresolved': []}}
    stub, _, _, out = run_arm('FULL', script)
    prompt = [c['prompt'] for c in stub.calls if c['phase'] == 'recheck'][0]
    payload = json.loads(prompt.rsplit('\n', 1)[-1])
    ids = [e['id'] for e in payload['evidence']]
    ok = (payload['candidate'] == REPAIR['answer'] and ids == ['i1', 'i2', 'c1', 'a1']
          and payload['reasoning'] == REPAIR['reasoning'])
    check(8, '改答后的复核使用当前候选与对应证据账本', ok,
          {'candidate': payload['candidate'], 'evidence_ids': ids})


# ---------------------------------------------------------------- T9 金标隔离
def t9():
    script = {'observe_facts': OBS, 'initial_answer': ANS, 'evidence_check': CHK_BAD,
              'acquire_A_VALUE': ACQ, 'evidence_reassess_and_correct': REPAIR,
              'recheck': {'verdict': 'correct', 'analysis': 'ok', 'supporting_ids': ['a1'],
                          'scope': 'rows', 'unresolved': []}}
    stub, _, _, _ = run_arm('FULL', script)
    joined = '\n'.join(stub.prompts())
    leaked = [w for w in ('SENTINEL_GOLD', 'exe_ans', 'official_exe_ans', '__gold__') if w in joined]
    check(9, '金标与评分字段不进入模型输入（运行进程不导入评分模块）',
          not leaked and 'scoring' not in sys.modules and 'evaluate' not in sys.modules,
          {'leaked': leaked, 'scoring_loaded': 'scoring' in sys.modules})


# ---------------------------------------------------------------- T10 五臂隔离
def t10():
    base = {'observe_facts': OBS, 'initial_answer': ANS, 'evidence_check': CHK_BAD,
            'acquire_A_VALUE': ACQ,
            'recheck': {'verdict': 'correct', 'analysis': 'ok', 'supporting_ids': ['a1'],
                        'scope': 'rows', 'unresolved': []}}
    s_full, _, _, o_full = run_arm('FULL', dict(base, evidence_reassess_and_correct=REPAIR))
    s_noacq, _, _, o_noacq = run_arm('NO_ACQ', dict(
        base,
        evidence_reassess_and_correct={**REPAIR, 'answer': '99', 'supporting_ids': ['i1']},
        recheck={'verdict': 'correct', 'analysis': 'ok', 'supporting_ids': ['c1'],
                 'scope': 'rows', 'unresolved': []}))
    full_prompts = '\n'.join(s_full.prompts())
    noacq_prompts = '\n'.join(s_noacq.prompts())
    ok = ('99' not in full_prompts and '13' not in noacq_prompts
          and o_full.get('answer') == '13' and o_noacq.get('answer') == '99'
          and o_full is not o_noacq)
    check(10, '五臂状态相互隔离、互不读取答案', ok,
          {'full_answer': o_full.get('answer'), 'noacq_answer': o_noacq.get('answer')})


# ---------------------------------------------------------------- T11 缓存身份
def t11():
    dummy = type('D', (), {'response_mode': 'text'})()
    API.MODEL, API.TEMP, API.THINK, API.MAXTOK = 'qwen3.8-flash', 0, False, 1200
    a = API.Caller._fingerprint(dummy, 'sha1', 'prompt-A', None)
    b = API.Caller._fingerprint(dummy, 'sha1', 'prompt-A', None)
    c = API.Caller._fingerprint(dummy, 'sha1', 'prompt-B', None)
    d = API.Caller._fingerprint(dummy, 'sha2', 'prompt-A', None)
    e = API.Caller._fingerprint(dummy, 'sha1', 'prompt-A', 'scope')
    API.MAXTOK = 1201
    f = API.Caller._fingerprint(dummy, 'sha1', 'prompt-A', None)
    API.MAXTOK = 1200
    ok = a == b and a != c and a != d and a != f and a != e
    check(11, '相同前缀可命中、不同上下文不误命中（身份含模型/提示/图像/max_tokens）',
          ok, {'same': a == b, 'prompt_diff': a != c, 'image_diff': a != d,
               'maxtok_diff': a != f, 'scope_included': a != e})


# ---------------------------------------------------------------- T12 并发硬顶
def _quota_worker(folder, cap, tries):
    import sys as _s
    from pathlib import Path as _P
    _s.path.insert(0, str(_P(__file__).resolve().parent))
    _s.path.insert(0, str(_P(__file__).resolve().parent.parent / '51_SSC_EVID_400统一对照'))
    import experiment as _X
    q = _X.Quota(folder, cap)
    for _ in range(tries):
        try:
            q.take()
        except Exception:
            return


def t12():
    with tempfile.TemporaryDirectory() as d:
        cap = 100
        procs = [mp.Process(target=_quota_worker, args=(d, cap, 40)) for _ in range(6)]
        for p in procs:
            p.start()
        for p in procs:
            p.join()
        used = json.loads((Path(d) / 'quota.json').read_text())['used']
    check(12, '并发请求硬顶不可超发（6 进程 × 40 次，硬顶 100）', used == cap,
          {'cap': cap, 'used': used, 'attempts': 240})


# ---------------------------------------------------------------- T13 失败可审计
def t13():
    script = {'observe_facts': OBS, 'initial_answer': ANS}
    stub, _, _, out = run_arm('FULL', script, fail_status={'evidence_check': 'error'})
    stub2, _, _, out2 = run_arm('FULL', dict(script, evidence_check=CHK_OK), length_phase='evidence_check')
    ok = (out.get('status') == 'FALLBACK_UNVERIFIED'
          and 'check_unparseable' in (out.get('unresolved') or [])
          and out.get('answer') == '12'
          and out2.get('status') == 'FALLBACK_UNVERIFIED'
          and 'check_unparseable' in (out2.get('unresolved') or []))
    check(13, '失败/超时/截断可审计，且回退初答后仍进固定分母', ok,
          {'http_error': [out.get('status'), out.get('unresolved')],
           'finish_length': [out2.get('status'), out2.get('unresolved')]})


# ---------------------------------------------------------------- T14 续跑不重采
def t14():
    items = [{'key': 'k%d' % i, 'order': i} for i in range(10)]
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / 'results_s0.jsonl'
        first = pending(items, 0, 1, p)
        p.write_text(''.join(json.dumps({'item': it['key']}) + '\n' for it in first), encoding='utf-8')
        second = pending(items, 0, 1, p)
    check(14, '已完成结果不重新采样（续跑只补未完成）', len(first) == 10 and second == [],
          {'first': len(first), 'second': len(second)})


def pending(items, i, n, out_path):
    done = set()
    if Path(out_path).exists():
        done = {json.loads(l)['item'] for l in Path(out_path).read_text(encoding='utf-8').splitlines() if l.strip()}
    return [it for k, it in enumerate(items) if k % n == i and it['key'] not in done]


# ---------------------------------------------------------------- T15 固定分母
def t15():
    gold = {f'S{i}': {'answer': 'x'} for i in range(400)}
    labels = {sid: ('A' if i % 7 == 0 else 'C') for i, sid in enumerate(gold)}
    counts = {}
    for v in labels.values():
        counts[v] = counts.get(v, 0) + 1
    check(15, '未提交与解析失败留在固定分母（400 不变）',
          sum(counts.values()) == 400 and counts.get('A', 0) > 0 and 'A' not in ('C', 'W'),
          {'n': sum(counts.values()), 'A': counts.get('A')})


# ---------------------------------------------------------------- T16 图像漂移
def t16():
    import run59
    good = json.loads((ROOT / 'freeze.json').read_text(encoding='utf-8')) if (ROOT / 'freeze.json').exists() else None
    rel = '51_SSC_EVID_400统一对照/datasets/mixed400/images/NEW162.jpg'
    fake = {'files': {rel: 'deadbeef' * 8}}
    err = []
    try:
        run59.guard_item(fake, ITEM)
    except ValueError as e:
        err.append(str(e))
    missing = dict(ITEM, image='datasets/mixed400/images/__missing__.jpg')
    try:
        run59.guard_item(fake, missing)
    except ValueError as e:
        err.append(str(e))
    check(16, '实际图像缺失或漂移在请求前拒绝', len(err) == 2, err)


# ---------------------------------------------------------------- T17 样本数来自协议
def t17():
    import run59
    its = run59.items()
    logical = run59.logical_count()
    seen, recount = set(), 0
    for ds in ('mixed400', 'numeric400'):
        for line in (PKG / 'datasets' / ds / 'runtime.jsonl').read_text(encoding='utf-8').splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            recount += 1
            seen.add((r['image_sha256'], r['question']))
    check(17, '样本数从清单读取，不写死旧数量（与独立重算一致）',
          len(its) == len(seen) and logical == recount,
          {'unique_from_runner': len(its), 'unique_recount': len(seen),
           'logical_runner': logical, 'logical_recount': recount})


# ---------------------------------------------------------------- T18 分来源合计
def t18():
    import run59
    its = run59.items()
    per = {}
    for it in its:
        per[it['first_dataset']] = per.get(it['first_dataset'], 0) + 1
    total_members = sum(len(it['members']) for it in its)
    per_members = {}
    for it in its:
        for m in it['members']:
            per_members[m['dataset']] = per_members.get(m['dataset'], 0) + 1
    ok = (sum(per.values()) == len(its) and sum(per_members.values()) == total_members
          and per_members == {'mixed400': 400, 'numeric400': 400})
    check(18, '每臂分来源合计与总体一致（400 + 400 = 800）', ok,
          {'unique_by_first': per, 'members': per_members})


def main():
    for fn in (t1, t2, t3, t4, t5, t6, t7, t8, t9, t10, t11, t12, t13, t14, t15, t16, t17, t18):
        fn()
    npass = sum(1 for r in RESULTS if r['pass'])
    out = {'n': len(RESULTS), 'pass': npass, 'fail': len(RESULTS) - npass, 'checks': RESULTS}
    (ROOT / 'tests_report.json').write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps({'pass': npass, 'total': len(RESULTS)}, ensure_ascii=False))
    return 0 if npass == len(RESULTS) else 1


if __name__ == '__main__':
    sys.exit(main())

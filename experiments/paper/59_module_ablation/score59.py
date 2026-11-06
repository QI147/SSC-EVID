# -*- coding: utf-8 -*-
"""实验四评分与统计：五臂 × 两题集，4 个消融 × 2 题集 = 8 项预注册比较。

主评分沿用原 400 题冻结口径（evaluate.label：display_match OR exec_match），
单位归并另列敏感性。区间按来源分层、表级聚类 bootstrap（10,000 次，种子固定）。
本文件是唯一读金标的位置；运行器从不导入本文件。
"""
import collections
import csv
import json
import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PKG = ROOT.parent / '51_SSC_EVID_400统一对照'
RUNNER = Path(r'<local-runner>')
for p in (str(PKG), str(ROOT), str(RUNNER)):
    if p not in sys.path:
        sys.path.insert(0, p)

import experiment as X  # noqa: E402
import evaluate as EV  # noqa: E402
import arms  # noqa: E402
from scoring_audit import flip_tier  # noqa: E402

DATASETS = ('mixed400', 'numeric400')
ABL = ('NO_FACT_FIRST', 'GENERIC_ACQ', 'NO_ACQ', 'NO_GATE')
NAMES = {'FULL': '完整 EVID36', 'NO_FACT_FIRST': '去事实先行初态',
         'GENERIC_ACQ': '定向动作→通用观察', 'NO_ACQ': '去新增取证', 'NO_GATE': '去最终复核'}
SEED = 20261103
BOOT = 10000
RES_PATH = None
OUT_DIR = None


def rows(p):
    txt = Path(p).read_text(encoding='utf-8')
    if txt.lstrip().startswith('['):
        return json.loads(txt)
    return [json.loads(s) for s in txt.splitlines() if s.strip()]


def mcnemar(gain, loss):
    n = gain + loss
    if not n:
        return 1.0
    return min(1.0, 2 * sum(math.comb(n, k) for k in range(min(gain, loss) + 1)) / (2 ** n))


def holm(pairs):
    """pairs = [(index, raw_p)] → 校正后的 p（单调、封顶 1）。"""
    m = len(pairs)
    ordered = sorted(pairs, key=lambda x: x[1])
    out, running = {}, 0.0
    for rank, (idx, p) in enumerate(ordered):
        running = max(running, min(1.0, p * (m - rank)))
        out[idx] = running
    return out


def cluster_bootstrap(a, b, groups):
    """表级聚类 bootstrap：同一 source_group 的题一起抽。返回百分点区间。"""
    idx = collections.defaultdict(list)
    for s in a:
        idx[groups[s]].append(s)
    keys = list(idx)
    if not keys:
        return [0.0, 0.0]
    rng = random.Random(SEED)
    deltas = []
    for _ in range(BOOT):
        num = den = 0
        for _ in keys:
            for s in idx[rng.choice(keys)]:
                num += (1 if b[s] == 'C' else 0) - (1 if a[s] == 'C' else 0)
                den += 1
        if den:
            deltas.append(100.0 * num / den)
    deltas.sort()
    return [round(deltas[int(0.025 * BOOT)], 3), round(deltas[int(0.975 * BOOT) - 1], 3)]


def mde80(n, discordant):
    if not n or not discordant:
        return None
    return round(100 * 2.802 * math.sqrt(discordant / n) / math.sqrt(n), 2)


def compare(a, b, groups):
    ids = list(a)
    if not ids:
        return {'n': 0, 'from_correct': 0, 'to_correct': 0, 'gained': 0, 'lost': 0, 'net': 0,
                'mcnemar_raw': 1.0, 'cluster_bootstrap95_pp': [0.0, 0.0], 'discordant': 0,
                'mde80_pp': None}
    gain = sum(1 for s in ids if a[s] != 'C' and b[s] == 'C')
    loss = sum(1 for s in ids if a[s] == 'C' and b[s] != 'C')
    return {'n': len(ids),
            'from_correct': sum(1 for s in ids if a[s] == 'C'),
            'to_correct': sum(1 for s in ids if b[s] == 'C'),
            'gained': gain, 'lost': loss, 'net': gain - loss,
            'mcnemar_raw': round(mcnemar(gain, loss), 10),
            'cluster_bootstrap95_pp': cluster_bootstrap(a, b, groups),
            'discordant': gain + loss,
            'mde80_pp': mde80(len(ids), gain + loss)}


def lab(sid, answer, gold, contract):
    if answer is None or str(answer).strip() == '':
        return 'A'
    base = EV.label(sid, answer, gold)
    if contract == 'strict' or base == 'C':
        return base
    return 'C' if flip_tier(answer, gold[sid].get('answer')) else 'W'


def transitions(initial, final):
    m = collections.Counter(initial[s] + '->' + final[s] for s in initial)
    return {'matrix': dict(m),
            'initial': dict(collections.Counter(initial.values())),
            'final': dict(collections.Counter(final.values())),
            'W_to_C': m.get('W->C', 0), 'C_to_W': m.get('C->W', 0),
            'W_to_W': m.get('W->W', 0), 'C_to_C': m.get('C->C', 0),
            'A_to_C': m.get('A->C', 0), 'C_to_A': m.get('C->A', 0),
            'W_to_A': m.get('W->A', 0), 'A_to_W': m.get('A->W', 0), 'A_to_A': m.get('A->A', 0)}


def load():
    f = json.loads((ROOT / 'freeze.json').read_text(encoding='utf-8'))
    res = rows(RES_PATH or (ROOT / 'runs' / 'main' / 'results.json'))
    gold, rt, mapping = {}, {}, {}
    for ds in DATASETS:
        gold[ds] = {r['sample_id']: r for r in rows(PKG / 'datasets' / ds / 'eval.jsonl')}
        rt[ds] = {r['sample_id']: r for r in rows(PKG / 'datasets' / ds / 'runtime.jsonl')}
    for rec in res:
        for m in rec['members']:
            mapping[(m['dataset'], m['sample_id'])] = rec
    return f, res, gold, rt, mapping


def label_all(res, gold, mapping, contract):
    L, I = {}, {}
    for ds in DATASETS:
        L[ds], I[ds] = {}, {}
        for arm in arms.ARMS:
            L[ds][arm], I[ds][arm] = {}, {}
        for sid in gold[ds]:
            rec = mapping.get((ds, sid), {'arms': {}})
            for arm in arms.ARMS:
                a = rec['arms'].get(arm, {}) or {}
                L[ds][arm][sid] = lab(sid, a.get('answer'), gold[ds], contract)
                I[ds][arm][sid] = lab(sid, a.get('initial_answer'), gold[ds], contract)
    return L, I


def costs():
    calls = []
    for p in sorted((ROOT / 'runs' / 'main').glob('requests_s*.jsonl')):
        calls += rows(p)
    out = {}
    for arm in arms.ARMS:
        sub = [c for c in calls if c['arm'] == arm]
        real = [c for c in sub if not c['reused']]
        out[arm] = {'logical': len(sub), 'real': len(real), 'cached': len(sub) - len(real),
                    'cost_cny': round(sum(float(c.get('cost_cny') or 0) for c in real), 4),
                    'prompt_tokens': sum(int(c.get('prompt_tokens') or 0) for c in real),
                    'completion_tokens': sum(int(c.get('completion_tokens') or 0) for c in real),
                    'http_status': dict(collections.Counter(c.get('status') for c in real)),
                    'finish': dict(collections.Counter(c.get('finish_reason') for c in real)),
                    'by_phase': dict(collections.Counter(c['phase'] for c in real))}
    out['_total'] = {'logical': len(calls),
                     'real': sum(1 for c in calls if not c['reused']),
                     'cost_cny': round(sum(float(c.get('cost_cny') or 0) for c in calls
                                           if not c['reused']), 4)}
    return out


def main():
    f, res, gold, rt, mapping = load()
    out = {'freeze': f['sha256'], 'n_unique_items': f['items']['unique'],
           'n_logical': f['items']['logical'], 'contracts': {}, 'cost': costs(),
           'cap': f['cap'], 'model': f['model'], 'endpoint': f['endpoint'],
           'total_items': len(res)}
    labels_by_contract = {}
    for contract in ('strict', 'unit_merged'):
        L, I = label_all(res, gold, mapping, contract)
        labels_by_contract[contract] = (L, I)
        d = {'datasets': {}, 'comparisons': {}}
        pairs = []
        for ds in DATASETS:
            counts = {arm: dict(collections.Counter(L[ds][arm].values())) for arm in arms.ARMS}
            groups = {sid: rt[ds][sid]['source_group'] for sid in gold[ds]}
            states = {}
            for arm in arms.ARMS:
                status, verdict, gap, reasons = (collections.Counter() for _ in range(4))
                for sid in gold[ds]:
                    a = (mapping.get((ds, sid), {'arms': {}})['arms'].get(arm) or {})
                    status[a.get('status')] += 1
                    verdict[a.get('check_verdict')] += 1
                    gap[a.get('gap_type')] += 1
                    for r in (a.get('unresolved') or []):
                        reasons[r] += 1
                states[arm] = {'status': dict(status), 'check_verdict': dict(verdict),
                               'gap_type': dict(gap), 'fallback_reasons': dict(reasons)}
            d['datasets'][ds] = {'counts': counts,
                                 'accuracy': {a: round(counts[a].get('C', 0) / 400, 4) for a in arms.ARMS},
                                 'transitions': {a: transitions(I[ds][a], L[ds][a]) for a in arms.ARMS},
                                 'states': states, 'n': 400}
            for ab in ABL:
                c = compare(L[ds]['FULL'], L[ds][ab], groups)
                c.update({'dataset': ds, 'ablation': ab, 'vs': 'FULL'})
                d['comparisons'][f'{ds}:{ab}_minus_FULL'] = c
                pairs.append(c)
        adj = holm([(i, c['mcnemar_raw']) for i, c in enumerate(pairs)])
        for i, c in enumerate(pairs):
            c['holm_p'] = round(adj[i], 10)
        d['holm_family_size'] = len(pairs)
        d['holm_scope'] = '8 项预注册比较（4 消融 × 2 题集）'
        # S1 预注册子集：FULL 的检查未判 correct（模型判定，不含金标）
        s1 = {}
        for ds in DATASETS:
            reach = [sid for sid in gold[ds]
                     if ((mapping.get((ds, sid), {'arms': {}})['arms'].get('FULL') or {})
                         .get('check_verdict') not in (None, 'correct'))]
            groups = {sid: rt[ds][sid]['source_group'] for sid in reach}
            s1[ds] = {'n': len(reach), 'comparisons': {}}
            for ab in ABL:
                a = {sid: L[ds]['FULL'][sid] for sid in reach}
                b = {sid: L[ds][ab][sid] for sid in reach}
                c = compare(a, b, groups)
                c.update({'dataset': ds, 'ablation': ab, 'note': 'S1 修复可达子集，探索性'})
                s1[ds]['comparisons'][ab] = c
        d['s1_repair_reachable'] = s1
        out['contracts'][contract] = d

    # 合池：按唯一 item 去重，取该 item 首个成员作代表
    pooled = {}
    for contract in ('strict', 'unit_merged'):
        L, _ = labels_by_contract[contract]
        rep = {}
        for rec in res:
            m = rec['members'][0]
            rep[rec['item']] = (m['dataset'], m['sample_id'])
        groups = {k: 'g' for k in rep}
        pooled[contract] = {'n': len(rep), 'steps': []}
        for ab in ABL:
            a = {k: L[ds][('FULL')][sid] for k, (ds, sid) in rep.items()}
            b = {k: L[ds][ab][sid] for k, (ds, sid) in rep.items()}
            c = compare(a, b, groups)
            c.update({'ablation': ab, 'vs': 'FULL'})
            pooled[contract]['steps'].append(c)
        adj = holm([(i, c['mcnemar_raw']) for i, c in enumerate(pooled[contract]['steps'])])
        for i, c in enumerate(pooled[contract]['steps']):
            c['holm_p'] = round(adj[i], 10)
    out['pooled'] = pooled

    rep_dir = Path(OUT_DIR) if OUT_DIR else (ROOT / 'reports')
    rep_dir.mkdir(parents=True, exist_ok=True)
    X.dump(rep_dir / 'results.json', out)
    write_csvs(out, res, mapping, gold, labels_by_contract, rep_dir)
    render(out, rep_dir)
    print(json.dumps({'strict_FULL': {ds: out['contracts']['strict']['datasets'][ds]['counts']['FULL']
                                      for ds in DATASETS},
                      'total': out['cost']['_total']}, ensure_ascii=False))


def write_csvs(out, res, mapping, gold, labels_by_contract, rep):

    L, I = labels_by_contract['strict']
    with (rep / 'comparisons.csv').open('w', newline='', encoding='utf-8-sig') as fh:
        w = csv.writer(fh)
        w.writerow(['contract', 'scope', 'dataset', 'ablation', 'from_correct', 'to_correct',
                    'gained', 'lost', 'net', 'mcnemar_raw', 'holm_p', 'ci_lo_pp', 'ci_hi_pp'])
        for contract in ('strict', 'unit_merged'):
            for c in out['contracts'][contract]['comparisons'].values():
                w.writerow([contract, 'dataset', c['dataset'], c['ablation'], c['from_correct'],
                            c['to_correct'], c['gained'], c['lost'], c['net'], c['mcnemar_raw'],
                            c['holm_p'], c['cluster_bootstrap95_pp'][0], c['cluster_bootstrap95_pp'][1]])
            for c in out['pooled'][contract]['steps']:
                w.writerow([contract, 'pooled_dedup', 'pooled', c['ablation'], c['from_correct'],
                            c['to_correct'], c['gained'], c['lost'], c['net'], c['mcnemar_raw'],
                            c['holm_p'], c['cluster_bootstrap95_pp'][0], c['cluster_bootstrap95_pp'][1]])
    with (rep / 'per_sample.csv').open('w', newline='', encoding='utf-8-sig') as fh:
        w = csv.writer(fh)
        w.writerow(['dataset', 'sample_id', 'source', 'item', 'arm', 'initial_answer',
                    'initial_label', 'answer', 'label', 'status', 'check_verdict', 'gap_type',
                    'evidence_versions', 'gate_skipped', 'unresolved'])
        for ds in DATASETS:
            for sid in gold[ds]:
                rec = mapping.get((ds, sid), {'arms': {}, 'item': ''})
                src = (rec['members'][0]['source'] if rec.get('members') else '')
                for arm in arms.ARMS:
                    a = rec['arms'].get(arm, {}) or {}
                    w.writerow([ds, sid, src, rec.get('item'), arm, a.get('initial_answer'),
                                I[ds][arm][sid], a.get('answer'), L[ds][arm][sid],
                                a.get('status'), a.get('check_verdict'), a.get('gap_type'),
                                a.get('evidence_versions'), a.get('gate_skipped'),
                                json.dumps(a.get('unresolved'), ensure_ascii=False)])
    with (rep / 'item_map.csv').open('w', newline='', encoding='utf-8-sig') as fh:
        w = csv.writer(fh)
        w.writerow(['item_key', 'image_sha256', 'first_dataset', 'member_dataset',
                    'member_sample_id', 'shared'])
        for rec in res:
            for m in rec['members']:
                w.writerow([rec['item'], rec['image_sha256'], rec['members'][0]['dataset'],
                            m['dataset'], m['sample_id'], len(rec['members']) > 1])


def fmt_p(p):
    return '<1e-4' if p < 1e-4 else f'{p:.4f}'.rstrip('0').rstrip('.')


def render(out, rep_dir):
    L = []
    A = L.append
    A('# 实验四：完整流程的端到端模块消融')
    A('')
    A(f"**冻结** `{out['freeze']}` ｜ 唯一题 {out['n_unique_items']}（逻辑样本 {out['n_logical']}，"
      f"实跑 {out['total_items']}） ｜ 模型 {out['model']['name']} "
      f"(T={out['model']['temperature']}, thinking={out['model']['thinking']}, "
      f"max_tokens={out['model']['max_tokens']}, retry={out['model']['retry']})")
    A(f"**端点** {out['endpoint']['scheme']}://{out['endpoint']['host']}{out['endpoint']['path']} ｜ "
      f"**硬顶** {out['cap']:,} 次 ｜ **真实调用** {out['cost']['_total']['real']:,} 次 ｜ "
      f"**费用** ¥{out['cost']['_total']['cost_cny']:.2f}")
    A('')
    A('## 一、五臂最终成绩（主评分：原 400 题冻结口径）')
    A('')
    A('| 版本 | 混合400 | 准确率 | 相对 FULL | Holm p | 数字400 | 准确率 | 相对 FULL | Holm p |')
    A('|---|---:|---:|---:|---:|---:|---:|---:|---:|')
    st = out['contracts']['strict']
    cm = st['comparisons']
    for arm in arms.ARMS:
        m = st['datasets']['mixed400']['counts'][arm]
        n = st['datasets']['numeric400']['counts'][arm]
        if arm == 'FULL':
            A(f"| {arm} {NAMES[arm]} | {m.get('C',0)} | {m.get('C',0)/4:.1f}% | — | — | "
              f"{n.get('C',0)} | {n.get('C',0)/4:.1f}% | — | — |")
        else:
            a = cm[f'mixed400:{arm}_minus_FULL']
            b = cm[f'numeric400:{arm}_minus_FULL']
            A(f"| {arm} {NAMES[arm]} | {m.get('C',0)} | {m.get('C',0)/4:.1f}% | {a['net']:+d} | "
              f"{fmt_p(a['holm_p'])} | {n.get('C',0)} | {n.get('C',0)/4:.1f}% | {b['net']:+d} | "
              f"{fmt_p(b['holm_p'])} |")
    A('')
    A('未提交（A）：' + '；'.join(
        f"{ds} " + '/'.join(f"{arm}={st['datasets'][ds]['counts'][arm].get('A',0)}" for arm in arms.ARMS)
        for ds in DATASETS))
    A('')
    A('## 二、8 项预注册比较（Holm 校正，family = 8）')
    A('')
    A('| 题集 | 消融 | 修对 | 改坏 | 净差 | 原始 p | Holm p | 表级聚类 95% 区间 (pp) | 80% 功效可辨差值 |')
    A('|---|---|---:|---:|---:|---:|---:|---|---:|')
    for ds in DATASETS:
        for ab in ABL:
            c = cm[f'{ds}:{ab}_minus_FULL']
            A(f"| {ds} | {ab} | {c['gained']} | {c['lost']} | {c['net']:+d} | "
              f"{fmt_p(c['mcnemar_raw'])} | {fmt_p(c['holm_p'])} | "
              f"{c['cluster_bootstrap95_pp'][0]:+.2f}～{c['cluster_bootstrap95_pp'][1]:+.2f} | "
              f"{c['mde80_pp'] if c['mde80_pp'] is not None else '—'} pp |")
    A('')
    A('### S1 修复可达子集（预注册，模型判定为门，探索性）')
    A('')
    A('| 题集 | n | 消融 | 修对 | 改坏 | 净差 | 原始 p | 区间 (pp) |')
    A('|---|---:|---|---:|---:|---:|---:|---|')
    for ds in DATASETS:
        s1 = st['s1_repair_reachable'][ds]
        for ab in ABL:
            c = s1['comparisons'][ab]
            A(f"| {ds} | {s1['n']} | {ab} | {c['gained']} | {c['lost']} | {c['net']:+d} | "
              f"{fmt_p(c['mcnemar_raw'])} | {c['cluster_bootstrap95_pp'][0]:+.2f}～{c['cluster_bootstrap95_pp'][1]:+.2f} |")
    A('')
    A('## 三、敏感性：单位归并口径')
    A('')
    A('| 消融 | 混合 净差 | 混合 Holm p | 数字 净差 | 数字 Holm p |')
    A('|---|---:|---:|---:|---:|')
    mu = out['contracts']['unit_merged']['comparisons']
    for ab in ABL:
        a = mu[f'mixed400:{ab}_minus_FULL']
        b = mu[f'numeric400:{ab}_minus_FULL']
        A(f"| {ab} | {a['net']:+d} | {fmt_p(a['holm_p'])} | {b['net']:+d} | {fmt_p(b['holm_p'])} |")
    A('')
    A('## 四、初态 → 终态转移（主评分）')
    A('')
    A('| 题集 | 臂 | 初态 C/W/A | 终态 C/W/A | 错→对 | 对→错 | 错→错 | 对→对 | 未提交转移 |')
    A('|---|---|---|---|---:|---:|---:|---:|---|')
    for ds in DATASETS:
        for arm in arms.ARMS:
            t = st['datasets'][ds]['transitions'][arm]
            i0, f0 = t['initial'], t['final']
            A(f"| {ds} | {arm} | {i0.get('C',0)}/{i0.get('W',0)}/{i0.get('A',0)} | "
              f"{f0.get('C',0)}/{f0.get('W',0)}/{f0.get('A',0)} | {t['W_to_C']} | {t['C_to_W']} | "
              f"{t['W_to_W']} | {t['C_to_C']} | C→A {t['C_to_A']} / W→A {t['W_to_A']} / A→C {t['A_to_C']} |")
    A('')
    A('## 五、调用、缓存与费用')
    A('')
    A('| 臂 | 逻辑请求 | 真实请求 | 命中缓存 | 输入 token | 输出 token | 费用(¥) | HTTP |')
    A('|---|---:|---:|---:|---:|---:|---:|---|')
    for arm in arms.ARMS:
        c = out['cost'][arm]
        A(f"| {arm} | {c['logical']} | {c['real']} | {c['cached']} | {c['prompt_tokens']:,} | "
          f"{c['completion_tokens']:,} | {c['cost_cny']:.2f} | {c['http_status']} |")
    A(f"| **合计** | {out['cost']['_total']['logical']} | {out['cost']['_total']['real']} | — | — | — | "
      f"{out['cost']['_total']['cost_cny']:.2f} | — |")
    A('')
    A('## 六、检查触发与回退原因')
    A('')
    for ds in DATASETS:
        A(f"**{ds}**")
        A('')
        A('| 臂 | 检查判定 | 缺口类型 | 终态状态 |')
        A('|---|---|---|---|')
        for arm in arms.ARMS:
            s = st['datasets'][ds]['states'][arm]
            A(f"| {arm} | {s['check_verdict']} | {s['gap_type']} | {s['status']} |")
        A('')
    A('## 七、合池（按题去重，探索性）')
    A('')
    A('| 口径 | n | 消融 | 修对 | 改坏 | 净差 | 原始 p |')
    A('|---|---:|---|---:|---:|---:|---:|')
    for contract in ('strict', 'unit_merged'):
        p = out['pooled'][contract]
        for c in p['steps']:
            A(f"| {contract} | {p['n']} | {c['ablation']} | {c['gained']} | {c['lost']} | "
              f"{c['net']:+d} | {fmt_p(c['mcnemar_raw'])} |")
    A('')
    (rep_dir / '报告.md').write_text('\n'.join(L) + '\n', encoding='utf-8')


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--results', default=None)
    ap.add_argument('--out', default=None)
    _a = ap.parse_args()
    RES_PATH, OUT_DIR = _a.results, _a.out
    main()

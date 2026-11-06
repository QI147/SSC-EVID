# -*- coding: utf-8 -*-
"""五臂实现：完整 EVID36 与其四个单模块删改版本。

设计纪律
--------
* FULL **原样调用** `methods.evid36.evid_integrated.run` 与 `fact_first.initial`，
  不复制、不重写，保证与冻结 36 版逐字节同源。
* 其余四臂走 `run_chain`：它是 `E36.run` 的逐行副本，只暴露五个开关
  （check 提示、acquire 方式、repair 提示、recheck 开关、空账本宽松引用）。
  零调用测试会把 `run_chain(FULL 配置)` 与 `E36.run` 在同一批模拟响应上逐字段比对。
* 每一处与 FULL 不同的提示，都在 `PROMPT_DIFFS` 里登记第几行、为什么改。

本文件不导入 scoring / evaluate：金标只在评分进程里读。
"""
import copy
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PKG = _HERE.parent / '51_SSC_EVID_400统一对照'
if str(_PKG) not in sys.path:
    sys.path.insert(0, str(_PKG))

from methods.evid36 import evid_integrated as E36
from methods.evid36 import fact_first as F36
import experiment as X

ARMS = ('FULL', 'NO_FACT_FIRST', 'GENERIC_ACQ', 'NO_ACQ', 'NO_GATE')
ABLATIONS = ('NO_FACT_FIRST', 'GENERIC_ACQ', 'NO_ACQ', 'NO_GATE')

# ---------------------------------------------------------------- 提示词差异登记
# 每条：(臂, 阶段, 相对 FULL 的改动, 是否必要兼容)
PROMPT_DIFFS = [
    {'arm': 'NO_FACT_FIRST', 'stage': 'initial',
     'change': '事实先行两次调用（OBSERVE + ANSWER 基于事实）整体替换为 GENERIC2 的两次读：'
               'X.COMMON 一次，再接 X.REREAD_JSON（含 previous_answer 与 previous_reasoning）。'
               '提示原文与 53/58 的 GENERIC2 逐字相同。',
     'necessary': False},
    {'arm': 'NO_FACT_FIRST', 'stage': 'evidence_check',
     'change': '在 E36.CHECK 末尾追加两句：本样本没有初始证据账本，判定前应记录新的图像观察 c1..；'
               '若判 correct 且未记录观察，supporting_ids 可为 []。',
     'necessary': True},
    {'arm': 'GENERIC_ACQ', 'stage': 'acquire',
     'change': '把 E36.ACQUIRE + "ACTION: " + DIRECTIVES[action] 替换为 GENERIC_ACQUIRE，'
               '不再带 ACTION 行与动作专用指令；数据段保留 question / initial_answer / diagnosis / question_plan，'
               '**因此仍含检查给出的 gap.target 与 analysis**。',
     'necessary': False},
    {'arm': 'NO_ACQ', 'stage': 'evidence_reassess_and_correct',
     'change': '在 E36.REPAIR 末尾追加一句：本次未执行新取证，账本只有初始观察。',
     'necessary': True},
    {'arm': 'NO_ACQ', 'stage': 'recheck',
     'change': '在 E36.RECHECK 末尾追加同一句披露。',
     'necessary': True},
    {'arm': 'NO_GATE', 'stage': 'recheck',
     'change': '不发送该调用；候选通过与 FULL 相同的结构性校验后直接采纳。',
     'necessary': False},
]

CHECK_NO_INITIAL = E36.CHECK + """
There is NO initial evidence ledger for this item: the initial answer came from an
ordinary two-pass reading with no recorded observations, so no i IDs exist.
Record the explicit image observations you rely on as c1,c2,... ; if you judge the
answer correct without recording any observation, set supporting_ids to [] and state
the basis in analysis. An empty supporting_ids list is not by itself an error and
must not be treated as one."""

GENERIC_ACQUIRE = '''Re-observe the table image for this question and record what it shows.
The initial answer and the check diagnosis above are context, not evidence: verify them
against the image yourself. Return ONE JSON object:
{"evidence":[{"id":"a1","location":"row/column/header","content":"observation or explicit calculation","source":"image|calculation"}],
"scope":"range inspected and limits","unresolved":[]}.
Use new IDs a1,a2,...; do not repeat c IDs. Do not output an answer or a verdict.
Do not invent missing source information. Use compact relevant records, not an entire table transcription.'''

REPAIR_NO_ACQ = E36.REPAIR + """
No new acquisition was performed in this run: the ledger contains only the initial
observations, and no a IDs exist. Work with what is there and disclose what is missing."""

RECHECK_NO_ACQ = E36.RECHECK + """
No new acquisition was performed in this run: the ledger contains only the initial
observations, and no a IDs exist."""


# ---------------------------------------------------------------- 配置
CONFIG = {
    'FULL': {'check': E36.CHECK, 'acquire': 'directed', 'repair': E36.REPAIR,
             'recheck': E36.RECHECK, 'allow_empty_refs': False},
    'NO_FACT_FIRST': {'check': CHECK_NO_INITIAL, 'acquire': 'directed', 'repair': E36.REPAIR,
                      'recheck': E36.RECHECK, 'allow_empty_refs': True},
    'GENERIC_ACQ': {'check': E36.CHECK, 'acquire': 'generic', 'repair': E36.REPAIR,
                    'recheck': E36.RECHECK, 'allow_empty_refs': False},
    'NO_ACQ': {'check': E36.CHECK, 'acquire': 'skip', 'repair': REPAIR_NO_ACQ,
               'recheck': RECHECK_NO_ACQ, 'allow_empty_refs': False},
    'NO_GATE': {'check': E36.CHECK, 'acquire': 'directed', 'repair': E36.REPAIR,
                'recheck': None, 'allow_empty_refs': False},
}

# 每题每臂的调用上限（结构上限，硬失败）
MAX_CALLS = {'FULL': 6, 'NO_FACT_FIRST': 6, 'GENERIC_ACQ': 6, 'NO_ACQ': 5, 'NO_GATE': 6}


def judgment_issue(obj, pool, allow_empty_refs=False):
    """E36.judgment_issue 的副本，只把"引用非空"拆成可配置。

    allow_empty_refs=True 时，supporting_ids=[] 不再算契约失败（仅在空账本时启用）；
    引用未知 ID、缺 analysis/scope/unresolved、correct 带 unresolved、correct 引 unverified
    这四类判定与 E36 完全一致。
    """
    if not isinstance(obj, dict) or obj.get('verdict') not in ('correct', 'incorrect', 'unknown'):
        return 'invalid_verdict'
    if not isinstance(obj.get('analysis'), str) or not obj['analysis'].strip():
        return 'missing_analysis'
    refs = obj.get('supporting_ids')
    if not isinstance(refs, list):
        return 'invalid_support_reference'
    if any(not isinstance(r, str) or r not in pool for r in refs):
        return 'invalid_support_reference'
    if not refs and not allow_empty_refs:
        return 'invalid_support_reference'
    if not isinstance(obj.get('unresolved'), list) or not str(obj.get('scope') or '').strip():
        return 'missing_scope_or_uncertainty'
    if obj['verdict'] == 'correct' and obj['unresolved']:
        return 'correct_with_unresolved'
    if obj['verdict'] == 'correct' and any(pool[r]['source'] == 'unverified' for r in refs):
        return 'unverified_source_support'
    return None


def _trim(obj):
    """轨迹里只留可审计的判定字段；原文在账本里按 call_id 可查。"""
    if not isinstance(obj, dict):
        return None
    return {'verdict': obj.get('verdict'), 'error_type': obj.get('error_type'),
            'gap': copy.deepcopy(obj.get('gap')), 'action': obj.get('action'),
            'evidence_n': len(obj.get('evidence') or []) if isinstance(obj.get('evidence'), list) else None,
            'supporting_ids': copy.deepcopy(obj.get('supporting_ids')),
            'scope': obj.get('scope'), 'unresolved': copy.deepcopy(obj.get('unresolved'))}


def run_chain(item, initial, invoke, arm):
    """E36.run 的逐行副本 + 五个开关。FULL 不走这里（直接调 E36.run）。"""
    cfg = CONFIG[arm]
    old = initial.get('answer')
    pool = {}
    out = {'answer': old, 'status': 'FALLBACK_UNVERIFIED', 'calls': [],
           'evidence_versions': [], 'events': [], 'unresolved': [], 'strict': False,
           'candidates': [{'source': 'initial', 'answer': old}]}
    if arm != 'FULL':
        out['arm'] = arm
    if not E36.valid_answer(old):
        out.update(status='INITIAL_MISSING', unresolved=['initial_missing'])
        return out
    initial_evidence = initial.get('evidence', [])
    if initial_evidence:
        issue = E36.add_evidence(pool, initial_evidence)
        if issue:
            out['unresolved'].append('initial_' + issue)
            return out
    out['initial_evidence'] = copy.deepcopy(list(pool.values()))

    def call(phase, prompt, data):
        r = invoke(arm, phase, '', prompt + '\n' + json.dumps(data, ensure_ascii=False), item['image'])
        out['calls'].append(r)
        return E36.parse(r)

    def fail(reason):
        out['unresolved'].append(reason)
        return out

    ctx = {'question': item['question'], 'initial_answer': old,
           'initial_reasoning': initial.get('reasoning', ''),
           'initial_evidence': copy.deepcopy(list(pool.values())),
           'initial_limits': {'scope': initial.get('scope'),
                              'unresolved': initial.get('unresolved', [])},
           'question_plan': initial.get('question_plan'),
           'computed_aux': initial.get('computed_aux', [])}

    check = call('evidence_check', cfg['check'], ctx)
    out['check'] = check
    if not check:
        return fail('check_unparseable')
    allow_empty = bool(cfg['allow_empty_refs']) and not pool
    if check.get('evidence') == [] and (pool or allow_empty):
        issue = None
    else:
        issue = E36.add_evidence(pool, check.get('evidence'))
    if issue and not (check.get('verdict') == 'unknown' and check.get('evidence') == []):
        return fail(issue)
    if check.get('verdict') == 'unknown' and not pool:
        issue = None if check.get('analysis') and isinstance(check.get('unresolved'), list) else 'invalid_unknown'
    else:
        issue = judgment_issue(check, pool, allow_empty)
    if issue:
        return fail(issue)
    out['evidence_versions'].append({'version': 0, 'evidence': copy.deepcopy(list(pool.values()))})
    if check['verdict'] == 'correct':
        if allow_empty and not check.get('supporting_ids'):
            out['events'].append({'phase': 'check_without_cited_observation',
                                  'basis': 'empty initial ledger; relaxed reference rule'})
        out.update(status='KEPT_EVIDENCE_SUPPORTED')
        return out

    gap = check.get('gap')
    if not isinstance(gap, dict) or not isinstance(gap.get('type'), str) or gap['type'] not in E36.ACTIONS:
        return fail('unknown_gap_type')
    if not all(isinstance(gap.get(k), str) and gap[k].strip() for k in ('target', 'why')):
        return fail('unspecified_gap')
    action = E36.ACTIONS[gap['type']]
    if check.get('action') != action:
        out['events'].append({'phase': 'action_normalized', 'requested_action': check.get('action'),
                              'selected_action': action, 'basis': 'declared_gap_type'})
    out['events'].append({'phase': 'action_selected', 'gap': copy.deepcopy(gap),
                          'action': action, 'basis_version': 0})

    if cfg['acquire'] == 'skip':
        out['events'].append({'phase': 'acquisition_skipped', 'action': action,
                              'reason': 'NO_ACQ: no new observation requested'})
        if arm != 'FULL':
            out['acquisition'] = None
        acq_limits = {'status': 'SKIPPED_BY_DESIGN', 'new_evidence_ids': [],
                      'note': 'no new acquisition; ledger unchanged'}
    else:
        if cfg['acquire'] == 'directed':
            prompt = E36.ACQUIRE + '\nACTION: ' + E36.DIRECTIVES[action]
            data = {'question': item['question'], 'action': action, 'target': gap['target'],
                    'question_plan': initial.get('question_plan')}
            phase = 'acquire_' + action
        else:
            prompt = GENERIC_ACQUIRE
            data = {'question': item['question'], 'initial_answer': old,
                    'diagnosis': check, 'question_plan': initial.get('question_plan')}
            phase = 'acquire_GENERIC'
        acquired = call(phase, prompt, data)
        if arm != 'FULL':
            out['acquisition'] = acquired
        if not acquired:
            return fail('acquisition_unparseable')
        issue = E36.add_evidence(pool, acquired.get('evidence'))
        if issue:
            return fail(issue)
        if not isinstance(acquired.get('unresolved'), list) or not str(acquired.get('scope') or '').strip():
            return fail('acquisition_scope_missing')
        out['events'].append({'phase': 'checks_invalidated', 'checks': ['answer_support', 'scope_support'],
                              'reason': 'new_evidence', 'version': 1})
        out['evidence_versions'].append({'version': 1, 'evidence': copy.deepcopy(list(pool.values())),
                                         'scope': acquired['scope'], 'unresolved': acquired['unresolved']})
        acq_limits = {'scope': acquired['scope'], 'unresolved': acquired['unresolved']}

    ctx.update(evidence=list(pool.values()), diagnosis=check, acquisition_limits=acq_limits)
    repair = call('evidence_reassess_and_correct', cfg['repair'], ctx)
    out['repair'] = repair
    issue = judgment_issue(repair, pool, allow_empty)
    if issue:
        return fail(issue)
    if not E36.valid_answer(repair.get('answer')) or not str(repair.get('reasoning') or '').strip():
        return fail('invalid_repair_answer')
    changed = str(repair['answer']) != str(old)
    if changed and repair['verdict'] != 'incorrect':
        return fail('changed_without_incorrect')
    if repair['verdict'] == 'unknown':
        return fail('repair_unknown')
    if repair['unresolved']:
        return fail('repair_unresolved')
    out['candidates'].append({'source': 'evidence_correction', 'answer': repair['answer'],
                              'evidence_version': out['evidence_versions'][-1]['version']})

    if not cfg['recheck']:
        out['gate_skipped'] = True
        out['events'].append({'phase': 'final_gate_skipped',
                              'note': 'structural checks passed (verdict/answer/reasoning/refs); no semantic recheck call'})
        out.update(answer=repair['answer'],
                   status='CORRECTED_EVIDENCE_SUPPORTED' if changed else 'KEPT_AFTER_ACQUISITION')
        return out

    final = call('recheck', cfg['recheck'],
                 {'question': item['question'], 'candidate': repair['answer'],
                  'reasoning': repair['reasoning'], 'evidence': list(pool.values()),
                  'acquisition_limits': acq_limits})
    out['final_check'] = final
    issue = judgment_issue(final, pool)
    if issue:
        return fail(issue)
    if final['verdict'] != 'correct':
        return fail('final_not_supported')
    out.update(answer=repair['answer'],
               status='CORRECTED_EVIDENCE_SUPPORTED' if changed else 'KEPT_AFTER_ACQUISITION')
    return out


# ---------------------------------------------------------------- 初态
def initial_full(item, invoke):
    """逐字节复用冻结 36 版：F36.initial。"""
    return F36.initial(item, invoke)


def initial_generic2(item, invoke):
    """GENERIC2 的两次读；提示与 53/58 逐字相同，第二次接收首次答案与推理。"""
    first = invoke('NO_FACT_FIRST', 'read1', '',
                   X.COMMON + '\n' + json.dumps({'question': item['question']}, ensure_ascii=False),
                   item['image'])
    a = X.parse_common(first) or {}
    second = invoke('NO_FACT_FIRST', 'read2', '',
                    X.REREAD_JSON + '\n' + json.dumps({'question': item['question'],
                                                       'previous_answer': a.get('answer'),
                                                       'previous_reasoning': a.get('reasoning', '')},
                                                      ensure_ascii=False),
                    item['image'])
    b = X.parse_common(second) or {}
    record = {'read1': first, 'read2': second,
              'first_answer': a.get('answer'), 'second_answer': b.get('answer'),
              'second_receives': ['question', 'previous_answer', 'previous_reasoning']}
    ans = b.get('answer') or a.get('answer')
    if ans is None or str(ans).strip() == '':
        record['issue'] = 'invalid_initial_answer'
        return None, record
    init = {'answer': ans,
            'reasoning': b.get('reasoning') or a.get('reasoning') or '',
            'supporting_ids': [],
            'scope': 'OPEN: generic two-pass initial; no observation ledger by design',
            'unresolved': [], 'evidence': [], 'question_plan': None, 'computed_aux': [],
            'initial_source': 'GENERIC2'}
    return init, record


INITIAL = {'FULL': initial_full, 'NO_FACT_FIRST': initial_generic2,
           'GENERIC_ACQ': initial_full, 'NO_ACQ': initial_full, 'NO_GATE': initial_full}

# 初态阶段（按臂）真正发生的阶段名，用于成本归集
INITIAL_PHASES = {'FULL': ('observe_facts', 'initial_answer'),
                  'NO_FACT_FIRST': ('read1', 'read2'),
                  'GENERIC_ACQ': ('observe_facts', 'initial_answer'),
                  'NO_ACQ': ('observe_facts', 'initial_answer'),
                  'NO_GATE': ('observe_facts', 'initial_answer')}


def run_arm(arm, item, invoke):
    """跑一条完整链路（初态 + 检查/修复）。FULL 走原实现。"""
    if arm == 'FULL':
        init, rec = F36.initial(item, invoke)
        out = E36.run(item, init, invoke) if init else {'answer': None, 'status': 'INITIAL_MISSING', 'calls': []}
        return init, rec, out
    init, rec = INITIAL[arm](item, invoke)
    out = run_chain(item, init, invoke, arm) if init else {'answer': None, 'status': 'INITIAL_MISSING', 'calls': [], 'arm': arm}
    return init, rec, out

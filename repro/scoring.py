# -*- coding: utf-8 -*-
"""统一实验入口｜隔离评价端（零 API 调用；两臂共用同一评分）。

来源: d20_run.py:780-833（eval_one / evaluate_arm）整体迁移。

纪律（D12 冻结口径，两臂一致）：
  - **本模块是唯一读官方 answer/exe_ans 的位置**，运行端不读；
  - 显示/执行分离报告：显示 = ROUND_HALF_UP 到官方小数位后比较（M8，
    display-v3：小数位只计数字字符，百分号不计入）；执行 = 带 % 预测
    先 ÷100，再 atol 1e-5 + rtol 1e-8×|ref|；
  - 执行值不通过不代表任务语义错误。
"""
import re
from decimal import Decimal, ROUND_HALF_UP


from . import numeric_helpers as E
from .display_scoring import display_decimals, display_number


def _rhu(x, nd):
    """ROUND_HALF_UP（来源: d20_run.py:789-793）。"""
    if x is None:
        return None
    q = Decimal(1).scaleb(-int(nd))
    return float(Decimal(str(x)).quantize(q, rounding=ROUND_HALF_UP))


def reference_of(item):
    """参考适配层（D43）：兼容两种清单形状，评分口径不变。

    - 旧形状（d11_inputs.jsonl / D25 evaluation_only.json）：{"gold": {"official": {...}}}
    - 平铺形状（D43 d43_dev_eval.jsonl）：{"answer": ..., "exe_ans": ...}
    只做字段定位，不做任何换算或容差调整。
    """
    gold = item.get("gold")
    if isinstance(gold, dict):
        return gold.get("official") or {}
    return item


def _decimals(x):
    s = str(x).strip().replace("%", "").replace(",", "").replace("$", "")
    return len(s.split(".")[1]) if "." in s else 0


def reference_profile(item):
    """参考单位约定（D43 修正）：只依据参考自身判断，不看任何臂的预测。

    两套数据集的百分数登记方式不同：
      - FinQA：answer="69.3%"、exe_ans=0.69322（显示百分点 / 执行分数）
      - HiTab：answer=27.6、exe_ans=27.6（两者同为百分点）
    旧实现只看"预测里有没有 %"就 ÷100，会把 HiTab 的执行值整类判错。
    这里用"官方 answer 与 exe_ans 的**尺度关系**"定约定，容差给到半个/一个末位：
      - same：answer 与 exe_ans 同尺度 → 预测永不 ÷100
      - div ：answer 是百分点、exe_ans 是分数 → 预测带 % 时 ÷100
    因此不依赖任何模型输出，也不可能按方法表现择优。

    返回 {"mode": same|div, "consistency": ok|rounding|inconsistent}
    """
    off = reference_of(item)
    a, e = display_number(off.get("answer")), E.num(off.get("exe_ans"))
    pct = "%" in str(off.get("answer") or "")
    if e is None:
        return {"mode": "same", "consistency": "no_exec_reference",
                "answer": off.get("answer"), "exe_ans": off.get("exe_ans")}
    if a is None:
        # 官方显示值无法被数值解析（如 "-.62"）：参考存在，只是显示层不可解析
        return {"mode": "same", "consistency": "display_unparsable",
                "answer": off.get("answer"), "exe_ans": off.get("exe_ans")}
    tol = 1e-5 + 1e-8 * abs(e)
    ulp = 10.0 ** (-_decimals(off.get("answer")))
    d_same, d_div = abs(a - e), abs(a / 100.0 - e)
    if d_same <= 0.5 * ulp + tol:
        return {"mode": "same", "consistency": "ok", "abs_diff": round(d_same, 10),
                "answer": off.get("answer"), "exe_ans": off.get("exe_ans")}
    if d_div <= 0.5 * ulp / 100.0 + tol:
        return {"mode": "div", "consistency": "ok", "abs_diff": round(d_div, 10),
                "answer": off.get("answer"), "exe_ans": off.get("exe_ans")}
    mode = "same" if d_same < d_div else "div"
    d = min(d_same, d_div)
    scale = 1.0 if mode == "same" else 100.0
    cons = "rounding" if d <= ulp / scale + tol else "inconsistent"
    return {"mode": mode, "consistency": cons, "abs_diff": round(d, 10),
            "answer": off.get("answer"), "exe_ans": off.get("exe_ans")}


def eval_one(sid, ans, item):
    """单样本判定。（来源: d20_run.py:780-819，逐行迁移）

    item 接受旧嵌套形状或 D43 平铺形状（见 reference_of）。
    执行比较的百分数换算由参考单位约定决定（reference_profile），
    不再由预测文本决定；参考自相矛盾只标 ref_consistency，不改变分母。
    """
    off = reference_of(item)
    prof = reference_profile(item)
    disp = off.get("answer")
    if isinstance(disp, list):
        disp = disp[0] if disp else None
    exe = off.get("exe_ans")
    d_ok = e_ok = None
    if disp is not None:
        if ans is None or str(ans).strip() == "":
            d_ok = False
        else:
            p, q = display_number(ans), display_number(disp)
            dec = display_decimals(disp) if q is not None else 0
            d_ok = (E.norm(ans) == E.norm(disp)) if (p is None or q is None) \
                else abs(_rhu(p, dec) - q) < 1e-9
    if exe is not None:
        if ans is None or str(ans).strip() == "":
            e_ok = False
        else:
            p = E.num(ans)
            if p is not None and "%" in str(ans) and prof["mode"] == "div":
                p = p / 100.0
            e_ok = bool(E.score_value(p, exe)[0]) if p is not None else False
    return {"sample_id": sid, "answer": ans,
            "official_answer": disp, "official_exe_ans": exe,
            "unit_mode": prof["mode"], "ref_consistency": prof["consistency"],
            "display_match": d_ok, "exec_match": e_ok}


def evaluate_answers(arm, answers, inputs, sids=None):
    """臂级评价汇总。（来源: d20_run.py:822-833 evaluate_arm，改为显式传
    answers: {sid: 最终答案}，不再绑定全局 SAMPLES —— 两臂/任意样本集通用。）
    """
    sids = sids or [s for s in answers if s in inputs]
    rows = [eval_one(sid, answers.get(sid), inputs[sid])
            for sid in sids if sid in inputs]
    n_d = sum(1 for r in rows if r["display_match"] is not None)
    h_d = sum(1 for r in rows if r["display_match"])
    n_e = sum(1 for r in rows if r["exec_match"] is not None)
    h_e = sum(1 for r in rows if r["exec_match"])
    return {"arm": arm, "rows": rows,
            "display": "%d/%d" % (h_d, n_d), "exec": "%d/%d" % (h_e, n_e),
            "note": "显示/执行匹配分开；执行值不通过不代表任务语义错误；"
                    "隔离评价端为唯一读官方答案的位置"}


# ---------------------------------------------------------------- D44 显式评价入口
D44_SCORING_VERSION = "d44-eval-v2_20261027"


def check_display(answer, display_reference, decimals):
    """显示匹配：按**冻结的每样本**显示参考与小数位比较（ROUND_HALF_UP）。

    与 eval_one 的区别：参考单位已逐题冻结，不再在评分时推断；
    `display_reference` 是百分点/原值，调用方负责提供。
    """
    p = E.num(answer)
    if p is None or display_reference is None:
        return None
    return abs(_rhu(p, int(decimals)) - float(display_reference)) < 1e-9


def check_canonical(canonical, canonical_reference):
    """规范数值匹配：沿用冻结容差（atol 1e-5 + rtol 1e-8×|ref|）。

    兼容旧调用（不换算）；D44 主路径用 check_canonical_v2。
    """
    if canonical is None or canonical_reference is None:
        return None
    return bool(E.score_value(canonical, canonical_reference)[0])


def _model_space(display_unit):
    """模型显示单位 → 模型规范值所在空间（percent 显示 ⇒ 规范值为分数）。"""
    return {"percent": "fraction", "pp": "pp", "number": "number"}.get(display_unit)


def check_canonical_v2(canonical, canonical_reference, model_display_unit,
                       canonical_space):
    """D44 规范比较 v2：模型规范值先**显式换算**到参考规范空间，再按冻结容差比较。

    参考侧：`canonical_reference` 来自 exe_ans（执行空间全精度），`canonical_space`
    逐题冻结（fraction=分数执行空间；number=普通数执行空间）。
    模型侧：规范值空间由模型自己的 display_unit 决定。
    换算表（模型空间 → 参考空间）：
      fraction→fraction、number→number：直接（全精度，converted=False）
      fraction→number、pp→number     ：显示数值（converted=True）
      pp→fraction                    ：显示数值/100（converted=True）
      number→fraction                ：歧义（plain 数无法判定是比值还是百分数），
                                       不猜，match=None + scale_ambiguous
    返回 {"match": bool|None, "converted": bool, "basis": str}
    """
    if canonical is None or canonical_reference is None:
        return {"match": None, "converted": False, "basis": "missing_value"}
    ms = _model_space(model_display_unit)
    if ms is None:
        return {"match": None, "converted": False, "basis": "model_unit_unknown"}
    if canonical_space is None:
        return {"match": None, "converted": False, "basis": "reference_space_unknown"}
    if ms == canonical_space:
        val, converted = canonical, False
    elif ms == "fraction" and canonical_space == "number":
        val, converted = canonical * 100.0, True
    elif ms == "pp" and canonical_space == "number":
        val, converted = canonical, True
    elif ms == "pp" and canonical_space == "fraction":
        val, converted = canonical / 100.0, True
    elif ms == "number" and canonical_space == "fraction":
        return {"match": None, "converted": False, "basis": "scale_ambiguous"}
    else:
        return {"match": None, "converted": False, "basis": "scale_unmapped"}
    return {"match": bool(E.score_value(val, canonical_reference)[0]),
            "converted": converted,
            "basis": "direct" if not converted else "converted"}

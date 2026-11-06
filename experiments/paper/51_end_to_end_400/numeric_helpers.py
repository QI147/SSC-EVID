import re
from decimal import Decimal
ATOL,RTOL=Decimal("1e-5"),Decimal("1e-8")
def norm(s):
    if s is None:
        return ""
    s = str(s).lower().replace("$", " ").replace(",", " ").replace("%", " ")
    s = re.sub(r"[^0-9a-z\u4e00-\u9fff\.\- ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def num(s):
    if s is None:
        return None
    t = re.sub(r"[^0-9.\-]", "", str(s).replace("$", "").replace(",", "").replace("%", ""))
    if t in ("", "-", "."):
        return None
    try:
        return float(t)
    except ValueError:
        return None

def score_value(pred, ref_exe):
    """执行一致性：|pred-ref| <= atol + rtol*|ref|。返回 (pass, detail)。"""
    try:
        p, r = Decimal(str(pred)), Decimal(str(ref_exe))
    except Exception:
        return False, "无法解析为数值"
    tol = ATOL + RTOL * abs(r)
    return (abs(p - r) <= tol), f"pred={p} ref={r} tol={tol}"
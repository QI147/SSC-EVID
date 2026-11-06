import json,re
def parse_json_block(text):
    """从模型输出里取第一个完整 JSON 对象；失败返回 (None, 原因)。"""
    if not text:
        return None, "empty"
    s = text.strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.S)
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        return None, "no_brace"
    try:
        return json.loads(s[i:j + 1]), None
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, str(e)[:80])
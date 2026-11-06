"""Display precision only; execution tolerance is unchanged (v3_20261026)."""
import re

VERSION = "display-v3_20261026"


def display_number(value):
    if value is None:
        return None
    text = str(value).strip().replace(',', '').replace('$', '').replace('%', '').strip()
    if not re.fullmatch(r'[+-]?\d+(?:\.\d+)?', text):
        return None
    return float(text)


def display_decimals(reference):
    text = str(reference).strip().replace(',', '').replace('$', '').replace('%', '').strip()
    match = re.fullmatch(r'[+-]?\d+(?:\.(\d+))?', text)
    if not match:
        raise ValueError('Display reference is not a single number: %r' % reference)
    return len(match.group(1) or '')

"""Build a Chinese visual table-QA subset from TableEval (EMNLP 2025, Apache-2.0).

Why this dataset: the paper's three sets are all English, while the target journal
is 中文信息学报 (Chinese information processing).  TableEval ships Simplified
Chinese tables from financial disclosures, government records and industry
reports, with human-verified answers.

Pipeline:
  TableEval-test.jsonl (context_markdown + question + 最终答案)
    -> filter (简体中文, single question, short answer, legible table)
    -> render the markdown table to a PNG at ~1600 px with a Chinese font
    -> datasets/zh_tableeval/{images,runtime.jsonl,eval.jsonl,manifest.json}

The rendered image is a clean grid, not a photographed document: it is easier
than a real scan, so absolute numbers must not be compared with the FinQA
rendered subset.  The point of this set is to verify that the method works on
Chinese content at all, and to give the journal a Chinese data point.

Usage:
    python scripts/build_zh_tableeval.py --raw <TableEval-test.jsonl> --n 200
"""
import argparse
import collections
import hashlib
import json
import random
import re
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from repro import common as C  # noqa: E402

FONT = "C:/Windows/Fonts/msyh.ttc"
WIDTH = 1600
MAX_ROWS = 28
MAX_COLS = 12
MAX_ANSWER = 40
SEED = 20261105
UNANSWERABLE = re.compile("无法确定|无法回答|无法判断|未提供|信息不足|无法得出")


def display_width(text):
    return sum(2 if ord(ch) > 0x2E80 else 1 for ch in str(text))


def wrap(text, limit):
    """Wrap a cell into lines that fit ``limit`` display columns."""
    text = str(text).replace("\n", " ").strip()
    if not text:
        return [""]
    lines, current, width = [], "", 0
    for ch in text:
        step = 2 if ord(ch) > 0x2E80 else 1
        if width + step > limit:
            lines.append(current)
            current, width = ch, step
        else:
            current += ch
            width += step
    lines.append(current)
    return lines[:3]


def parse_markdown(markdown):
    """Return (caption_lines, header, rows) from a markdown table."""
    caption, table = [], []
    for line in str(markdown).splitlines():
        stripped = line.strip()
        if stripped.startswith("|"):
            table.append(stripped)
        elif table:
            break
        elif stripped:
            caption.append(stripped)
    if len(table) < 2:
        return caption, [], []
    def cells(line):
        return [c.strip() for c in line.strip("|").split("|")]
    header = cells(table[0])
    body = [cells(line) for line in table[2:] if not re.fullmatch(r"[\s\|:\-]+", line)]
    return caption, header, body


def render(markdown, path):
    caption, header, body = parse_markdown(markdown)
    if not header or not body:
        return False
    columns = max(len(header), max(len(r) for r in body))
    header += [""] * (columns - len(header))
    body = [r + [""] * (columns - len(r)) for r in body]

    limits = []
    for c in range(columns):
        widest = max([display_width(header[c])] + [display_width(r[c]) for r in body])
        limits.append(max(12, min(widest, 34)))

    font_size = 30
    for _ in range(12):
        char_w = font_size * 0.62
        total = sum(limit * char_w + 18 for limit in limits)
        if total <= WIDTH * 0.94:
            break
        font_size -= 1
        if font_size < 12:
            break
    char_w = font_size * 0.62
    total = sum(limit * char_w + 18 for limit in limits)
    # The caption must wrap to the *actual* image width, not the nominal WIDTH:
    # narrow tables produce ~1000 px images, and a 1600 px wrap would clip text.
    width = int(min(WIDTH, total + 16))

    font = ImageFont.truetype(FONT, font_size)
    head_font = ImageFont.truetype(FONT, font_size + 1)
    cap_font = ImageFont.truetype(FONT, font_size)

    wrapped = [[wrap(cell, lim) for cell, lim in zip(row, limits)] for row in body]
    head_lines = [wrap(cell, lim) for cell, lim in zip(header, limits)]
    row_h = [max(len(lines) for lines in row) * (font_size + 6) + 10 for row in wrapped]
    head_h = max(len(lines) for lines in head_lines) * (font_size + 6) + 10
    cap_lines = []
    cap_limit = max(20, int((width - 24) / char_w))
    for line in caption:
        cap_lines += wrap(line, cap_limit)
    cap_h = len(cap_lines) * (font_size + 6) + (8 if caption else 0)
    height = int(cap_h + head_h + sum(row_h) + 16)
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)

    y = 6
    for line in cap_lines:
        draw.text((8, y), line, fill=(60, 60, 60), font=cap_font)
        y += font_size + 6
    y = int(cap_h)

    x = 8
    spans = []
    for limit in limits:
        spans.append(x)
        x += limit * char_w + 18

    draw.rectangle([8, y, 8 + sum(l * char_w + 18 for l in limits), y + head_h],
                   fill=(238, 238, 238))
    x = 8
    for cell, limit, lines in zip(header, limits, head_lines):
        ty = y + 5
        for line in lines:
            draw.text((x + 4, ty), line, fill=(20, 20, 20), font=head_font)
            ty += font_size + 6
        x += limit * char_w + 18
    y += head_h

    for index, row in enumerate(wrapped):
        if index % 2 == 1:
            draw.rectangle([8, y, 8 + sum(l * char_w + 18 for l in limits), y + row_h[index]],
                           fill=(250, 250, 252))
        x = 8
        for lines, limit in zip(row, limits):
            ty = y + 5
            for line in lines:
                draw.text((x + 4, ty), line, fill=(30, 30, 30), font=font)
                ty += font_size + 6
            x += limit * char_w + 18
        y += row_h[index]

    right = 8 + sum(l * char_w + 18 for l in limits)
    draw.line([8, cap_h, right, cap_h], fill=(120, 120, 120), width=2)
    for row_y in [cap_h + head_h + sum(row_h[:i]) for i in range(len(row_h) + 1)]:
        draw.line([8, row_y, right, row_y], fill=(200, 200, 200), width=1)
    for span in spans + [right]:
        draw.line([span, cap_h, span, cap_h + head_h + sum(row_h)], fill=(200, 200, 200), width=1)

    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "PNG", optimize=True)
    return True


def canonical(answer):
    text = str(answer).strip().replace("\u3000", " ")
    text = re.sub(r"(?<=\d),(?=\d{3})", "", text)
    text = text.replace("％", "%").replace("（", "(").replace("）", ")")
    return re.sub(r"\s+", " ", text).strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", required=True)
    parser.add_argument("--n", type=int, default=200)
    parser.add_argument("--dataset", default="zh_tableeval")
    args = parser.parse_args()

    records = [json.loads(line) for line in open(args.raw, encoding="utf-8")]
    dropped = collections.Counter()
    candidates = []
    for record in records:
        ctx = record.get("context") or {}
        if ctx.get("table_language") != "简体中文":
            continue
        questions = record.get("question_list") or []
        if len(questions) != 1:
            continue
        # TableEval stores a *list* of sub-questions, each with a *list* of
        # acceptable answers.  The frozen scorer reads a single ``answer``
        # field and ignores ``alts``.  Keeping composite/multi-answer items
        # would silently score a correct full answer as wrong (e.g. gold
        # "文莱" for a two-part question, or only "墨西哥" when both
        # 墨西哥 and 加拿大 are correct).  Only 1-question/1-answer items
        # are comparable under this scorer.
        pairs = [p for block in (record.get("golden_answer_list") or [])
                 for p in (block.get("问题列表") or [])]
        if len(pairs) != 1:
            dropped["复合子问题"] += 1
            continue
        gold = [a for a in (pairs[0].get("最终答案") or []) if str(a).strip()]
        if len(gold) != 1:
            dropped["多答案"] += 1
            continue
        # Items whose gold is a refusal cannot be scored by exact match and
        # this method has no abstention mechanism, so they are out of scope.
        if UNANSWERABLE.search(str(gold[0])):
            dropped["不可回答"] += 1
            continue
        if len(canonical(gold[0])) > MAX_ANSWER:
            dropped["答案过长"] += 1
            continue
        markdown = ctx.get("context_markdown") or ""
        _, header, body = parse_markdown(markdown)
        if not (2 <= len(header) <= MAX_COLS and 2 <= len(body) <= MAX_ROWS):
            dropped["表格不可渲染"] += 1
            continue
        if len(markdown) < 80:
            dropped["表格过短"] += 1
            continue
        candidates.append({"record": record, "question": questions[0], "answers": gold,
                           "markdown": markdown, "task": record.get("task_name")})
    print("候选 %d 题；剔除：%s" % (len(candidates), dict(dropped)))

    # balance the sample across tasks so the set is not all one question type
    rng = random.Random(SEED)
    by_task = {}
    for item in candidates:
        by_task.setdefault(item["task"], []).append(item)
    for items in by_task.values():
        rng.shuffle(items)
    picked, tasks = [], sorted(by_task, key=lambda t: -len(by_task[t]))
    while len(picked) < args.n and any(by_task.values()):
        for task in tasks:
            if by_task[task] and len(picked) < args.n:
                picked.append(by_task[task].pop())

    target = C.DATA / args.dataset
    if target.exists():
        shutil.rmtree(target)
    (target / "images").mkdir(parents=True)

    runtime, evaluation, tables = [], [], {}
    for order, item in enumerate(picked):
        record = item["record"]
        sample_id = "ZH%03d" % (order + 1)
        image_path = target / "images" / (sample_id + ".png")
        if not render(item["markdown"], image_path):
            continue
        digest = hashlib.sha256(image_path.read_bytes()).hexdigest()
        group = hashlib.sha256(item["markdown"].encode("utf-8")).hexdigest()
        tables[group] = tables.get(group, 0) + 1
        runtime.append({
            "sample_id": sample_id,
            "dataset": "tableeval_zh",
            "question": item["question"],
            "image": str(image_path.relative_to(C.ROOT)).replace("\\", "/"),
            "image_sha256": digest,
            "source": {"pool": "TableEval", "task": item["task"],
                       "table_id": record.get("table_id"), "qa_id": record.get("id")},
            "source_group": group,
            "source_group_basis": "normalized_markdown_table_sha256",
            "table_pool_size": 1,
            "selected_from_same_table": 1,
            "order": order,
            "cohort": "zh_heldout200",
            "input_condition": "rendered_table_image",
            "experiment_family": "chinese_tableeval",
        })
        evaluation.append({
            "sample_id": sample_id,
            "answer": canonical(item["answers"][0]),
            "exe_ans": None,
            "alts": [canonical(a) for a in item["answers"]],
        })

    def dump(path, rows):
        path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                        encoding="utf-8")

    dump(target / "runtime.jsonl", runtime)
    dump(target / "eval.jsonl", evaluation)
    manifest = {
        "n": len(runtime),
        "tables": len(tables),
        "source_counts": {"tableeval_zh": len(runtime)},
        "scope": "Chinese held-out subset rendered from TableEval markdown tables; "
                 "single sub-question, exactly one gold answer, answerable, short answer, "
                 "clean grid render. Composite/multi-answer/refusal items excluded because "
                 "the frozen scorer compares one string only.",
        "license": "TableEval is Apache-2.0; cite Zhu et al., EMNLP 2025",
        "source_repo": "wenge-research/TableEval (TableEval-test.jsonl)",
        "filter_dropped": dict(dropped),
        "invalid_tasks": {
            "表格结构理解": "题目询问合并单元格；干净网格渲染压平了层级/嵌套结构，"
                           "这些题在渲染后的图像上失去原评测意义，不计入有效评测。",
        },
        "valid_task_count": sum(1 for r in runtime
                                if r["source"]["task"] != "表格结构理解"),
        "note": "题目集合与图片在 2026-11-05 定稿后未改动；本字段为事后标注，"
                "不触发重抽或重渲染，已跑结果仍可复现。",
        "seed": SEED,
        "task_counts": {t: sum(1 for r in runtime if r["source"]["task"] == t)
                        for t in {r["source"]["task"] for r in runtime}},
    }
    (target / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

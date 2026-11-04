"""SSC baseline: the author's prompts, parsers and three-step branching, with image input.

The author's source is **not redistributed here** (no licence was found in the
checkout that was used), so this adapter loads it from ``vendor/ssc_author`` on
demand. Drop ``prompts.py`` and ``parser.py`` from the SSC release into that
folder, or point ``EVID_SSC_SRC`` at your checkout.

Only the transport changes: the table is supplied as an image instead of a
serialized text table. That is a condition adaptation, not a reproduction of the
author's original text-table experiment.
"""
import importlib.util
import os
from pathlib import Path

DEFAULT_SRC = Path(os.environ.get("EVID_SSC_SRC")
                   or Path(__file__).resolve().parent.parent / "vendor" / "ssc_author")


def available():
    return all((DEFAULT_SRC / (name + ".py")).exists() for name in ("prompts", "parser"))


def modules():
    if not available():
        raise SystemExit(
            "SSC author code not found in %s.\n"
            "The baseline is optional: pass --no-ssc to run EVID only, or place the "
            "author's prompts.py and parser.py in vendor/ssc_author/ (see docs/data.md)." % DEFAULT_SRC)
    out = []
    for name in ("prompts", "parser"):
        spec = importlib.util.spec_from_file_location("ssc_author_original_" + name,
                                                      DEFAULT_SRC / (name + ".py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        out.append(module)
    return out


def parse_initial(resp):
    if resp.get("status") != "ok" or not resp.get("content"):
        return None
    parsed = modules()[1].parse_step1(resp["content"])
    return parsed if parsed.get("answer") else None


def finish(item, initial, invoke):
    prompts, parser = modules()
    benchmark = "faketab" if item.get("dataset") in ("fintabnetqa", "finqa_rendered") else "wikitq"
    table = "[See the supplied table image.]"
    out = {"answer": initial["answer"], "status": "INITIAL", "events": [], "calls": [],
           "reasoning_source": "model",
           "adaptation": "author prompts/parsers/branching, visual transport"}
    system, user = prompts.get_prompts(benchmark, "step2", variant="ssc")
    resp = invoke("SSC", "review", system,
                  user.format(table=table, question=item["question"],
                              answer=initial["answer"], reasoning=initial["reasoning"]),
                  item["image"])
    out["calls"].append(resp)
    if resp.get("status") != "ok":
        out["status"] = "REVIEW_FAILED"
        return out
    parsed = parser.parse_step2(resp.get("content", ""))
    out["events"].append({"stage": "review", "raw": resp.get("content", ""), **parsed})
    if parsed["verdict"] != "incorrect":
        out["status"] = "REVIEW_ACCEPTED" if parsed["verdict"] == "correct" else "REVIEW_UNKNOWN"
        return out
    system, user = prompts.get_prompts(benchmark, "step3")
    resp = invoke("SSC", "correction", system,
                  user.format(table=table, question=item["question"], answer=initial["answer"],
                              error_analysis=parsed["error_analysis"]),
                  item["image"])
    out["calls"].append(resp)
    if resp.get("status") != "ok":
        out["status"] = "CORRECTION_FAILED"
        return out
    corrected = parser.parse_step3(resp.get("content", ""))
    out["events"].append({"stage": "correction", "raw": resp.get("content", ""), **corrected})
    out.update(answer=corrected["corrected_answer"], status="CORRECTION_PROPOSED")
    return out

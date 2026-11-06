"""Live dashboard for the second-model run.

Reads the append-only shard files the harness writes, so it needs no cooperation
from the running job and never touches its state. Serves one page on
127.0.0.1 and refreshes the numbers every two seconds.

    python scripts/dashboard.py            # http://127.0.0.1:8777
    python scripts/dashboard.py --port 9000
"""
import argparse
import json
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs"
STAGE = "e2e"
DATASETS = ["mixed400_dsflash", "numeric400_dsflash"]
SHARDS = 12
STARTED = time.time()

PAGE = """<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<title>SSC-EVID · DeepSeek 实时进度</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0d1117;color:#e6edf3;font:14px/1.5 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;padding:28px 32px}
h1{font-size:18px;font-weight:600;letter-spacing:.3px}
.sub{color:#8b949e;font-size:12px;margin-top:6px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin:22px 0}
.card{background:#161b22;border:1px solid #21262d;border-radius:10px;padding:16px 18px}
.card .k{color:#8b949e;font-size:12px;letter-spacing:.4px}
.card .v{font-size:26px;font-weight:600;margin-top:6px;font-variant-numeric:tabular-nums}
.card .v small{font-size:13px;color:#8b949e;font-weight:400;margin-left:4px}
.bar{height:8px;background:#21262d;border-radius:6px;overflow:hidden;margin-top:14px}
.bar > i{display:block;height:100%;background:linear-gradient(90deg,#2f81f7,#3fb950);width:0;transition:width .6s ease}
.row{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:4px}
.row b{font-weight:600;font-size:13px}
table{width:100%;border-collapse:collapse;margin-top:8px}
th,td{text-align:right;padding:8px 10px;border-bottom:1px solid #21262d;font-variant-numeric:tabular-nums}
th:first-child,td:first-child{text-align:left}
th{color:#8b949e;font-weight:500;font-size:12px;letter-spacing:.3px}
.tag{display:inline-block;padding:1px 7px;border-radius:20px;font-size:11px;border:1px solid #30363d;color:#8b949e}
.ok{color:#3fb950}.warn{color:#d29922}.bad{color:#f85149}
.shards{display:flex;gap:3px;flex-wrap:wrap;margin-top:10px}
.shards i{width:14px;height:14px;border-radius:3px;background:#21262d;display:block}
.shards i.done{background:#238636}
.shards i.part{background:#9e6a03}
footer{margin-top:26px;color:#6e7681;font-size:11px}
</style></head>
<body>
<h1>SSC-EVID · DeepSeek-V4.1-Flash 端到端实验</h1>
<div class="sub" id="sub">连接中…</div>

<div class="grid">
  <div class="card"><div class="k">总进度</div><div class="v" id="pct">–<small>%</small></div>
    <div class="bar"><i id="pbar"></i></div></div>
  <div class="card"><div class="k">已完成题目</div><div class="v" id="done">–<small>/ 800</small></div></div>
  <div class="card"><div class="k">已发请求</div><div class="v" id="calls">–</div></div>
  <div class="card"><div class="k">估算费用</div><div class="v" id="cost">–<small>元</small></div></div>
  <div class="card"><div class="k">已用时 / 预计剩余</div><div class="v" id="time" style="font-size:20px">–</div></div>
</div>

<div class="card" style="margin-bottom:16px">
  <div class="row"><b>数据集</b><span class="tag" id="health">–</span></div>
  <div id="ds"></div>
</div>

<div class="card">
  <div class="row"><b>各臂调用量</b><span class="sub" style="margin:0">解析失败 <span id="fail">0</span> · 空响应 <span id="empty">0</span></span></div>
  <table><thead><tr><th>臂</th><th>调用</th><th>平均输出 token</th><th>解析失败</th></tr></thead>
  <tbody id="arms"></tbody></table>
</div>

<footer id="foot">数据每 2 秒从 runs/ 目录直接读取，不干扰正在运行的任务。</footer>

<script>
const fmt = n => n.toLocaleString('zh-CN');
function pad(n){return String(n).padStart(2,'0')}
function dur(s){ if(s<0||!isFinite(s)) return '–'; const h=Math.floor(s/3600),m=Math.floor(s%3600/60);
  return h? `${h}h ${pad(m)}m` : `${m}m ${pad(Math.floor(s%60))}s`; }
async function tick(){
  try{
    const r = await fetch('/api/progress', {cache:'no-store'});
    const d = await r.json();
    document.getElementById('sub').textContent =
      `${d.model} · 分片 ${d.shards} · 更新于 ${d.now}`;
    document.getElementById('pct').innerHTML = d.percent.toFixed(1) + '<small>%</small>';
    document.getElementById('pbar').style.width = d.percent + '%';
    document.getElementById('done').innerHTML = fmt(d.done) + '<small>/ ' + fmt(d.total) + '</small>';
    document.getElementById('calls').textContent = fmt(d.calls);
    document.getElementById('cost').innerHTML = d.cost.toFixed(2) + '<small>元</small>';
    document.getElementById('time').textContent = dur(d.elapsed) + ' / ' + dur(d.eta);

    document.getElementById('ds').innerHTML = d.datasets.map(x => `
      <div style="margin-top:14px">
        <div class="row"><span style="font-size:13px">${x.name}</span>
          <span class="sub" style="margin:0">${x.done} / ${x.total}　${x.calls} 次调用　¥${x.cost.toFixed(2)}</span></div>
        <div class="bar"><i style="width:${(100*x.done/x.total).toFixed(1)}%"></i></div>
        <div class="shards">${x.shards.map(s => `<i class="${s>=x.quota?'done':(s>0?'part':'')}" title="shard ${s}"></i>`).join('')}</div>
      </div>`).join('');

    document.getElementById('arms').innerHTML = d.arms.map(a => `
      <tr><td>${a.arm}</td><td>${fmt(a.calls)}</td><td>${a.avg_out.toFixed(0)}</td>
      <td class="${a.fail?'bad':'ok'}">${a.fail}</td></tr>`).join('');
    document.getElementById('fail').innerHTML = d.parse_fail
      ? `<span class="bad">${d.parse_fail}</span>` : `<span class="ok">0</span>`;
    document.getElementById('empty').innerHTML = d.empty
      ? `<span class="bad">${d.empty}</span>` : `<span class="ok">0</span>`;

    const h = document.getElementById('health');
    if(d.finished){ h.textContent='已完成'; h.style.color='#3fb950'; h.style.borderColor='#238636'; }
    else if(d.parse_fail>0 || d.empty>0){ h.textContent='有异常'; h.style.color='#f85149'; }
    else { h.textContent='运行正常'; h.style.color='#3fb950'; }
  }catch(e){
    document.getElementById('sub').textContent = '等待服务…（' + e.message + '）';
  }
}
tick(); setInterval(tick, 2000);
</script></body></html>"""


def count_lines(path):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            return sum(1 for line in handle if line.strip())
    except OSError:
        return 0


def snapshot():
    total, done, calls, cost = 0, 0, 0, 0.0
    parse_fail = empty = 0
    arms = {}
    datasets = []
    started = None

    for name in DATASETS:
        directory = RUNS / name
        runtime = ROOT / "datasets" / name / "runtime.jsonl"
        quota = count_lines(runtime) or 400
        total += quota

        stage = directory / STAGE
        per_shard = [count_lines(stage / ("results_s%d.jsonl" % i)) for i in range(SHARDS)]
        done += sum(per_shard)

        ds_calls = ds_cost = 0
        for ledger in sorted(stage.glob("*_calls.jsonl")):
            mtime = ledger.stat().st_mtime
            started = mtime if started is None else min(started, mtime)
            with open(ledger, "r", encoding="utf-8", errors="ignore") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                    except Exception:
                        continue
                    if record.get("reused"):
                        continue
                    ds_calls += 1
                    calls += 1
                    ds_cost += record.get("cost_cny") or 0.0
                    cost += record.get("cost_cny") or 0.0
                    usage = record.get("usage") or {}
                    arm = record.get("arm") or "?"
                    bucket = arms.setdefault(arm, {"calls": 0, "out": 0, "fail": 0})
                    bucket["calls"] += 1
                    bucket["out"] += usage.get("completion_tokens", 0)
                    if not (record.get("response_text") or "").strip():
                        empty += 1
                    if record.get("json_parse_error"):
                        parse_fail += 1
                        bucket["fail"] += 1

        datasets.append({"name": name, "done": sum(per_shard), "total": quota,
                         "calls": ds_calls, "cost": ds_cost,
                         "quota": max(1, quota // SHARDS), "shards": per_shard})

    elapsed = time.time() - (started or STARTED)
    rate = done / elapsed if elapsed > 0 and done else 0.0
    eta = (total - done) / rate if rate > 0 else -1

    return {
        "model": "deepseek-v4.1-flash-1017",
        "shards": SHARDS,
        "now": datetime.now().strftime("%H:%M:%S"),
        "done": done, "total": total,
        "percent": 100.0 * done / total if total else 0.0,
        "calls": calls, "cost": cost,
        "parse_fail": parse_fail, "empty": empty,
        "elapsed": elapsed, "eta": eta,
        "finished": done >= total and total > 0,
        "datasets": datasets,
        "arms": [{"arm": k, "calls": v["calls"],
                  "avg_out": v["out"] / max(v["calls"], 1), "fail": v["fail"]}
                 for k, v in sorted(arms.items())],
    }


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/api/progress"):
            payload = json.dumps(snapshot(), ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        body = PAGE.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8777)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print("http://127.0.0.1:%d" % args.port, flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()

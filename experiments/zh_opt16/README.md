# 中文优化试点（2026-11-05，16 题）

> **这不是留出测试集，也不是显著性结论。** 16 题是从 `datasets/zh_tableeval`（200 题）
> 按任务轮转、表哈希去重选出的**优化材料**，选样时不看模型成绩。三轮都是同 16 题，
> 只用于诊断中文表述、任务类型与空集判定；最终结论须在未参与优化的新表上验证。
> 原论文的主评分与金标未改动。

## 三轮在改什么

| 变体 | 改动 | 触发方式 |
|---|---|---|
| v36 | 论文原契约（36 版），作为对照 | `EVID_PROMPT_VARIANT=36` |
| v40 | 加强中文任务类型识别、完整比较范围、支持充分性；不改评分、金标、SSC 提示与单题配额 | `EVID_PROMPT_VARIANT=40` |
| v41 | 在 v40 上增加「经完整检查的空集是有效答案」、严格 AND/OR 与边界，禁止猜测题目意图或取最接近项 | `EVID_PROMPT_VARIANT=41` |

`variants/v36_v40/evid36/` 是 v36 与 v40 共用的代码树（靠环境变量切换），
`variants/v41/evid36/` 是 41 版单独副本。根目录 `evid36/` 仍是论文冻结方法，
**没有被任何一轮覆盖**；`freeze.json` 里的文件哈希对应这里的变体代码，
不要拿它去核对根目录的 `evid36/`。

## 冻结结果

主评分 = 冻结项目评分，宽松 = 表述容错评分；分母 16，McNemar 为配对精确检验。

| 轮次 | SSC 主 | EVID 主 | EVID−SSC | p | 宽松 EVID−SSC |
|---|---:|---:|---:|---:|---:|
| v36 | 4 | 6 | +2（赢 2 / 输 0） | 0.500 | −1 |
| v40 | 4 | 7 | +3（赢 3 / 输 0） | 0.250 | −1 |
| v41 | 3 | 6 | +3（赢 3 / 输 0） | 0.250 | −1 |

区间宽度都在 ±20–38 个百分点量级，宽松口径三轮都没有优势。这些都只是优化诊断，
不得表述为「方法显著优于 SSC」。逐题输出、冻结哈希、配额与运行日志在
`results/<轮次>/`：`results_s0.jsonl`（16 行）、`freeze.json`、`quota.json`、
`paid_budget.json`、`console.log`。v36 与 v40 的 `console.log` 是同一次批运行的
完整输出，所以两份内容相同、各自都含两轮摘要。

`notes/实施计划.md` 记录了 300 次请求 / 约 2 元的停批额度，
`notes/实时审查记录.md` 逐题登记了金标歧义（ZH013 方向、ZH017 行序、
ZH015 空集等），这些歧义没有被用来改金标。

## 数据

`datasets/zh_opt16_v36/`、`_v40/`、`_v41/` 各含 `manifest.json`、
`eval.jsonl`、`runtime.jsonl`，三份**逐字节相同**，仅目录名不同以便 runner 直接
按变体名取数。`runtime.jsonl` 的图片路径指向 `datasets/zh_tableeval/images/`，
每行带 `image_sha256`；图片与 TableEval 原始 30 MB 转储不随仓库发布（见
`docs/data.md`），可用 `scripts/build_zh_tableeval.py` 与
`scripts/import_images.py` 按哈希重建/校验。

## 复现（会真实付费）

```bash
cp .env.example .env            # 填 EVID_API_KEY / EVID_BASE_URL
python scripts/import_images.py --dataset zh_tableeval --source <图片目录>
python experiments/zh_opt16/run_pilot.py        # v36 + v40
python experiments/zh_opt16/run_pilot_v41.py    # v41
python scripts/zh_robust_score.py zh_opt16_v41 --by-task
```

两个入口默认从仓库根读取 `.env`（可用 `EVID_CRED_FILE` 覆盖），单进程串行、
传输只尝试一次、共享同一停批额度；原脚本里写死的本机 `.env` 绝对路径已去掉。
只复算已有的 16 题不需要密钥，但 `scripts/zh_robust_score.py` 与
`scripts/compare_variants.py` 固定读 `runs/<变体>/e2e/results.jsonl`，需先把冻结输出
放回该位置（`runs/` 本身不入库）：

```bash
for v in v36 v40 v41; do
  mkdir -p runs/zh_opt16_$v/e2e
  cp experiments/zh_opt16/results/$v/results_s0.jsonl runs/zh_opt16_$v/e2e/results.jsonl
done
python scripts/zh_robust_score.py zh_opt16_v41 --by-task
python scripts/compare_variants.py
```

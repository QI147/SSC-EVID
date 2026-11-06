# SSC-EVID 代码与复现包（中文说明）

本仓库是论文《SSC-EVID：面向视觉表格问答的事实组织与可追溯取证》的方法代码、冻结结果与复现步骤。
英文主文档见 `README.md`，逐步操作见 `docs/reproduce.md`，论文表格与代码的对应见 `docs/experiment-map.md`。

## 一、这套代码是什么

```
表格图像 + 问题
   ├─ 1 观察事实 ─────────────► 证据账本（i1, i2, …）
   ├─ 2 初始作答 ─────────────► 候选 a0 + 引用
   ├─ 3 支持检查 ─────────────► correct / incorrect / unknown
   │        └─ 缺口 {类型, 目标, 理由} ─► 4 定向取证（a1, …）
   │                                          │ 账本版本 1
   └─ 5 重新评估（新候选）◄───────────────────┘
            └─ 最终复核：只有「判对 + 引用有效 + 无未决项」才交付
```

* `evid36/` 是**方法本体**，只用标准库、不做任何 I/O；默认 `EVID_PROMPT_VARIANT=36`
  即产出论文数据的契约（可选变体与 MD5 口径见 `evid36/README.md`）。
* `repro/` 是实验脚手架：传输、冻结、三种试次、基线、消融臂、评分。
* `results/` 是逐题冻结结果，`scripts/verify_reported_numbers.py` 用它复算论文里的 56 个数字。

## 二、三分钟自检（不需要密钥、不需要图片）

```bash
python -m venv .venv
. .venv/Scripts/activate
pip install -r requirements.txt

python -m unittest discover -s tests -v      # 32 项离线测试，含本地假模型跑通全流程
python scripts/verify_reported_numbers.py    # 56/56 项与论文一致
```

## 三、复现一次真实运行

1. **配凭据**：`cp .env.example .env`，填 `EVID_API_KEY` 与 `EVID_BASE_URL`（任意 OpenAI 兼容端点）。
2. **放图片**：`python scripts/import_images.py --dataset mixed400 --source <图片目录>`，
   脚本逐张核对 SHA256，不一致就拒绝。图片不在仓库里，来源与许可见 `docs/data.md`。
3. **预检**：`python scripts/preflight.py --strict`，打印样本数、金标覆盖、缺失图片、每阶段请求上限。
4. **冻结**：`python -m repro.runner freeze --dataset mixed400`。
5. **跑分片**：`python scripts/run_experiment.py --dataset mixed400 --stage initial --shard 0/8 --authorize-paid`，
   8 个分片跑完后 `--merge`，再 `python -m repro.runner freeze-initial --dataset mixed400`。
6. **端到端与修复**：把 `--stage` 换成 `e2e` 或 `repair`；SSC 作者代码未随仓库分发时加 `--no-ssc`。
7. **评分**：`python scripts/score_all.py`，结果写到 `reports/`。

请求上限由文件锁在所有分片间共享，中断后重跑同一分片会从账本续跑，不会重复采样已完成的逻辑请求。

## 四、论文数字（冻结口径）

| 题集 | 直接读 | 读两次 | SSC | **EVID** | 等预算独立投票 |
|---|---:|---:|---:|---:|---:|
| 混合400 | 76.25%（305） | 78.25%（313） | 80.25%（321） | **87.50%（350）** | 76.00%（304） |
| 数字400 | 68.50%（274） | 71.25%（285） | 77.00%（308） | **81.25%（325）** | 70.00%（280） |
| FinTabNetQA 250（官方 relieved） | — | 98.80%（247） | — | **98.40%（246）** | 98.00%（245） |

共同初态修复：混合 323 → SSC 341 / EVID 346；数字 301 → SSC 316 / EVID 325。
模块消融：完整 352/327，四个删改版本净差在 −5 至 +2 题之间，Holm p 全为 1.00；
正文同时给出触发率（92.6% 停在检查、6.1% 进入取证），说明这是「测不出」而非「无作用」。

## 五、上传前后仍待确认的几件事

1. ~~选许可证~~ 已定：**MIT**，见 `LICENSE`。第三方材料不在此授权范围内（`NOTICE.md`）。
2. **补作者信息**：`CITATION.cff` 已填 `Qi, Kai`；ORCID 与单位留空，未替你猜。
3. ~~图片是否入库~~ 已定：**不入库**。`.gitignore` 排除 `datasets/*/images/` 与
   `datasets/_raw/`（TableEval 原始转储约 30 MB）；每行 `runtime.jsonl` 带图片 SHA256，
   按 `docs/data.md` 的来源自行导入。若要改主意，先核对各来源许可并考虑 Git LFS。
4. **SSC 作者代码**：未随仓库分发（未找到许可证）。公开仓库保持 `vendor/ssc_author/` 为空，
   需要 SSC 对照臂的复现者自行放置 `prompts.py` 与 `parser.py`。
5. **论文草稿是否入库**：`paper/` 目前只放图与图数据，没有放正文，避免双盲评审时泄露作者身份。
   若期刊不要求双盲，再加 `paper/draft/`。

## 六、与内部实验目录的关系

* 方法是 36 版；`evid36/` 与内部 `methods/evid36` 内容一致（仅 CRLF→LF 行尾差异，
  MD5 说明见 `evid36/README.md`）。默认 `EVID_PROMPT_VARIANT=36` 即论文契约。
* 论文五个实验包的**源码**已归档到 `experiments/paper/`（51/52/54/55/59），
  私有端点与本机绝对路径已替换为占位符；约 1 GB 的调用流水、receipts、
  内部数据树仍不入库。归档源码用于溯源，真正可跑的是根目录 `repro/` + `scripts/`。
* 未包含：44/50 版扩展、P1/P2/P3 未执行设计、附录 B 的「带自身历史重读」、
  已被 59 取代的 56/57/58、未执行的 60。逐项原因见 `docs/scope.md`。
* 论文正文（含作者信息与致谢）不入库；`paper/` 只放图、图数据与重绘脚本。

## 七、论文快照之后新增的实验

| 位置 | 内容 | 结论边界 |
|---|---|---|
| `experiments/zh_opt16/` | 2026-11-05 中文优化试点：16 题、v36/v40/v41 三个提示契约、冻结逐题输出与审查记录 | **优化材料，不是留出测试**，不得用于显著性结论 |
| `datasets/zh_opt16_v36/`、`_v40/`、`_v41/` | 同一 16 题的三份清单（逐字节相同，仅目录名不同） | 图片按 SHA256 引用，不随仓库发布 |
| `datasets/zh_tableeval*`、`vtabfact250*`、`vwtqsyn250*`、`*_dsflash*`、`*_qwen*` | 中文 TableEval 200 题、两个合成 TableVQA 分片、第二模型与提示变体子集 | 清单与金标入库，图片不入库 |
| `reports/*.json` | 第二模型端到端、格式鲁棒性的冻结汇总 | 仅这三份 JSON 放行，其余 `reports/` 输出仍忽略 |

新实验不改变 `evid36/` 与 `results/`；`python -m unittest discover -s tests` 和
`python scripts/verify_reported_numbers.py` 仍只校验论文数字。许可与来源见
`docs/data.md`，命令映射见 `docs/experiment-map.md`。

## 八、引用

投稿前请补全 `CITATION.cff`；论文正文中的表 2–5 与本仓库 `results/` 一一对应，
`scripts/verify_reported_numbers.py` 会在数字被改动时直接报错。

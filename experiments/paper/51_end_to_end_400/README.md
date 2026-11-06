# SSC–EVID 数字400与混合400对照

本目录是本地研究与GitHub发布准备包，尚未公开发布。主方法为原36版EVID；SSC采用原作者三步提示、解析与分支，仅适配图像传输及金融来源。50版只作共同初态修复消融。

## 数据与命名

- 数字400：保留原金融200，新增FinQA dev 200。FinQA原表及全部pre/post上下文一并渲染，不能解释为纯原生表格图像测试。来源和输入条件分别报告。
- 混合400：原174金融、226百科不变。百科显示为“文本（百科来源）”，来源标签不等同于所有答案为文本。
- 历史金融200、混合400目录与分数不重命名覆盖。新旧样本ID和图像SHA可追溯。
- FinQA选样只接收无答案投影：固定哈希、输入完整性、每张新增表最多一道，不使用答案、程序、gold_inds、检索排名或模型表现。选择ID先冻结，再构建eval。
- runtime.jsonl可进入运行端；eval.jsonl只供离线评分。金标、HTML、FinQA解题程序/标注不发送模型。

数字400与混合400共享174个题号，共800次执行、626个不同问题，不能合并为800个独立测试。全部为开发实验，不是独立泛化测试。

## 两套独立实验

端到端DIRECT、REREAD2、SSC、EVID各自产生初态。直接读仅答一次；读两次对原图无条件再读一次。SSC一轮原作者检查与条件纠错，EVID采用原事实先行和缺口动作。

修复实验单独生成答案＋简短推理并冻结，不复用旧答案-only结果。KEEP、SSC、EVID、调用匹配重读及EVID50读取同一个COMMON。每题最多两轮；未改且已确认correct早停，改答或无法确认可继续第二轮。EVID保存自身证据并命名空间化旧ID，SSC保留自己的错误分析，绝不伪造新推理。

RR_MATCH按EVID实际逻辑调用数逐轮安排重读，不读取EVID答案、证据或评分。它是调用数匹配，不是token或费用完全相同，后两项照实报告。50版消融只比较修复阶段，共同初态中不额外注入50版自审。

共同初态成本单列；每方法总成本可加共同初态，但不能将重复分摊后的方法成本相加作为全局费用。轮2早停样本沿用当前答案并标executed=false。

## 运行

Python3.11+，运行本身使用标准库；重建数据需Pillow/pyarrow。凭据通过环境变量或BAILIAN_CRED_FILE外部文件，不将真实.env放入目录。

```text
python -m unittest -q
python experiment.py freeze --dataset numeric400
python experiment.py verify --dataset numeric400
python experiment.py run --dataset numeric400 --mode initial --shard 0/8 --authorize-paid
# 启动其余分片1/8…7/8，之后：
python experiment.py merge --dataset numeric400 --mode initial --shards 8
python experiment.py freeze-initial --dataset numeric400
# 对e2e、repair分别运行并合并。mixed400相同。
python evaluate.py --dataset numeric400
python evaluate.py --dataset mixed400
python collect.py
```

每批共享系统文件锁配额；重启从账本与缓存恢复，失败和截断不重试到成功。缓存保留finish_reason及空文本，保证续跑与原解析一致。调用前验证代码、数据JSON、当前图片及共同初态哈希。

两集端到端请求上限9600，共同初态800，修复22400，合计32800。模型qwen3.8-flash、temperature0、thinking=false、max_tokens1200、retry0。没有金额硬闸；cost字段为按冻结输入0.8/输出2.7元每百万token计算的记录估算，不是供应商账单。

## 评价

固定400分母，未提交单列。报告初态、每轮和最终答案；错→对修复率、对→错改错率、对→未提交、错改错、保正确率、净修复和完整3×3转移矩阵。判断评价分别报告错误召回、检错精确率、选择性判断正确率与覆盖率，UNKNOWN不强制归为正确或错误。

原评分eval_one、reference_profile、num/norm、score_value与显示精度函数保持逻辑一致，可移植适配去掉历史全局数据导入；400条真实历史预测回归验证标签一致。模型判断与程序交付状态分别记录。

主比较预设为两集端到端和两集修复R2的SSC–EVID四项，精确McNemar及Holm校正；区间按来源分层、表聚类bootstrap。其他比较为探索性。注入诊断与自然错误主实验分开，不混分。

## 文件与发布

SOURCE_PROVENANCE.json记录原方法与适配；protocol.json为冻结方案。runs保存冻结、共同初态、各分片结果、每轮证据、完整调用账本。reports输出机器可读JSON/CSV和汇总工作簿。

REBUILD_SPEC.json保留上游QA ID、输入条件和图片哈希，不需要历史D10路径即可用rebuild_from_upstream.py重建；FinQA渲染还需与记录SHA一致的Arial字体。直接使用保留PNG可复现实际输入，不需要重新渲染。

TableVQA下载记录声明数据CC-BY-4.0，FinQA上游仓库有MIT许可；原财报图文及数据分发仍需审阅。SSC本地checkout未找到LICENSE，vendor/ssc_author先保留为私有材料，公开前须核对上游授权。本包不自动选定整个仓库的开源许可，不自动推送GitHub。详见PUBLICATION_STATUS.md。

## 一条命令入口

本机运行：启动实验.cmd。脚本依次执行零调用测试、实际依赖/图像校验、初态生成冻结、8片实验、离线评分、CSV/JSON/Excel导出。再次执行沿用当前冻结续跑；程序异常隔离并停批，保留记录。只预检：python run_all.py --check-only。

两集合计32800次为请求硬顶，不是预计调用数；没有金额硬闸。若每次输入8000、输出1200 token，按满32800次约316.19元，仅为情景估算，输入更长时可更高。账本据实际usage记录。

reports/实验汇总.xlsx、结果摘要.md、*_all.csv及all_metrics.json为自动结果；runs保留逐轮与完整调用。公开前须按PUBLICATION_STATUS.md核查授权；启动实验.cmd为本机路径适配，不含密钥。


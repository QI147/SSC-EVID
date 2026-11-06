# 论文补充实验52：FinTabNetQA250与修复预算重读

新批次，原51目录代码和运行记录全部只读。用户于2026-10-30明确授权启动实验一与实验三补充组，允许将实际结果写入桌面论文正文。

- fin250：完整官方原始图像250题，REREAD2与原36版EVID各自初态；最多2000次真实尝试。
- numeric400_rr/mixed400_rr：各400题，复用已冻结COMMON。仅新增RR_TOKEN，旧SSC/EVID不重跑。每题最多16次真实尝试，每集最多6400次。
- 合计真实尝试上限14800，所有传输失败也计数，不退回额度。语义输出、长度截断不重试。传输异常最多额外2次；每次保留独立scope、账本与调用ID。
- endpoint固定为已授权百炼北京主机，模型qwen3.8-flash，temperature0，thinking=false，max_tokens1200。
- token匹配是prompt+completion（含图像），不含共同初态费用。保存预算前后状态，明确实际偏差与未达标，不声称逐token精确相同。
- Fin250官方relieved与exact评分分别保留，另报历史评分。GPT-4V原作者公开预测199/250（relieved）和141/250（exact）已做零调用复核，非本次重跑。

运行前 `python -m unittest -q test_followup`；`python followup.py freeze`；`python followup.py verify`。真实运行 `python -u followup.py all --authorize-paid --shards 8`，只运行新三阶段。再次执行从逻辑调用收据、原始缓存和账本续跑，不重发已经完成的逻辑请求。

运行端只读取runtime、COMMON和数字预算，不读取金标。评分器仅在全部合并后单独进程执行。冻结覆盖代码、协议、实际图片与被复用的输入/结果副本。语义方案不在运行途中修改。

`runs/<stage>/`保存每分片结果、完整调用账本、逻辑收据、quota硬计数；`reports/`保存机器结果与原始长表。未知usage费用为null，不写成零。本批没有金额硬闸。

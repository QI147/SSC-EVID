# -*- coding: utf-8 -*-
"""从冻结数据生成论文统计图 fig3-fig6（SVG/PDF/300dpi PNG）。
运行: python plot_figures.py
输入: paper/figure_data/*.csv（已随仓库发布）
输出: paper/figures/*.svg|pdf|png，与论文定稿一致
"""
import csv, json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
import numpy as np

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / 'figure_data'
FIG = HERE.parent / 'figures'
FIG.mkdir(exist_ok=True)

# 中文字体
for f in ['C:/Windows/Fonts/msyh.ttc', 'C:/Windows/Fonts/simhei.ttf',
          '/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc']:
    if Path(f).exists():
        font_manager.fontManager.addfont(f)
plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False
plt.rcParams['svg.fonttype'] = 'none'  # 矢量文本可编辑
plt.rcParams['pdf.fonttype'] = 42
plt.rcParams['font.size'] = 10

C_DIRECT = '#9aa0a6'; C_SSC = '#4c72b0'; C_EVID = '#c44e52'; C_VOTE = '#55a868'
C_HIST = '#bbbbbb'

def read(name):
    return list(csv.DictReader(open(DATA / name, encoding='utf-8-sig')))

def save(fig, name):
    for ext in ('svg', 'pdf', 'png'):
        kw = {'dpi': 300} if ext == 'png' else {}
        fig.savefig(FIG / f'{name}.{ext}', bbox_inches='tight', **kw)
    plt.close(fig)
    print('saved', name)

# ---------------- 图3 端到端成绩 ----------------
def fig3():
    rows = read('fig3_e2e.csv')
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.2), gridspec_kw={'width_ratios': [1.6, 1]})
    methods = ['直接读', 'SSC', 'SSC-EVID', '独立投票(达预算)']
    colors = [C_DIRECT, C_SSC, C_EVID, C_VOTE]
    ds_names = {'mixed400': '混合400', 'numeric400': '数字400'}
    width = 0.18; x = np.arange(2)
    for i, m in enumerate(methods):
        vals = [next(r for r in rows if r['dataset'] == ds and r['method'] == m) for ds in ds_names]
        acc = [float(v['accuracy']) * 100 for v in vals]
        bars = ax1.bar(x + (i - 1.5) * width, acc, width, label=m, color=colors[i])
        for b, v in zip(bars, vals):
            ax1.text(b.get_x() + b.get_width() / 2, b.get_height() + 0.6, f"{v['correct']}", ha='center', fontsize=9)
    ax1.set_xticks(x); ax1.set_xticklabels([ds_names[d] for d in ds_names])
    ax1.set_ylabel('准确率（%，项目冻结评分，分母400）')
    ax1.set_ylim(0, 100); ax1.legend(fontsize=8.5, loc='upper right', ncol=2)
    ax1.set_title('(a) 端到端主结果（同模型 qwen3.8-flash）')
    ax1.spines[['top', 'right']].set_visible(False)
    # 右：fin250 官方relieved，主结果与历史参考分区
    items = [('SSC-EVID\n(本次实测)', 98.4, C_EVID, '246/250'),
             ('独立投票\n(本次实测)', 98.0, C_VOTE, '245/250'),
             ('GPT-4V\n(2024文献)', 79.6, C_HIST, ''),
             ('GPT-4V→GPT-4\n(2024文献)', 95.2, C_HIST, '')]
    xx = np.arange(len(items))
    for i, (lab, v, c, note) in enumerate(items):
        hatch = '//' if i >= 2 else None
        b = ax2.bar(i, v, 0.6, color=c, hatch=hatch, edgecolor='white' if i >= 2 else c)
        ax2.text(i, v + 1, f'{v:.1f}' + (f'\n{note}' if note else ''), ha='center', fontsize=8.5)
    ax2.axvline(1.5, color='gray', ls=':', lw=1)
    ax2.text(2.5, 40, '历史文献参考\n不同模型与年代\n非受控比较', ha='center', fontsize=8.5, color='gray')
    ax2.set_xticks(xx); ax2.set_xticklabels([it[0] for it in items], fontsize=8)
    ax2.set_ylabel('官方 relieved accuracy（%）')
    ax2.set_ylim(0, 108); ax2.set_title('(b) FinTabNetQA 250（官方指标）')
    ax2.spines[['top', 'right']].set_visible(False)
    fig.tight_layout()
    save(fig, 'fig3_end_to_end')

# ---------------- 图4 预算—性能 ----------------
def fig4():
    rows = read('fig4_budget_checkpoints.csv')
    ds_names = {'fin250': 'FinTabNetQA 250（官方relieved）', 'mixed400': '混合400（项目评分）', 'numeric400': '数字400（项目评分）'}
    fig, axes = plt.subplots(1, 3, figsize=(12.5, 4.0))
    styles = {'预算前/固定次数': ('o', C_VOTE, '独立投票：固定调用次数（预算前）'),
              '预算内': ('s', '#8172b2', '独立投票：预算内最后检查点 LEQ'),
              '达到/超过预算': ('^', '#2ca02c', '独立投票：首次达到/超过预算 GEQ'),
              'EVID参照': ('*', C_EVID, 'SSC-EVID（目标预算处）')}
    seen = set()
    for ax, ds in zip(axes, ds_names):
        sub = [r for r in rows if r['dataset'] == ds]
        vote_pts = sorted([r for r in sub if r['kind'] != 'EVID参照'], key=lambda r: float(r['mean_tokens']))
        ax.plot([float(r['mean_tokens']) for r in vote_pts], [float(r['accuracy']) * 100 for r in vote_pts],
                color='#cccccc', lw=1, zorder=1)
        for r in sub:
            m, c, lab = styles[r['kind']]
            lbl = lab if lab not in seen else None
            seen.add(lab)
            ax.scatter(float(r['mean_tokens']), float(r['accuracy']) * 100, marker=m, c=c, s=90 if m == '*' else 55,
                       zorder=3, label=lbl, edgecolors='white', linewidths=0.6)
            if r['kind'] in ('达到/超过预算', 'EVID参照'):
                ax.annotate(f"{int(r['correct'])}/{r['n']}", (float(r['mean_tokens']), float(r['accuracy']) * 100),
                            textcoords='offset points', xytext=(0, 9), ha='center', fontsize=8.5)
        ax.set_title(ds_names[ds], fontsize=10)
        ax.set_xlabel('平均每题 token（含图像 token）')
        ax.set_ylabel('准确率（%）')
        ax.spines[['top', 'right']].set_visible(False)
        accs = [float(r['accuracy']) * 100 for r in sub]
        lo, hi = min(accs), max(accs)
        ax.set_ylim(lo - 1.5, hi + 2.2)
        ax.grid(axis='y', alpha=0.25)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.tight_layout()
    fig.subplots_adjust(bottom=0.30)
    fig.legend(handles, labels, loc='lower center', ncol=2, fontsize=8.5, bbox_to_anchor=(0.5, 0.01))
    save(fig, 'fig4_budget_performance')

# ---------------- 图5 修复转移 ----------------
def fig5():
    rows = read('fig5_repair_transitions.csv')
    fig, axes = plt.subplots(2, 3, figsize=(12.5, 7.2))
    states = ['C', 'W', 'A']; slab = {'C': '对', 'W': '错', 'A': '未提交'}
    for ri, ds in enumerate(['mixed400', 'numeric400']):
        for ci, arm in enumerate(['SSC', 'EVID']):
            ax = axes[ri][ci]
            r = next(x for x in rows if x['dataset'] == ds and x['arm'] == arm and x['initial'] == '共同初态')
            M = np.zeros((3, 3), dtype=int)
            for i, a in enumerate(states):
                for j, b in enumerate(states):
                    key = f'{a}_to_{b}'
                    M[i, j] = int(r.get(key, 0) or 0)
            im = ax.imshow(M, cmap='Blues', vmin=0, vmax=max(M.max(), 1))
            for i in range(3):
                for j in range(3):
                    ax.text(j, i, str(M[i, j]), ha='center', va='center',
                            color='white' if M[i, j] > M.max() * 0.55 else '#333333', fontsize=11)
            ax.set_xticks(range(3)); ax.set_yticks(range(3))
            ax.set_xticklabels([f'终态{slab[s]}' for s in states], fontsize=9)
            ax.set_yticklabels([f'初态{slab[s]}' for s in states], fontsize=9)
            dsn = '混合400' if ds == 'mixed400' else '数字400'
            ax.set_title(f'{dsn}　{arm}（共同初态，分母400）', fontsize=10)
            init_c = int(r['init_C']); wc = int(r['W_to_C']); cw = int(r['C_to_W'])
            ax.set_xlabel(f'错→对 {wc}　对→错 {cw}　初态正确 {init_c}', fontsize=9)
        # 第三列：共同 vs 独立初态（EVID）
        ax = axes[ri][2]
        rc = next(x for x in rows if x['dataset'] == ds and x['arm'] == 'EVID' and x['initial'] == '共同初态')
        rind = next(x for x in rows if x['dataset'] == ds and x['initial'].startswith('独立初态'))
        xx = np.arange(2); w = 0.35
        b1 = ax.bar(xx - w / 2, [int(rc['W_to_C']), int(rind['W_to_C'])], w, color=C_VOTE, label='错→对')
        b2 = ax.bar(xx + w / 2, [int(rc['C_to_W']), int(rind['C_to_W'])], w, color=C_EVID, label='对→错')
        for bars in (b1, b2):
            for b in bars:
                ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 0.3, str(int(b.get_height())), ha='center', fontsize=9)
        ax.set_xticks(xx)
        ax.set_xticklabels([f"共同初态\n({rc['init_C']}对起步)", f"独立初态EVID_INIT\n({rind['init_C']}对起步)"], fontsize=8.5)
        dsn = '混合400' if ds == 'mixed400' else '数字400'
        ax.set_title(f'{dsn}　EVID 两种起点', fontsize=10)
        ax.set_ylabel('题数'); ax.legend(fontsize=8.5)
        ax.spines[['top', 'right']].set_visible(False)
        ax.set_xlabel('分母均为400；独立初态行为终态配对差', fontsize=8)
    fig.suptitle('修复转移：共同初态（左、中）与独立初态（右）分开报告', fontsize=12)
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    save(fig, 'fig5_repair_transitions')

# ---------------- 补充图S1 初态阶梯双面板点图（原图6，2026-11-03 起移入补充材料） ----------------
def figS1():
    rows = read('figS1_ladder.csv')
    arms = ['COMMON', 'GENERIC2', 'EVID_INIT', 'EVID_FINAL']
    alab = ['COMMON\n单次读+契约', 'GENERIC2\n普通两阶段', 'EVID_INIT\n事实先行初态', 'EVID_FINAL\n端到端终态']
    fig, axes = plt.subplots(1, 2, figsize=(10.5, 4.6))
    for ax, ds, dsn in [(axes[0], 'mixed400', '混合400'), (axes[1], 'numeric400', '数字400')]:
        sub = [r for r in rows if r['dataset'] == ds and r['contract'] == 'primary']
        ys = [next(int(r['correct']) for r in sub if r['arm'] == a) for a in arms]
        ax.plot(range(4), ys, 'o', color=C_SSC, markersize=9, zorder=3)
        for i, y in enumerate(ys):
            ax.annotate(f'{y}/400', (i, y), textcoords='offset points',
                        xytext=(6 if i == 0 else 0, 10), ha='left' if i == 0 else 'center', fontsize=9.5)
        ax.set_xticks(range(4)); ax.set_xticklabels(alab, fontsize=8.5)
        ax.set_ylabel('正确题数（主评分，分母400）')
        ax.set_title(dsn); ax.set_ylim(min(ys) - 6, max(ys) + 8)
        ax.grid(axis='y', alpha=0.25); ax.spines[['top', 'right']].set_visible(False)
        mg = [next(int(r['correct']) for r in rows
                   if r['dataset'] == ds and r['contract'] == 'merged' and r['arm'] == a) for a in arms]
        ax.text(0.5, -0.40, '合并契约敏感性：' + '→'.join(str(v) for v in mg) + '（顺序不变）',
                transform=ax.transAxes, ha='center', fontsize=8.5, color='#555555')
    fig.suptitle('顺序比较阶梯：相邻两臂为分别运行的观测增量，非独立因果贡献', fontsize=11)
    fig.tight_layout(rect=[0, 0.06, 1, 0.94])
    save(fig, 'figS1_init_ladder')

# ---------------- 图6 单模块删改：相对 FULL 的准确率差（2026-11-03 新版） ----------------
ABL_LAB = {'NO_FACT_FIRST': '去事实先行\n（改两次读）', 'GENERIC_ACQ': '去定向动作\n（改通用观察）',
           'NO_ACQ': '去新增取证', 'NO_GATE': '去最终复核'}
ABL_COLOR = {'NO_FACT_FIRST': '#c44e52', 'GENERIC_ACQ': '#4c72b0',
             'NO_ACQ': '#55a868', 'NO_GATE': '#8172b2'}


def fig6_module_ablation():
    rows = read('fig6_module_ablation.csv')
    order = list(ABL_LAB)
    fig, axes = plt.subplots(1, 2, figsize=(10.6, 4.3), sharey=True)
    for ax, ds, dsn in [(axes[0], 'mixed400', '混合400'), (axes[1], 'numeric400', '数字400')]:
        sub = {r['ablation']: r for r in rows if r['dataset'] == ds}
        xs = np.arange(len(order))
        for i, ab in enumerate(order):
            r = sub[ab]
            y = float(r['net_pp']); lo = float(r['lo_pp']); hi = float(r['hi_pp'])
            ax.errorbar(i, y, yerr=[[y - lo], [hi - y]], fmt='o', color=ABL_COLOR[ab],
                        markersize=8, capsize=4, lw=1.6, zorder=3)
            ax.annotate(f"{int(r['net_items']):+d}题", (i, y), textcoords='offset points',
                        xytext=(0, 12 if y >= 0 else -16), ha='center', fontsize=9)
        ax.axhline(0, color='#333333', lw=1.0, ls='-', zorder=1)
        ax.set_xticks(xs); ax.set_xticklabels([ABL_LAB[a] for a in order], fontsize=8.5)
        ax.set_title(dsn); ax.grid(axis='y', alpha=0.25)
        ax.spines[['top', 'right']].set_visible(False)
        ax.set_ylim(-5.2, 2.6)
        ax.text(0.02, 0.04, '全部 8 项比较 Holm p = 1.00', transform=ax.transAxes,
                fontsize=8.5, color='#555555')
    axes[0].set_ylabel('相对完整 EVID36 的准确率差（百分点，分母400）')
    fig.suptitle('完整流程下的单模块删改：点估计与表级聚类 95% 区间（n=400/题集）', fontsize=11.5)
    fig.tight_layout(rect=[0, 0, 1, 0.94])
    save(fig, 'fig6_module_ablation')


if __name__ == '__main__':
    fig3(); fig4(); fig5(); fig6_module_ablation(); figS1()
    print('done')
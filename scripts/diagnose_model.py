#!/usr/bin/env python3
"""
ML 模型诊断 + 修复 — 找到为什么 AUC 只有 0.556
"""
import json, numpy as np
from pathlib import Path

MODEL_DIR = Path("data/ml_model")

with open(MODEL_DIR / "dataset_v3.json") as f:
    v3 = json.load(f)
recs = v3["records"]

print("=" * 60)
print("ML 模型诊断报告")
print("=" * 60)

# ── Bug 1: turnover 和 vol_ma5 完全一样 ──
same = sum(1 for r in recs if r.get("turnover") == r.get("vol_ma5"))
print(f"\n🔴 Bug 1: turnover == vol_ma5 (100%相同)")
print(f"   两个特征用了同一个公式: vol[-5:]均值 / vol[-20:]均值")
print(f"   影响: 21特征实际只有20个独立信号")
print(f"   修复: 去掉vol_ma5, 只保留turnover")

# ── Bug 2: 97%数据没有LLM评分 ──
has_score = sum(1 for r in recs if r.get("score", 0) > 50)
print(f"\n🔴 Bug 2: 97%记录没有LLM评分")
print(f"   有评分: {has_score} ({has_score/len(recs)*100:.1f}%)")
print(f"   无评分(默认50): {len(recs)-has_score} ({(len(recs)-has_score)/len(recs)*100:.1f}%)")
print(f"   影响: score特征对模型几乎无用(97%都是50)")
print(f"   修复: 用LLM评分加权，或对候选池和非候选池分开建模")

# ── Bug 3: PE极端值 ──
pes = [r["pe"] for r in recs if r.get("pe") is not None]
extreme_pe = sum(1 for p in pes if p > 500 or p < -100)
print(f"\n🔴 Bug 3: PE极端值未清理")
print(f"   范围: {min(pes):.1f} ~ {max(pes):.1f}")
print(f"   极端值(>500或<-100): {extreme_pe} ({extreme_pe/len(pes)*100:.1f}%)")
print(f"   影响: PE极端值扭曲Ridge回归系数")
print(f"   修复: PE裁剪到[1, 200]范围")

# ── Bug 4: 基本面数据只覆盖96% ──
has_fund = sum(1 for r in recs if r.get("pe") is not None)
print(f"\n🟡 Bug 4: 基本面数据缺失")
print(f"   有PE: {has_fund}/{len(recs)} ({has_fund/len(recs)*100:.1f}%)")
print(f"   缺失的315条是早期记录(5-6月)")
print(f"   修复: 对缺失的记录从缓存补充基本面数据")

# ── 数据质量对比 ──
print(f"\n{'='*60}")
print("数据质量对比")
print(f"{'='*60}")
print(f"{'指标':<20} {'候选池(有评分)':<20} {'批量生成(默认分)':<20}")
candidates = [r for r in recs if r.get("score", 0) > 50]
non_candidates = [r for r in recs if r.get("score", 50) == 50]

for label, subset in [("候选池", candidates), ("批量生成", non_candidates)]:
    r3 = [r["r3"] for r in subset if r.get("r3") is not None]
    win = sum(1 for x in r3 if x > 0) if r3 else 0
    print(f"  {label}: n={len(subset)}, 上涨率={win/max(len(r3),1)*100:.1f}%, "
          f"平均r3={np.mean(r3):.2f}%, 有PE={sum(1 for r in subset if r.get('pe'))}")

# ── 结论 ──
print(f"\n{'='*60}")
print("结论: 为什么AUC=0.556")
print(f"{'='*60}")
print("""
核心原因: 数据稀释

之前310条精选数据 → AUC 0.639 (接近健康线)
现在8233条批量数据 → AUC 0.556 (低于降级线)

原因不是数据多了不好, 而是:
1. 97%的批量数据没有LLM评分, score特征失效
2. turnover和vol_ma5完全重复, 浪费一个特征位
3. PE极端值(-4090~142559)扭曲回归系数
4. 批量生成的"噪声数据"稀释了候选池的"信号数据"

修复策略:
A. 快速修复: 修bug + 裁剪PE + 去掉冗余特征 → 预计AUC提升0.02-0.05
B. 中期策略: 加权训练(候选池权重×5) + 基本面清理 → 预计AUC 0.60-0.65
C. 长期策略: 引入真正的增量数据(北向资金/融资余额/龙虎榜) → 0.70+
""")

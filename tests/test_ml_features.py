#!/usr/bin/env python3
"""
P0-1 ML评分特征漏传修复测试

bug: review_agent 的 ML 特征映射只挑 8/20 个特征，其余 12 个在推理时恒为0。
     pb 是模型第1重要特征(imp=0.144)，而 pb=0 在训练集仅占4.2%属罕见值
     → 模型对高估值股票全部误判看跌（兆易 80分LLM → 42分ML → 误降级）
fix: 映射补 pb/pe（calculate_qlib_factors 已算出，只是没挑出来）

回测(6955样本, ground truth=实际r10)：
  拒绝率 48.7%→16.4%，区分度 +4.68→+7.12pct
  高PB子集预测 从-2.84%校正到+8.63%（实际+8.84%）

运行: python3 tests/test_ml_features.py
"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "agents"))
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.ml_scorer import predict_ml_score, FEATURE_KEYS
from market_agent import calculate_qlib_factors

PASS, FAIL = 0, 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ✅ {name}")
    else: FAIL += 1; print(f"  ❌ {name}  {detail}")

POOL = Path(__file__).parent.parent / "五池管理" / "边缘池.json"
zgy = next(s for s in json.load(open(POOL))["stocks"] if s.get("代码") == "603986")
zgy_live = dict(zgy)
zgy_live.update({"今日收盘": 367.39, "今日涨跌": "+1.18%", "换手率": 1.44,
                 "量比": 1.09, "成交量_手": 96773, "振幅": 1.86,
                 "市盈率_TTM": 32.72, "市净率": 6.5})

detail = calculate_qlib_factors(zgy_live).get("factor_details", {})

print("═══ ML-1: calculate_qlib_factors 输出 pb/pe ═══")
print(f"  factor_details['pb']     = {detail.get('pb')!r}")
print(f"  factor_details['pe_ttm'] = {detail.get('pe_ttm')!r}")
check("ML-1a factor_details 含 pb", "pb" in detail, "缺 pb")
check("ML-1b factor_details 含 pe_ttm", "pe_ttm" in detail, "缺 pe_ttm")

print()
print("═══ ML-2: 按 review_agent 修复后的映射构造 ml_factors ═══")
ml_factors = {
    "ma5_div": round((detail.get("factor_ma5", 1) - 1) * 100, 2),
    "ma10_div": round((detail.get("factor_ma10", 1) - 1) * 100, 2),
    "ret5": round(detail.get("factor_ret5", 0) * 100, 2),
    "ret20": round(detail.get("factor_ret20", 0) * 100, 2),
    "vol20": detail.get("factor_vol20", 0),
    "vol_ratio": detail.get("factor_turn", 1),
    "day_range": detail.get("day_range", 0),
    "ma20_pos": detail.get("ma20_pos", 0),
    "pb": detail.get("pb", 0),
    "pe": detail.get("pe_ttm", 0),
}
check("ML-2a pb=6.5 已传入", abs(ml_factors["pb"] - 6.5) < 0.01, f"实际={ml_factors['pb']}")
check("ML-2b pe=32.72 已传入", abs(ml_factors["pe"] - 32.72) < 0.01, f"实际={ml_factors['pe']}")

print()
print("═══ ML-3: 兆易 ML 评分（LLM 80 分）═══")
r = predict_ml_score(ml_factors, llm_score=80)
print(f"  ml_score    = {r['ml_score']}   win_prob = {r['win_prob']}")
print(f"  pred_return = {r['pred_return']}%")
print(f"  impression  = {r['feature_impression']}")
check("ML-3a 兆易 ml_score ≥ 45（跨过门控线）", r["ml_score"] >= 45,
      f"={r['ml_score']}（修复前=42）")
check("ML-3b 兆易不会触发ML降级", r["ml_score"] >= 45, "仍<45 会继续被误降级")

print()
print("═══ ML-4: 同批4只对照（确认整体受益）═══")
CASES = [
    ("兆易创新", "603986", {"市盈率_TTM": 32.72, "市净率": 6.5}, 80, 42),
    ("中际旭创", "300308", {"市盈率_TTM": 30.0, "市净率": 5.0}, 70, 44),
    ("国轩高科", "002074", {"市盈率_TTM": 25.0, "市净率": 3.0}, 68, 44),
    ("东山精密", "002384", {"市盈率_TTM": 28.0, "市净率": 4.0}, 65, 43),
]
for name, code, extra, llm, before in CASES:
    d = calculate_qlib_factors({**{"代码": code, "名称": name}, **extra}).get("factor_details", {})
    f = {
        "ma5_div": round((d.get("factor_ma5", 1) - 1) * 100, 2),
        "ma10_div": round((d.get("factor_ma10", 1) - 1) * 100, 2),
        "ret5": round(d.get("factor_ret5", 0) * 100, 2),
        "ret20": round(d.get("factor_ret20", 0) * 100, 2),
        "vol20": d.get("factor_vol20", 0),
        "vol_ratio": d.get("factor_turn", 1),
        "day_range": d.get("day_range", 0),
        "ma20_pos": d.get("ma20_pos", 0),
        "pb": d.get("pb", 0), "pe": d.get("pe_ttm", 0),
    }
    res = predict_ml_score(f, llm_score=llm)
    arrow = "✅" if res["ml_score"] >= 45 else "⚠️"
    print(f"  {arrow} {name}({code}) pb={f['pb']}: {before}→{res['ml_score']}分 "
          f"(pred_return {res['pred_return']:+.2f}%)")
check("ML-4 兆易 42→≥45", r["ml_score"] >= 45, f"={r['ml_score']}")

print()
print("═══ ML-5: 未提供 pb/pe 时安全回退（不崩溃）═══")
f_empty: dict = {k: 0 for k in FEATURE_KEYS}
f_empty.update({"ma5_div": 5.0, "ret20": 10.0, "vol20": 3.0})
r_empty = predict_ml_score(f_empty, llm_score=70)
check("ML-5a 缺pb/pe时不抛异常", isinstance(r_empty.get("ml_score"), int),
      f"={r_empty.get('ml_score')}")
check("ML-5b 缺pb/pe时仍返回有效分", 0 < r_empty["ml_score"] <= 95, f"={r_empty['ml_score']}")

print()
print(f"═══ 结果: {PASS} 通过 / {FAIL} 失败 ═══")
sys.exit(1 if FAIL else 0)

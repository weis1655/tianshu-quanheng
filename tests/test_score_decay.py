#!/usr/bin/env python3
"""
评分衰减累加 bug 修复测试

bug: 衰减从 stock['综合分']（已衰减分）起算 → 每次运行都再降，衰减速率翻倍
     80分标: days 9→76, 10→71, 11→65, 12→64  (≈1分/天，应为0.5分/天)
fix: 懒初始化 stock['入池原始分'] 基线 + stock['衰减后分'] 水位标记
     每次从基线起算; 综合分被外部写入(重新审查)时重置基线、本轮不衰减

运行: python3 tests/test_score_decay.py
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "agents"))
sys.path.insert(0, str(Path(__file__).parent.parent))

from pool_manager import PoolManager
from thresholds import SCORE_DECAY_DAYS, SCORE_DECAY_PER_DAY, SCORE_DECAY_MAX, SCORE_DECAY_FLOOR

D = PoolManager.apply_score_decay
PASS, FAIL = 0, 0

def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ✅ {name}")
    else: FAIL += 1; print(f"  ❌ {name}  {detail}")

def date_n_days_ago(n):
    """n天前日期字符串"""
    return (datetime.now() - timedelta(days=n)).strftime("%Y-%m-%d")

def expect(days, base=80, up_day=False):
    """按修复后语义算期望分：从基线起算"""
    decay = min(days * SCORE_DECAY_PER_DAY, SCORE_DECAY_MAX)
    if up_day: decay *= 0.5
    return max(round(base - decay), SCORE_DECAY_FLOOR)

print("═══ DC-1: 不累加（核心 bug）——每次从基线起算 ═══")
s = {"名称": "兆易创新", "代码": "603986", "综合分": 80, "纳入日期": "2026-09-01"}
D(s)
check("DC-1a 首次衰减生效", s["综合分"] < 80, f"衰减后={s['综合分']}")
check("DC-1b 基线建立=80", s["入池原始分"] == 80, f"实际={s.get('入池原始分')}")
s2_score = s["综合分"]
D(s)
check("DC-1c 同天重复调用不累加", s["综合分"] == s2_score, f"再次调用后={s['综合分']}（未变）")
check("DC-1d 基线始终为80", s["入池原始分"] == 80)

print()
print("═══ DC-2: 反向验证 —— 修复前逻辑确实累加（证明用例有效）═══")
def old_decay(stock):
    orig = stock.get("综合分", 0)
    entry = stock.get("纳入日期", "")
    if orig > 0 and entry:
        try:
            days = (datetime.now() - datetime.strptime(entry, "%Y-%m-%d")).days
        except ValueError:
            days = 999
        if days > SCORE_DECAY_DAYS:
            stock["综合分"] = max(round(orig - min(days * SCORE_DECAY_PER_DAY, SCORE_DECAY_MAX)), SCORE_DECAY_FLOOR)
old = {"综合分": 80, "纳入日期": date_n_days_ago(9)}
for _ in range(3):
    old_decay(old)
check("DC-2a 旧逻辑3次后显著更低", old["综合分"] <= 70, f"旧逻辑={old['综合分']}")
new = {"综合分": 80, "纳入日期": date_n_days_ago(9)}
for _ in range(3):
    D(new)
check("DC-2b 新逻辑3次后不累加", new["综合分"] > old["综合分"],
      f"旧={old['综合分']} 新={new['综合分']}")
want9 = expect(9)
check("DC-2c 新逻辑=从基线算", new["综合分"] == want9, f"新={new['综合分']} 期望={want9}")

print()
print("═══ DC-3: 边界条件 ═══")
s0 = {"代码": "600000", "综合分": 80, "纳入日期": date_n_days_ago(5)}
check("DC-3a 未超期(5天)不衰减", D(s0) is False and s0["综合分"] == 80, f"={s0['综合分']}")
check("DC-3b 未超期不建基线", "入池原始分" not in s0)

s1 = {"代码": "600001", "综合分": 80, "纳入日期": ""}
check("DC-3c 无纳入日期不衰减", D(s1) is False and s1["综合分"] == 80)

s2 = {"代码": "600002", "综合分": 0, "纳入日期": date_n_days_ago(10)}
check("DC-3d 综合分0不衰减", D(s2) is False and s2["综合分"] == 0)

s3 = {"代码": "600003", "综合分": 45, "纳入日期": date_n_days_ago(40)}
D(s3)
check("DC-3e 下限40分", s3["综合分"] == SCORE_DECAY_FLOOR, f"={s3['综合分']}")

s4 = {"代码": "600004", "综合分": 80, "纳入日期": date_n_days_ago(20), "今日涨跌": "+3.2%"}
D(s4)
check("DC-3f 涨日衰减减半", s4["综合分"] == expect(20, up_day=True),
      f"={s4['综合分']} 期望={expect(20, up_day=True)}")

s5 = {"代码": "600005", "综合分": 80, "纳入日期": "bad-date"}
D(s5)
check("DC-3g 日期格式异常→999天→触顶15", s5["综合分"] == expect(999),
      f"={s5['综合分']} 期望={expect(999)}")

print()
print("═══ DC-4: 重新审查上调评分 → 基线重置，本轮不衰减 ═══")
s6 = {"名称": "上调", "代码": "600006", "综合分": 80, "纳入日期": date_n_days_ago(20)}
D(s6)
check("DC-4a 先衰减生效", s6["综合分"] < 80, f"衰减后={s6['综合分']}")
s6["综合分"] = 95
D(s6)
check("DC-4b 上调后基线重置为95", s6["入池原始分"] == 95, f"={s6.get('入池原始分')}")
check("DC-4c 上调当轮不衰减", s6["综合分"] == 95,
      f"={s6['综合分']}（不能变成95-10=85，也不能用旧基线75-15=60）")
s6["衰减后分"] = 95
D(s6)
check("DC-4d 水位匹配后从新基线95衰减", s6["综合分"] == expect(20, base=95),
      f"={s6['综合分']} 期望={expect(20, base=95)}")

print()
print("═══ DC-5: 重新审查下调评分 → 基线同步下调 ═══")
s7 = {"代码": "600007", "综合分": 80, "纳入日期": date_n_days_ago(15)}
D(s7)
s7["综合分"] = 70
D(s7)
check("DC-5a 基线重置为70", s7["入池原始分"] == 70, f"={s7.get('入池原始分')}")
check("DC-5b 不用旧基线80覆盖新分70", s7["综合分"] == 70, f"={s7['综合分']}")

print()
print("═══ DC-6: 水位连续性 ═══")
s8 = {"代码": "600008", "综合分": 80, "纳入日期": date_n_days_ago(10)}
D(s8)
b1 = s8["入池原始分"]
D(s8)
check("DC-6a 水位匹配时基线不重置", s8["入池原始分"] == b1 == 80, f"基线={s8['入池原始分']}")
check("DC-6b 持续从基线衰减", s8["综合分"] == expect(10), f"={s8['综合分']} 期望={expect(10)}")

print()
print("═══ DC-7: 日志痕迹 ═══")
s9 = {"名称": "Z", "代码": "600009", "综合分": 90, "纳入日期": date_n_days_ago(10)}
D(s9)
trace = s9.get("评分最后更新", "")
check("DC-7a 有衰减痕迹", "→" in trace and "入池" in trace, f"='{trace}'")
check("DC-7b 痕迹从基线90起算", trace.startswith("90→"), f"='{trace}'")
s9["衰减后分"] = s9["综合分"]
D(s9)
check("DC-7c 第二次痕迹仍从基线90", s9.get("评分最后更新", "").startswith("90→"),
      f"='{s9.get('评分最后更新')}'")

print()
print("═══ DC-8: 返回值 ═══")
sA = {"代码": "600010", "综合分": 80, "纳入日期": date_n_days_ago(12)}
check("DC-8a 发生衰减返回True", D(sA) is True)
sB = {"代码": "600011", "综合分": 80, "纳入日期": date_n_days_ago(5)}
check("DC-8b 未超期返回False", D(sB) is False)

print()
print(f"═══ 结果: {PASS} 通过 / {FAIL} 失败 ═══")
sys.exit(1 if FAIL else 0)

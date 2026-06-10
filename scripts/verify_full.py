#!/usr/bin/env python3
"""验证8笔亏损标的 + 补充行情"""
import json, re, time, urllib.request
from datetime import datetime
from pathlib import Path
from collections import defaultdict

BASE = Path(__file__).parent.parent
HISTORY = BASE / "data" / "历史记录"

def get_prefix(code):
    c = str(code).strip()
    if c.startswith('6'): return 'sh'
    if c.startswith(('0','3')): return 'sz'
    if c.startswith(('8','4')): return 'bj'
    return 'sh'

def fetch_prices(code, num_days=50):
    prefix = get_prefix(code)
    urls = [
        f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={prefix}{code}&scale=240&ma=no&datalen={num_days}",
        f"http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={prefix}{code}&scale=240&ma=no&datalen={num_days}"
    ]
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"Referer":"https://finance.sina.com.cn/","User-Agent":"Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read().decode("utf-8","replace")
            data = json.loads(raw)
            return {d["day"]: float(d["close"]) for d in data if "day" in d and "close" in d}
        except Exception:
            continue
    return {}

# 8笔亏损标的 + 蓝筹抽样
stocks_to_check = [
    # 8笔亏损标的（已知买入日期）
    ("601899", "紫金矿业", "2026-06-02"),
    ("600489", "中金黄金", "2026-06-03"),
    ("002156", "通富微电", "2026-06-03"),
    ("300604", "长川科技", "2026-06-03"),
    ("600406", "国电南瑞", "2026-06-03"),
    ("000977", "浪潮信息", "2026-06-03"),
    ("600900", "长江电力", "2026-06-03"),
    ("688676", "金盘科技", "2026-06-04"),
    # 抽样级：4/25、5/6主推
    ("300342", "天银机电", "2026-04-25"),
    ("600118", "中国卫星", "2026-04-25"),
    ("300223", "北京君正", "2026-05-20"),
    ("601975", "招商南油", "2026-05-21"),
]

print(" 📊 天枢权衡全量行情验证\n")
print(f"{'股票':<12} {'代码':<8} {'决策日':<12} {'3日涨跌':<10} {'5日涨跌':<10} {'10日涨跌':<10} {'当前价':<10}")
print("-"*72)

all_picks = []
for code, name, entry_date in stocks_to_check:
    prices = fetch_prices(code)
    time.sleep(0.3)
    if not prices:
        print(f"  {name:<10} {code:<8} {entry_date:<12} ⚠️ 行情获取失败")
        continue

    sorted_dates = sorted(prices.keys())

    def calc_ret(hold):
        idx = None
        for i, d in enumerate(sorted_dates):
            if d >= entry_date:
                idx = i
                break
        if idx is None or idx + hold >= len(sorted_dates):
            return None
        entry = prices[sorted_dates[idx]]
        exit_p = prices[sorted_dates[idx + hold]]
        if entry and entry > 0:
            return round((exit_p - entry) / entry * 100, 2)
        return None

    r3 = calc_ret(3)
    r5 = calc_ret(5)
    r10 = calc_ret(10)
    cur = prices[sorted_dates[-1]]

    r3_s = f"{r3:+.2f}%" if r3 else "N/A"
    r5_s = f"{r5:+.2f}%" if r5 else "N/A"
    r10_s = f"{r10:+.2f}%" if r10 else "N/A"
    mark = "🟢" if (r3 or 0) > 0 else "🔴"
    print(f"  {mark} {name:<10} {code:<8} {entry_date:<12} {r3_s:<10} {r5_s:<10} {r10_s:<10} {cur:.2f}")

    all_picks.append({
        "code": code, "name": name, "entry_date": entry_date,
        "r3": r3, "r5": r5, "r10": r10,
        "is_profit_3d": (r3 or 0) > 0,
        "is_profit_5d": (r5 or 0) > 0,
    })

# 统计
print()
print("="*72)
print(" 📈 全量准确率统计\n")

profit_3d = sum(1 for p in all_picks if p["is_profit_3d"] and p["r3"] is not None)
total_3d = sum(1 for p in all_picks if p["r3"] is not None)
profit_5d = sum(1 for p in all_picks if p["is_profit_5d"] and p["r5"] is not None)
total_5d = sum(1 for p in all_picks if p["r5"] is not None)

r3_values = [p["r3"] for p in all_picks if p["r3"] is not None]
r5_values = [p["r5"] for p in all_picks if p["r5"] is not None]
avg_r3 = sum(r3_values)/len(r3_values) if r3_values else 0
avg_r5 = sum(r5_values)/len(r5_values) if r5_values else 0

print(f"  3日持有: 盈利 {profit_3d}/{total_3d} ({profit_3d/total_3d*100:.1f}%)  平均收益: {avg_r3:+.2f}%")
print(f"  5日持有: 盈利 {profit_5d}/{total_5d} ({profit_5d/total_5d*100:.1f}%)  平均收益: {avg_r5:+.2f}%")
worst = min(all_picks, key=lambda p: p["r3"] or 0)
best = max(all_picks, key=lambda p: p["r3"] or 0)
print(f"  最差: {worst['name']}({worst['code']}) {worst['r3']:+.2f}%")
print(f"  最佳: {best['name']}({best['code']}) {best['r3']:+.2f}%")

# 按决策日期统计
print()
print(" 📅 按决策日统计\n")
from collections import defaultdict
by_date = defaultdict(list)
for p in all_picks:
    by_date[p["entry_date"]].append(p)
for date in sorted(by_date.keys()):
    picks = by_date[date]
    profits = [p for p in picks if p["is_profit_3d"]]
    avg_r = sum(p["r3"] for p in picks if p["r3"] is not None) / max(1, sum(1 for p in picks if p["r3"] is not None))
    print(f"  {date}: {len(picks)}只, 盈利{len(profits)}只, 平均{avg_r:+.2f}%")

# 保存JSON
out = {"generated_at": datetime.now().isoformat(), "stocks": all_picks, "summary": {
    "total": total_3d, "profit_3d": profit_3d, "avg_r3": round(avg_r3,2), "winrate_3d": round(profit_3d/total_3d*100,1) if total_3d else 0,
    "profit_5d": profit_5d, "avg_r5": round(avg_r5,2), "winrate_5d": round(profit_5d/total_5d*100,1) if total_5d else 0
}}
(BASE / "data" / "full_verify.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n✅ 全量验证完成 → data/full_verify.json")

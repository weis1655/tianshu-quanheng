#!/usr/bin/env python3
"""
重点观察池全量验证
从重点池+历史池提取所有曾入池的股票，验证纳入日→当前的涨跌幅
"""
import json, re, time, urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
POOL_DIR = ROOT / "五池管理"

def get_prefix(code):
    c = str(code).strip()
    if c.startswith('6'): return 'sh'
    if c.startswith(('0','3')): return 'sz'
    if c.startswith(('8','4')): return 'bj'
    return 'sh'

def fetch_prices(code, num_days=60):
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

def safe_read_json(path):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}

def calc_return(prices, entry_date, hold=3):
    sorted_dates = sorted(prices.keys())
    entry_idx = None
    for i, d in enumerate(sorted_dates):
        if d >= entry_date:
            entry_idx = i
            break
    if entry_idx is None or entry_idx + hold >= len(sorted_dates):
        return None
    entry = prices[sorted_dates[entry_idx]]
    exit_p = prices[sorted_dates[entry_idx + hold]]
    if entry and entry > 0:
        return round((exit_p - entry) / entry * 100, 2)
    return None

def get_current_price(prices):
    if not prices:
        return 0
    return prices[sorted(prices.keys())[-1]]

# 读取重点池 + 历史池
pool_data = safe_read_json(POOL_DIR / "重点观察池.json")
history_data = safe_read_json(POOL_DIR / "重点观察池_历史池.json")

# 提取所有曾入池的股票（含当前池）
all_stocks = []
for s in pool_data.get("stocks", []):
    code = str(s.get("代码",""))
    name = str(s.get("名称",""))
    entry_date = str(s.get("纳入日期",""))
    score = s.get("综合分", 0)
    logic = s.get("核心逻辑", "")[:40]
    if code:
        all_stocks.append({"code": code, "name": name, "entry_date": entry_date, "score": score, "logic": logic})

for s in history_data.get("stocks", []):
    code = str(s.get("代码",""))
    name = str(s.get("名称",""))
    entry_date = str(s.get("纳入日期",""))
    score = s.get("综合分", 0)
    logic = s.get("核心逻辑", "")[:40]
    if code and entry_date:
        all_stocks.append({"code": code, "name": name, "entry_date": entry_date, "score": score, "logic": logic})

# 去重：保留最后一次入池记录
seen = {}
for s in all_stocks:
    seen[s["code"]] = s  # 后来的覆盖前面的
all_stocks = list(seen.values())
print(f"📂 重点观察池历史合计: {len(all_stocks)} 只股票\n")

print(f"{'股票':<10} {'代码':<8} {'入池日':<12} {'评分':<5} {'3日涨跌':<10} {'5日涨跌':<10} {'10日涨跌':<10} {'现价':<8} {'核心逻辑':<30}")
print("-"*108)

results = []
for i, s in enumerate(all_stocks):
    prices = fetch_prices(s["code"])
    time.sleep(0.25)

    r3 = calc_return(prices, s["entry_date"], 3)
    r5 = calc_return(prices, s["entry_date"], 5)
    r10 = calc_return(prices, s["entry_date"], 10)
    cur = get_current_price(prices)

    r3_s = f"{r3:+.2f}%" if r3 else "N/A"
    r5_s = f"{r5:+.2f}%" if r5 else "N/A"
    r10_s = f"{r10:+.2f}%" if r10 else "N/A"
    mark = "🟢" if (r3 or 0) > 0 else "🔴"
    print(f"  {mark} {s['name']:<8} {s['code']:<8} {s['entry_date']:<12} {s['score']:<5} {r3_s:<10} {r5_s:<10} {r10_s:<10} {cur:<8.2f} {s['logic']:<30}")

    results.append({**s, "r3": r3, "r5": r5, "r10": r10, "cur_price": cur})

# 统计
print()
print("="*108)
print(" 📊 重点观察池全量统计\n")

valid = [r for r in results if r["r3"] is not None]
profit = sum(1 for r in valid if r["r3"] > 0)
avg_r3 = sum(r["r3"] for r in valid) / len(valid) if valid else 0

valid5 = [r for r in results if r["r5"] is not None]
profit5 = sum(1 for r in valid5 if r["r5"] > 0)
avg_r5 = sum(r["r5"] for r in valid5) / len(valid5) if valid5 else 0

print(f"  总数: {len(all_stocks)} 只")
print(f"  有行情数据: {len(valid)} 只")
print(f"\n  3日持有: 盈利 {profit}/{len(valid)} ({profit/len(valid)*100:.1f}%)  平均: {avg_r3:+.2f}%")
print(f"  5日持有: 盈利 {profit5}/{len(valid5)} ({profit5/len(valid5)*100:.1f}%)  平均: {avg_r5:+.2f}%")

worst = min(valid, key=lambda r: r["r3"] or 0)
best = max(valid, key=lambda r: r["r3"] or 0)
print(f"\n  🏆 最佳: {best['name']}({best['code']}) {best['r3']:+}% (入池{best['entry_date']}, 评分{best['score']})")
print(f"  💀 最差: {worst['name']}({worst['code']}) {worst['r3']:+}% (入池{worst['entry_date']}, 评分{worst['score']})")

# 按评分区间统计
print(f"\n  📈 按评分区间:")
for s_min, s_max, label in [(0, 64, "≤64分"), (65, 74, "65-74分"), (75, 84, "75-84分"), (85, 100, "85+分")]:
    group = [r for r in valid if s_min <= r["score"] <= s_max]
    if group:
        g_profit = sum(1 for r in group if r["r3"] > 0)
        g_avg = sum(r["r3"] for r in group) / len(group)
        print(f"    {label:<10} {len(group):3d}只, 盈利{g_profit:2d}只, 胜率{g_profit/len(group)*100:5.1f}%, 平均{g_avg:+.2f}%")

# 保存
out = {"generated_at": datetime.now().isoformat(), "total": len(all_stocks), "stocks": results,
       "summary": {"total": len(valid), "profit_3d": profit, "winrate_3d": round(profit/len(valid)*100,1) if valid else 0,
                   "avg_r3": round(avg_r3,2), "profit_5d": profit5, "winrate_5d": round(profit5/len(valid5)*100,1) if valid5 else 0}}
(ROOT / "data" / "key_watch_pool_verify.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n✅ 验证完成 → data/key_watch_pool_verify.json")

#!/usr/bin/env python3
"""快筛候选池全量验证 — 验证快筛池每只股票入池后的3日/5日/10日/到今日表现"""
import json, time, urllib.request
from datetime import datetime
from pathlib import Path
from collections import defaultdict

BASE = Path(__file__).parent.parent

# 读取快筛候选池
pool_data = json.loads((BASE / "五池管理" / "快筛候选池.json").read_text(encoding="utf-8"))
history = pool_data.get("_fast_screen_history", {})

print(f"📊 快筛候选池全量验证")
print(f"   历史记录: {len(history)} 只股票")
print(f"   当前池:   {len(pool_data.get('stocks', []))} 只")

# 行情工具
CACHE = {}
def get_prefix(code):
    c = str(code).strip()
    if c.startswith('6'): return 'sh'
    if c.startswith(('0','3')): return 'sz'
    if c.startswith(('8','4')): return 'bj'
    return 'sh'

def fetch_prices(code, num_days=60):
    if code in CACHE:
        return CACHE[code]
    prefix = get_prefix(code)
    urls = [
        f"http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={prefix}{code}&scale=240&ma=no&datalen={num_days}",
    ]
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"Referer":"https://finance.sina.com.cn/","User-Agent":"Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read().decode("utf-8","replace")
            data = json.loads(raw)
            prices = {d["day"]: float(d["close"]) for d in data if "day" in d and "close" in d}
            CACHE[code] = prices
            return prices
        except Exception:
            continue
    CACHE[code] = {}
    return {}

# 按入池日期统计
by_date = defaultdict(list)
for code, entry_date in history.items():
    by_date[entry_date].append(code)

# 只验证有足够历史数据的（入池超过3个交易日）
today = datetime.now().strftime("%Y-%m-%d")
results = []
errors = []
total = len(history)

print(f"\n⏳ 开始行情获取 ({total} 只, 每次间隔0.3秒)...")
estimate_seconds = total * 0.3 / 60
print(f"   预估耗时: {estimate_seconds:.1f} 分钟")

count = 0
for entry_date in sorted(by_date.keys()):
    for code in by_date[entry_date]:
        count += 1
        if count % 20 == 0:
            print(f"   进度: {count}/{total}")
        prices = fetch_prices(code, num_days=60)
        time.sleep(0.3)
        if not prices:
            errors.append({"code": code, "entry_date": entry_date, "error": "行情获取失败"})
            continue
        sorted_dates = sorted(prices.keys())
        
        # 找入池日期的索引
        entry_idx = None
        for i, d in enumerate(sorted_dates):
            if d >= entry_date:
                entry_idx = i
                break
        
        if entry_idx is None:
            errors.append({"code": code, "entry_date": entry_date, "error": "入池日期不在行情范围"})
            continue
        
        entry_price = prices[sorted_dates[entry_idx]]
        if not entry_price or entry_price <= 0:
            errors.append({"code": code, "entry_date": entry_date, "error": "入池价无效"})
            continue
        
        # 计算各持有期收益
        def calc_ret(hold):
            if entry_idx + hold < len(sorted_dates):
                exit_p = prices[sorted_dates[entry_idx + hold]]
                return round((exit_p - entry_price) / entry_price * 100, 2)
            return None
        
        cur_price = prices[sorted_dates[-1]]
        cur_ret = round((cur_price - entry_price) / entry_price * 100, 2)
        
        results.append({
            "code": code,
            "entry_date": entry_date,
            "entry_price": round(entry_price, 2),
            "current_price": round(cur_price, 2),
            "current_return": cur_ret,
            "r3": calc_ret(3),
            "r5": calc_ret(5),
            "r10": calc_ret(10),
            "days_since_entry": (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(entry_date, "%Y-%m-%d")).days,
        })

print(f"\n✅ 验证完成: {len(results)} 只成功, {len(errors)} 只失败")

# 统计分析
r3_values = [r["r3"] for r in results if r["r3"] is not None]
r5_values = [r["r5"] for r in results if r["r5"] is not None]
cur_values = [r["current_return"] for r in results]

print("\n" + "="*70)
print(" 📈 快筛池全量统计")
print("="*70)

def stats(vals, label):
    if not vals:
        return
    positive = sum(1 for v in vals if v > 0)
    negative = sum(1 for v in vals if v <= 0)
    avg = sum(vals) / len(vals)
    best = max(vals)
    worst = min(vals)
    print(f"\n  {label}:")
    print(f"    总数: {len(vals)}")
    print(f"    盈利: {positive} ({positive/len(vals)*100:.1f}%)")
    print(f"    亏损: {negative} ({negative/len(vals)*100:.1f}%)")
    print(f"    平均: {avg:+.2f}%")
    print(f"    最好: {best:+.2f}%")
    print(f"    最差: {worst:+.2f}%")

stats(r3_values, "3日持有收益")
stats(r5_values, "5日持有收益")
stats(cur_values, "当前收益(从入池至今)")

# 按入池分数区间分析
print("\n" + "="*70)
print(" 📈 入池日期分布")
print("="*70)
dates = [r["entry_date"] for r in results]
date_counts = defaultdict(int)
for d in dates:
    date_counts[d] += 1
print(f"  最早入池: {min(dates)}")
print(f"  最晚入池: {max(dates)}")
print(f"  覆盖交易日: {len(date_counts)} 天")

# 按月份统计
monthly = defaultdict(list)
for r in results:
    month = r["entry_date"][:7]
    r3 = r["r3"]
    if r3 is not None:
        monthly[month].append(r3)
print(f"\n  📅 月度统计:")
for month in sorted(monthly.keys()):
    vals = monthly[month]
    avg = sum(vals)/len(vals)
    positive = sum(1 for v in vals if v > 0)
    print(f"    {month}: {len(vals)}只, 盈利{positive}({positive/len(vals)*100:.0f}%), 平均{avg:+.2f}%")

# 最差/最好的股票
print("\n" + "="*70)
print(" 🏆 最佳/最差 TOP5 (按入池后至今收益)")
print("="*70)
by_cur = sorted(results, key=lambda r: r["current_return"], reverse=True)
print("\n  最佳5只:")
for r in by_cur[:5]:
    print(f"    ✅ {r['code']} 入池{r['entry_date']}价{r['entry_price']}→今{r['current_price']} 收益{r['current_return']:+.2f}%")
print("\n  最差5只:")
for r in by_cur[-5:]:
    print(f"    ❌ {r['code']} 入池{r['entry_date']}价{r['entry_price']}→今{r['current_price']} 收益{r['current_return']:+.2f}%")

# 保存
out = {"generated_at": datetime.now().isoformat(), "total": len(results), "errors": len(errors), "stocks": results,
       "summary": {
           "r3": {"count": len(r3_values), "winrate": round(sum(1 for v in r3_values if v>0)/len(r3_values)*100,1) if r3_values else 0, "avg": round(sum(r3_values)/len(r3_values),2) if r3_values else 0},
           "r5": {"count": len(r5_values), "winrate": round(sum(1 for v in r5_values if v>0)/len(r5_values)*100,1) if r5_values else 0, "avg": round(sum(r5_values)/len(r5_values),2) if r5_values else 0},
           "current": {"count": len(cur_values), "winrate": round(sum(1 for v in cur_values if v>0)/len(cur_values)*100,1) if cur_values else 0, "avg": round(sum(cur_values)/len(cur_values),2) if cur_values else 0},
       }}
(BASE / "data" / "screen_pool_verify.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n✅ 结果已保存: data/screen_pool_verify.json")
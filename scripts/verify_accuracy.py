#!/usr/bin/env python3
"""
天枢权衡准确性验证脚本 — 分层抽样验证
P0级(6/2-6/4 8笔亏损) + 抽样级(4/25, 5/6, 5/20, 5/21)
"""
import json, re, time, urllib.request
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).parent.parent
HISTORY = BASE / "data" / "历史记录"

# ── 行情工具 ───────────────────────────────────────
def get_prefix(code):
    c = str(code).strip()
    if c.startswith('6'): return 'sh'
    if c.startswith(('0','3')): return 'sz'
    if c.startswith(('8','4')): return 'bj'
    return 'sh'

def fetch_prices(code, num_days=40):
    """从新浪财经获取日K线"""
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

def calc_return(prices, decision_date, hold=3):
    """计算决策日后hold个交易日的涨跌幅"""
    sorted_dates = sorted(prices.keys())
    entry_idx = None
    for i, d in enumerate(sorted_dates):
        if d >= decision_date:
            entry_idx = i
            break
    if entry_idx is None or entry_idx + hold >= len(sorted_dates):
        return None
    entry = prices[sorted_dates[entry_idx]]
    exit_p = prices[sorted_dates[entry_idx + hold]]
    if entry and entry > 0:
        chg = round((exit_p - entry) / entry * 100, 2)
        return {
            "buy_price": round(entry,2), "sell_price": round(exit_p,2),
            "change_pct": chg, "hold_days": hold,
            "entry_date": sorted_dates[entry_idx],
            "exit_date": sorted_dates[entry_idx + hold],
            "is_profit": chg > 0
        }
    return None

# ── 决策报告解析 ──────────────────────────────────
def parse_decision(filepath):
    """提取决策报告中的主推标的"""
    content = Path(filepath).read_text(encoding="utf-8")
    date_match = re.search(r'(\d{4}-\d{2}-\d{2})', filepath.name)
    date = date_match.group(1) if date_match else ""

    stocks = []
    is_empty = bool(re.search(r'(空仓|暂无|空仓等待)', content))

    # 提取【主推】标的
    for m in re.finditer(r'【主推】\s*([\u4e00-\u9fa5]{2,6})\s*[（(](\d{6})[）)]', content):
        stocks.append({"type": "主推", "name": m.group(1), "code": m.group(2)})

    # 提取【观察】标的（我们新加的格式）
    for m in re.finditer(r'【观察】\s*([\u4e00-\u9fa5]{2,6})\s*[（(](\d{6})[）)]', content):
        stocks.append({"type": "观察", "name": m.group(1), "code": m.group(2)})

    # 提取降级操作
    for m in re.finditer(r'【降级操作】\s*([\u4e00-\u9fa5]{2,6})\s*[（(](\d{6})[）)]', content):
        stocks.append({"type": "降级操作", "name": m.group(1), "code": m.group(2)})

    # 提取备选观察
    for m in re.finditer(r'【备选\s*观察】\s*([\u4e00-\u9fa5]{2,6})\s*[（(](\d{6})[）)]', content):
        stocks.append({"type": "备选观察", "name": m.group(1), "code": m.group(2)})

    # 兜底：没有【主推】标记时，匹配兜底模板格式
    if not stocks and not is_empty:
        for m in re.finditer(r'###\s*【[^】]*】\s*([\u4e00-\u9fa5]{2,6})\s*[（(](\d{6})[）)]', content):
            stocks.append({"type": "主推", "name": m.group(1), "code": m.group(2)})

    # 提取评分
    scores = {}
    for m in re.finditer(r'评分(\d+)分|评分[：:](\d+)', content):
        s = int(m.group(1) or m.group(2))
        if 40 <= s <= 100:
            scores["review"] = s
    for m in re.finditer(r'综合评分[：:]?\s*(\d+)', content):
        s = int(m.group(1))
        if 40 <= s <= 100:
            scores["composite"] = s

    return {"date": date, "is_empty": is_empty, "stocks": stocks, "scores": scores}

# ── 主流程 ─────────────────────────────────────────
def main():
    # P0级：6/2-6/4 8笔亏损
    p0_dates = ["2026-06-02", "2026-06-03", "2026-06-04"]
    # 抽样级：4/25, 5/6, 5/20, 5/21
    sample_dates = ["2026-04-25", "2026-05-06", "2026-05-20", "2026-05-21"]

    results = {"p0": [], "sample": [], "errors": []}

    for date in p0_dates + sample_dates:
        fp = HISTORY / f"{date}_决策报告.md"
        if not fp.exists():
            results["errors"].append(f"决策报告不存在: {date}")
            continue

        decision = parse_decision(fp)
        if decision["is_empty"]:
            decision["verification"] = "空仓"
            results["p0" if date in p0_dates else "sample"].append(decision)
            continue

        for s in decision["stocks"]:
            prices = fetch_prices(s["code"])
            if not prices:
                results["errors"].append(f"行情失败: {s['name']}({s['code']}) {date}")
                s["verification"] = "行情获取失败"
                continue
            time.sleep(0.3)  # 保护新浪API

            r3 = calc_return(prices, date, hold=3)
            r5 = calc_return(prices, date, hold=5)
            r10 = calc_return(prices, date, hold=10)

            s["verification"] = {
                "3日持有": r3,
                "5日持有": r5,
                "10日持有": r10,
            }
            print(f"[{date}] {s['name']}({s['code']}) 3日:{r3['change_pct'] if r3 else 'N/A'}%  5日:{r5['change_pct'] if r5 else 'N/A'}%")

        results["p0" if date in p0_dates else "sample"].append(decision)

    # 保存结果
    out = {"generated_at": datetime.now().isoformat(), "results": results}
    out_path = BASE / "data" / "verify_accuracy_result.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✅ 验证完成，结果已保存: {out_path}")

    # 输出汇总
    print("\n" + "="*60)
    print(" 📊 天枢权衡准确性验证报告")
    print("="*60)
    for tier in ["p0", "sample"]:
        label = "🔴 P0级: 8笔亏损复盘" if tier == "p0" else "🟡 抽样级: 4天验证"
        print(f"\n--- {label} ---")
        for day in results[tier]:
            if day.get("verification") == "空仓":
                print(f"  {day['date']} 📭 空仓")
                continue
            for s in day.get("stocks", []):
                v = s.get("verification", {})
                if isinstance(v, dict) and "3日持有" in v:
                    r3 = v["3日持有"]
                    r5 = v["5日持有"]
                    if r3:
                        r5_str = f"{r5['change_pct']:+.2f}%" if r5 else "N/A"
                        mark = "✅" if r3["is_profit"] else "❌"
                        print(f"  {day['date']} {mark} {s['name']}({s['code']}) [{s['type']}] "
                              f"3日:{r3['change_pct']:+.2f}%  5日:{r5_str}")
                    else:
                        print(f"  {day['date']} ⚠️ {s['name']}({s['code']}) 行情不足")
                elif isinstance(v, str):
                    print(f"  {day['date']} ⚠️ {s['name']}({s['code']}) 行情获取失败")

    # 统计准确率
    print("\n--- 📈 准确率统计 ---")
    total_picks = 0
    profitable = 0
    for tier in ["p0", "sample"]:
        for day in results[tier]:
            for s in day.get("stocks", []):
                v = s.get("verification", {})
                if isinstance(v, dict) and "3日持有" in v and v["3日持有"]:
                    total_picks += 1
                    if v["3日持有"]["is_profit"]:
                        profitable += 1
    if total_picks > 0:
        print(f"总推荐: {total_picks} 只")
        print(f"盈利: {profitable} 只 ({profitable/total_picks*100:.1f}%)")
        print(f"亏损: {total_picks-profitable} 只 ({(total_picks-profitable)/total_picks*100:.1f}%)")
        print(f"综合胜率: {profitable/total_picks*100:.1f}%")

if __name__ == "__main__":
    main()

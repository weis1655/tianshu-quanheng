#!/usr/bin/env python3
"""
ML评分模型 v2 — 全量历史数据提取
修复: 决策报告表格格式 + K线日期排序 + 收益率计算
"""
import json, re, time, sys, random
from pathlib import Path
import urllib.request
import urllib.error

BASE = Path(__file__).parent.parent
HISTORY = BASE / "data" / "历史记录"
OUT_DIR = BASE / "data" / "ml_model"
OUT_DIR.mkdir(parents=True, exist_ok=True)

PRICE_CACHE = {}
_LAST_FETCH_TIME = 0

# ── 新浪财经行情 ──────────────────────────────────────────

def get_prefix(code):
    c = str(code).strip()
    return 'sh' if c.startswith('6') else 'sz' if c.startswith(('0','3')) else 'sh'

def _rate_limit(min_interval=1.2):
    """确保两次API调用间隔至少min_interval秒"""
    global _LAST_FETCH_TIME
    elapsed = time.time() - _LAST_FETCH_TIME
    if elapsed < min_interval:
        sleep_time = min_interval - elapsed + random.uniform(0.1, 0.3)
        time.sleep(sleep_time)
    _LAST_FETCH_TIME = time.time()

def fetch_kline(code, days=90, max_retries=2):
    """拉取日K线并解析, 返回按日期升序排列（含反爬重试）"""
    if code in PRICE_CACHE:
        return PRICE_CACHE[code]
    prefix = get_prefix(code)
    url = f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={prefix}{code}&scale=240&ma=no&datalen={days}"
    
    for attempt in range(max_retries + 1):
        _rate_limit()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read().decode("gbk", errors="replace")
            data = json.loads(raw)
            if not data:
                raise ValueError("empty response")
            data.sort(key=lambda x: x.get("day", ""))
            PRICE_CACHE[code] = data
            return data
        except (urllib.error.HTTPError, urllib.error.URLError) as e:
            code_str = str(e)
            if "456" in code_str and attempt < max_retries:
                backoff = 3 * (attempt + 1) + random.uniform(0.5, 1.5)
                print(f"  ⚠️ {code} 反爬限制(attempt {attempt+1}), 等待{backoff:.0f}s...")
                time.sleep(backoff)
                continue
            elif attempt < max_retries:
                backoff = 2 * (attempt + 1)
                print(f"  ⚠️ {code} 请求异常(attempt {attempt+1}), 重试...")
                time.sleep(backoff)
                continue
        except Exception as e:
            if attempt < max_retries:
                print(f"  ⚠️ {code} 解析异常(attempt {attempt+1}), 重试...")
                time.sleep(2)
                continue
            print(f"  ⚠️ {code} 行情获取失败: {e}")
            return []
    
    # Fallback to http
    url2 = url.replace("https://", "http://")
    for attempt in range(max_retries + 1):
        _rate_limit()
        try:
            req = urllib.request.Request(url2, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read().decode("gbk", errors="replace")
            data = json.loads(raw)
            if not data:
                raise ValueError("empty response")
            data.sort(key=lambda x: x.get("day", ""))
            PRICE_CACHE[code] = data
            return data
        except (urllib.error.HTTPError, urllib.error.URLError) as e:
            code_str = str(e)
            if "456" in code_str and attempt < max_retries:
                backoff = 3 * (attempt + 1) + random.uniform(0.5, 1.5)
                time.sleep(backoff)
                continue
        except Exception as e2:
            if attempt < max_retries:
                time.sleep(2)
                continue
            print(f"  ⚠️ {code} 行情获取失败(含fallback): {e2}")
            return []
    print(f"  ⚠️ {code} 所有重试均失败")
    return []

def get_price_returns(kline, entry_date: str, hold_days=3):
    """计算持有N日的收益率"""
    for i, bar in enumerate(kline):
        if bar.get("day", "") == entry_date:
            entry_close = float(bar.get("close", 0))
            if entry_close == 0:
                return None
            target_idx = i + hold_days
            if target_idx >= len(kline):
                return None
            target_close = float(kline[target_idx].get("close", 0))
            if target_close == 0:
                return None
            return round((target_close - entry_close) / entry_close * 100, 2)
    return None

def extract_features(kline, entry_date: str) -> dict:
    """从入场日期提取技术因子"""
    entry_idx = None
    for i, bar in enumerate(kline):
        if bar.get("day", "") == entry_date:
            entry_idx = i
            break
    if entry_idx is None or entry_idx < 5:
        return {}
    
    close = float(kline[entry_idx].get("close", 0))
    volume = float(kline[entry_idx].get("volume", 0))
    high = float(kline[entry_idx].get("high", 0))
    low = float(kline[entry_idx].get("low", 0))
    if close == 0:
        return {}
    
    # MA计算
    ma5 = sum(float(kline[entry_idx - j].get("close", 0)) for j in range(5)) / 5
    ma10 = sum(float(kline[entry_idx - j].get("close", 0)) for j in range(10)) / 10 if entry_idx >= 9 else close
    ma20 = sum(float(kline[entry_idx - j].get("close", 0)) for j in range(20)) / 20 if entry_idx >= 19 else close
    
    # 6因子
    ma5_div = (close - ma5) / ma5 * 100 if ma5 else 0
    ma10_div = (close - ma10) / ma10 * 100 if ma10 else 0
    
    ret5_close = float(kline[entry_idx - 4].get("close", 0)) if entry_idx >= 4 else close
    ret20_close = float(kline[entry_idx - 19].get("close", 0)) if entry_idx >= 19 else close
    ret5 = (close - ret5_close) / ret5_close * 100 if ret5_close else 0
    ret20 = (close - ret20_close) / ret20_close * 100 if ret20_close else 0
    
    # 20日波动率
    if entry_idx >= 19:
        daily_returns = []
        for j in range(20):
            if entry_idx - j - 1 >= 0:
                c1 = float(kline[entry_idx - j].get("close", 0))
                c2 = float(kline[entry_idx - j - 1].get("close", 0))
                if c2:
                    daily_returns.append((c1 / c2 - 1) * 100)
        vol20 = (sum(r * r for r in daily_returns) / max(len(daily_returns), 1)) ** 0.5 if daily_returns else 0
    else:
        vol20 = 0
    
    # 量比
    avg_vol_5 = sum(float(kline[entry_idx - j].get("volume", 0)) for j in range(1, 6)) / 5 if entry_idx >= 5 else 1
    vol_ratio = volume / avg_vol_5 if avg_vol_5 > 0 else 1.0
    
    # 额外
    day_range = (high - low) / close * 100 if close else 0
    ma20_pos = (close - ma20) / ma20 * 100 if ma20 else 0
    
    return {
        "ma5_div": round(ma5_div, 2),
        "ma10_div": round(ma10_div, 2),
        "ret5": round(ret5, 2),
        "ret20": round(ret20, 2),
        "vol20": round(vol20, 2),
        "vol_ratio": round(vol_ratio, 2),
        "day_range": round(day_range, 2),
        "ma20_pos": round(ma20_pos, 2),
        "close_price": close,
    }

# ── 决策报告解析 ──────────────────────────────────────────

STOCK_PATTERN = r'\|\s*([\u4e00-\u9fa5]{2,8})\s*[（(]\s*(\d{6})\s*[）)]\s*\|\s*(\d{2,3})\s*分?\s*\|'
MAIN_PUSH = r'【主推】\s*([\u4e00-\u9fa5]{2,8})\s*[（(]\s*(\d{6})\s*[）)]\s*.*?(\d{2,3})\s*分'

def parse_report(text: str) -> tuple[list, list]:
    """返回 (评分股票列表, 主推标的列表)"""
    stocks = []
    seen = set()
    for m in re.finditer(STOCK_PATTERN, text):
        name, code, score = m.group(1), m.group(2), int(m.group(3))
        if 30 <= score <= 100 and code not in seen:
            seen.add(code)
            stocks.append({"code": code, "name": name, "score": score})
    
    main_push = []
    for m in re.finditer(MAIN_PUSH, text):
        name, code, score = m.group(1), m.group(2), int(m.group(3))
        main_push.append({"code": code, "name": name, "score": score})
    
    return stocks, main_push

# ── 主流程 ────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("ML评分模型 v2 — 全量提取")
    print("=" * 60)
    
    reports = sorted(HISTORY.glob("*决策报告*.md"))
    print(f"📂 {len(reports)} 份决策报告")
    
    # 第一步：解析所有审查报告（比决策报告含更多股票）
    report_files = sorted(HISTORY.glob("*审查报告*.md"))
    print(f"📂 {len(report_files)} 份审查报告")
    
    all_records = {}
    for fp in report_files:
        date = fp.name[:10]
        text = fp.read_text(encoding="utf-8", errors="replace")
        scored, main_push = parse_report(text)
        
        # 审查报告格式：每个 ## [代码] 名称 是一个股票
        # 用更直接的regex： ## [600118] 中国卫星
        stock_pattern2 = r'^##\s*\[?(\d{6})\]?\s*[\u4e00-\u9fa5]'
        for m in re.finditer(stock_pattern2, text, re.MULTILINE):
            code = m.group(1)
            # 找这一段的评分
            block_start = m.start()
            block_end = text.find('\n## ', block_start + 1)
            if block_end == -1:
                block_end = len(text)
            block = text[block_start:block_end]
            
            # 提取综合评分
            score = None
            for sm in re.finditer(r'综合评分[^\\d]*?(\d{2,3})', block):
                s = int(sm.group(1))
                if 30 <= s <= 100:
                    score = s
                    break
            
            if score is None:
                continue
            
            # 提取名称
            nm = re.match(r'##\s*\[?\d{6}\]?\s*([\u4e00-\u9fa5]{2,10})', block)
            name = nm.group(1) if nm else "?"
            
            if code not in all_records:
                all_records[code] = {"name": name, "entries": []}
            if not any(e["date"] == date for e in all_records[code]["entries"]):
                all_records[code]["entries"].append({
                    "date": date,
                    "score": score,
                    "is_main": False,
                    "source": "review",
                })
    
    # 第二步：单独处理"今日无≥75分标的"的空仓决策
    # 这些报告虽然没有评分表, 但有重要信号：偏空市场
    empty_dates = []
    for fp in reports:
        text = fp.read_text(encoding="utf-8", errors="replace")
        if "暂无" in text or "空仓" in text or "无≥75" in text or "无审查通过" in text:
            date = fp.name[:10]
            empty_dates.append(date)
    
    print(f"📊 不重复股票: {len(all_records)} 只")
    total_entries = sum(len(v["entries"]) for v in all_records.values())
    print(f"📊 评分记录: {total_entries} 条")
    print(f"📊 空仓决策日: {len(empty_dates)} 天")
    
    # 第三步：拉行情+算收益率
    print(f"\n📈 拉行情...")
    dataset = []
    failed_price = 0
    
    for i, (code, info) in enumerate(all_records.items()):
        kline = fetch_kline(code, 120)
        if not kline:
            failed_price += 1
            continue
        
        for entry in info["entries"]:
            date = entry["date"]
            r3 = get_price_returns(kline, date, 3)
            r5 = get_price_returns(kline, date, 5)
            r10 = get_price_returns(kline, date, 10)
            features = extract_features(kline, date)
            
            dataset.append({
                "code": code,
                "name": info["name"],
                "date": date,
                "score": entry["score"],
                "is_main": entry["is_main"],
                "r3": r3,
                "r5": r5,
                "r10": r10,
                **features,
            })
        
        if (i + 1) % 10 == 0:
            print(f"  ... {i+1}/{len(all_records)} 只股票处理完成")
        time.sleep(0.15)
    
    if failed_price:
        print(f"  ⚠️ {failed_price} 只股票行情获取失败")
    
    # 保存
    output = {
        "total_stocks": len(all_records),
        "total_entries": total_entries,
        "actual_records": len(dataset),
        "empty_dates": len(empty_dates),
        "records": dataset,
    }
    out_path = OUT_DIR / "dataset_v2.json"
    with open(out_path, "w") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n💾 保存: {out_path}")
    
    # 统计
    valid = [r for r in dataset if r["r3"] is not None]
    if valid:
        wins = sum(1 for r in valid if r["r3"] > 0)
        scores = [r["score"] for r in valid]
        r3s = [r["r3"] for r in valid]
        print(f"\n📊 有效数据: {len(valid)}/{len(dataset)}")
        print(f"   3日胜率: {wins}/{len(valid)} = {wins/len(valid)*100:.1f}%")
        print(f"   评分范围: {min(scores)}~{max(scores)}, 均值 {sum(scores)/len(scores):.1f}")
        print(f"   r3范围: {min(r3s):.2f}~{max(r3s):.2f}, 均值 {sum(r3s)/len(r3s):.2f}%")
        
        # 按分数段统计胜率
        from collections import defaultdict
        buckets = defaultdict(list)
        for r in valid:
            bucket = (r["score"] // 10) * 10  # 70-79, 80-89, etc
            buckets[bucket].append(r)
        print(f"\n   按分数段:")
        for bucket in sorted(buckets.keys()):
            items = buckets[bucket]
            seg_wins = sum(1 for r in items if r["r3"] > 0)
            seg_r3 = sum(r["r3"] for r in items) / len(items)
            print(f"      {bucket}-{bucket+9}分: {len(items)}条 胜率{seg_wins/len(items)*100:.0f}% r3均值{seg_r3:.2f}%")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
ML 训练数据回补 v2 — 并发版 + 东财/腾讯双源
2026-09-09

快速策略：
- 东财优先，失败立即切腾讯（不重试）
- ThreadPoolExecutor 并发 8 线程
- 结果缓存到缓存文件
"""
import json, time, sys, math
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib.request
import urllib.error

BASE = Path(__file__).parent.parent
MODEL_DIR = BASE / "data" / "ml_model"
CACHE_FILE = MODEL_DIR / "kline_cache.json"

EM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://quote.eastmoney.com/",
}

def em_kline(code: str, beg: str, end: str) -> list:
    """东财 K 线"""
    market = 1 if code.startswith(("60", "68")) else 0
    secid = f"{market}.{code}"
    url = (f"http://push2his.eastmoney.com/api/qt/stock/kline/get"
           f"?secid={secid}&fields1=f1,f2,f3,f4,f5,f6"
           f"&fields2=f51,f52,f53,f54,f55,f56,f57"
           f"&klt=101&fqt=1&beg={beg}&end={end}&lmt=500")
    try:
        req = urllib.request.Request(url, headers=EM_HEADERS)
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        klines = data.get("data", {}).get("klines", [])
        result = []
        for k in klines:
            parts = k.split(",")
            result.append({"date": parts[0], "open": float(parts[1]), "close": float(parts[2]),
                           "high": float(parts[3]), "low": float(parts[4]), "volume": float(parts[5]),
                           "amount": float(parts[6])})
        return result
    except:
        return []


def tx_kline(code: str, beg: str, end: str) -> list:
    """腾讯 K 线（limit=500 + 本地过滤）"""
    tx_code = f"sh{code}" if code.startswith(("60", "68")) else f"sz{code}"
    url = f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={tx_code},day,,,500,qfq"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": EM_HEADERS["User-Agent"]})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        d = data.get("data", {})
        inner = d.get(tx_code, {}) if isinstance(d, dict) else {}
        klines = (inner.get("qfqday", []) or inner.get("day", [])) if isinstance(inner, dict) else []
        beg_dt = datetime.strptime(beg, "%Y%m%d")
        end_dt = datetime.strptime(end, "%Y%m%d")
        result = []
        for k in klines:
            try:
                kdate = datetime.strptime(k[0], "%Y-%m-%d")
                if beg_dt <= kdate <= end_dt:
                    result.append({"date": k[0], "open": float(k[1]), "close": float(k[2]),
                                   "high": float(k[3]), "low": float(k[4]), "volume": float(k[5]),
                                   "amount": 0})
            except (ValueError, IndexError):
                continue
        return result
    except:
        return []


def fetch_kline(code: str, beg: str, end: str) -> list:
    """优先东财，失败切腾讯"""
    result = em_kline(code, beg, end)
    if result:
        return result
    return tx_kline(code, beg, end)


def load_cache():
    if CACHE_FILE.exists():
        with open(CACHE_FILE) as f:
            return json.load(f)
    return {}


def save_cache(cache):
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, ensure_ascii=False)


def compute_features(klines: list, target_date: str) -> dict:
    """从 K 线计算全部特征"""
    if not klines or target_date not in [k["date"] for k in klines]:
        return {}
    idx = next(i for i, k in enumerate(klines) if k["date"] == target_date)
    if idx < 20:
        return {}

    closes = np.array([k["close"] for k in klines[:idx + 1]])
    volumes = np.array([k["volume"] for k in klines[:idx + 1]])
    highs = np.array([k["high"] for k in klines[:idx + 1]])
    lows = np.array([k["low"] for k in klines[:idx + 1]])
    opens = np.array([k["open"] for k in klines[:idx + 1]])

    cur_close = float(closes[-1])
    ma5 = float(np.mean(closes[-5:]))
    ma10 = float(np.mean(closes[-10:]))
    ma20 = float(np.mean(closes[-20:]))

    rets = np.diff(closes[-21:]) / closes[-21:-1]
    vol20_raw = float(np.std(rets[-20:])) if len(rets) >= 20 else 0

    # 原有 8 特征
    ma5_div = round((cur_close - ma5) / ma5 * 100, 2)
    ma10_div = round((cur_close - ma10) / ma10 * 100, 2)
    ma20_pos = round((cur_close - ma20) / ma20 * 100, 2)
    ret5 = round(float((closes[-1] - closes[-6]) / closes[-6] * 100), 2) if idx >= 5 else 0
    ret20 = round(float((closes[-1] - closes[-21]) / closes[-21] * 100), 2) if idx >= 20 else 0
    vol20 = round(vol20_raw * 100, 2)
    vol_ratio = round(float(np.mean(volumes[-5:]) / np.mean(volumes[-20:])) if np.mean(volumes[-20:]) > 0 else 0, 2)
    day_range = round(float((highs[-1] - lows[-1]) / cur_close * 100), 2)

    # 新增 8 特征
    bias_5 = ma5_div  # 同 ma5_div
    bias_20 = ma20_pos  # 同 ma20_pos
    turnover = round(float(np.mean(volumes[-5:]) / np.mean(volumes[-20:])) if np.mean(volumes[-20:]) > 0 else 0, 2)
    amps = (highs[-20:] - lows[-20:]) / closes[-20:] * 100
    amplitude = round(float(np.mean(amps)), 2)
    gap_up = round(float((opens[-1] - closes[-2]) / closes[-2] * 100), 2) if idx >= 1 else 0
    vol_ma5 = round(float(np.mean(volumes[-5:]) / np.mean(volumes[-20:])) if np.mean(volumes[-20:]) > 0 else 0, 2)
    ma20_3ago = float(np.mean(closes[-23:-3])) if idx >= 23 else ma20
    ma20_slope = round((ma20 - ma20_3ago) / ma20_3ago * 100, 2) if ma20_3ago > 0 else 0
    rets_5 = np.diff(closes[-6:]) / closes[-6:-1]
    ret5_annual = round(float(np.std(rets_5) * math.sqrt(252) * 100), 2) if len(rets_5) >= 4 else 0

    return {
        "ma5_div": ma5_div, "ma10_div": ma10_div, "ret5": ret5, "ret20": ret20,
        "vol20": vol20, "vol_ratio": vol_ratio, "day_range": day_range, "ma20_pos": ma20_pos,
        "bias_5": bias_5, "bias_20": bias_20, "turnover": turnover, "amplitude": amplitude,
        "gap_up": gap_up, "vol_ma5": vol_ma5, "ma20_slope": ma20_slope, "ret5_annual": ret5_annual,
        "close_price": cur_close,
    }


def compute_returns(klines: list, target_date: str) -> dict:
    """计算未来收益"""
    if not klines or target_date not in [k["date"] for k in klines]:
        return {"r3": None, "r5": None, "r10": None}
    idx = next(i for i, k in enumerate(klines) if k["date"] == target_date)
    cur = klines[idx]["close"]
    def safe_ret(d):
        return round((klines[idx + d]["close"] - cur) / cur * 100, 2) if idx + d < len(klines) else None
    return {"r3": safe_ret(3), "r5": safe_ret(5), "r10": safe_ret(10)}


def main():
    print("=" * 60)
    print("ML 训练数据回补 v2 — 并发双源")
    print("=" * 60)

    with open(MODEL_DIR / "dataset_v2.json") as f:
        data = json.load(f)
    records = data["records"]
    print(f"\n[原始] {len(records)} 条")

    # 去重
    seen, unique = set(), []
    for r in records:
        key = (r.get("code"), r.get("date"))
        if key not in seen:
            seen.add(key)
            unique.append(r)
    records = unique
    print(f"[去重] {len(records)} 条")

    # 过滤全零
    before = len(records)
    records = [r for r in records if any(
        r.get(f, 0) != 0 for f in ["ma5_div", "ma10_div", "ret5", "ret20", "vol20", "vol_ratio", "day_range", "ma20_pos"]
    )]
    print(f"[过滤全零] {len(records)} 条（去 {before - len(records)}）")

    # 按股票分组
    by_stock = {}
    for r in records:
        by_stock.setdefault(r["code"], set()).add(r["date"])

    all_stocks = sorted(by_stock.keys())
    print(f"\n[股票] {len(all_stocks)} 只")

    # 加载缓存
    cache = load_cache()
    print(f"[缓存] {len(cache)} 只已有缓存")

    # 需要拉取的股票
    to_fetch = [c for c in all_stocks if c not in cache]
    print(f"[待拉] {len(to_fetch)} 只")

    # 并发拉取
    def fetch_one(code):
        dates = by_stock[code]
        min_d = datetime.strptime(min(dates), "%Y-%m-%d") - timedelta(days=60)
        max_d = datetime.strptime(max(dates), "%Y-%m-%d") + timedelta(days=20)
        beg = min_d.strftime("%Y%m%d")
        end = max_d.strftime("%Y%m%d")
        return code, fetch_kline(code, beg, end)

    if to_fetch:
        print(f"\n[并发拉取] {len(to_fetch)} 只，8线程...")
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(fetch_one, c): c for c in to_fetch}
            for i, fut in enumerate(as_completed(futures)):
                code, klines = fut.result()
                if klines:
                    cache[code] = klines
                    print(f"  [{i+1}/{len(to_fetch)}] {code}: {len(klines)}天")
                else:
                    cache[code] = []
                    print(f"  [{i+1}/{len(to_fetch)}] {code}: 0天 ⚠️")
        save_cache(cache)

    # 回补记录
    print(f"\n[回补计算] {len(records)} 条记录...")
    updated, failed = 0, 0
    for r in records:
        code, date = r["code"], r["date"]
        klines = cache.get(code, [])
        if not klines:
            failed += 1
            continue
        feats = compute_features(klines, date)
        if not feats:
            failed += 1
            continue
        futures = compute_returns(klines, date)
        for f, v in feats.items():
            r[f] = v
        for f, v in futures.items():
            r[f] = v
        r["close_price"] = feats["close_price"]
        updated += 1

    has_r3 = sum(1 for r in records if r.get("r3") is not None)
    has_r5 = sum(1 for r in records if r.get("r5") is not None)
    has_r10 = sum(1 for r in records if r.get("r10") is not None)
    print(f"  更新: {updated}, 失败: {failed}")
    print(f"  r3: {has_r3}/{len(records)}, r5: {has_r5}/{len(records)}, r10: {has_r10}/{len(records)}")

    # 保存
    v3 = {
        "total_stocks": len(set(r["code"] for r in records)),
        "total_entries": len(records),
        "records_with_r5": has_r5,
        "updated": updated,
        "failed": failed,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "feature_keys": [
            "score", "ma5_div", "ma10_div", "ret5", "ret20", "vol20", "vol_ratio",
            "day_range", "ma20_pos", "bias_5", "bias_20", "turnover", "amplitude",
            "gap_up", "vol_ma5", "ma20_slope", "ret5_annual"
        ],
        "records": records,
    }
    out_path = MODEL_DIR / "dataset_v3.json"
    with open(out_path, "w") as f:
        json.dump(v3, f, ensure_ascii=False, indent=2)
    print(f"\n[保存] {out_path}")

    # 验证
    print("\n[验证] 前 5 条:")
    for r in [x for x in records if x.get("r5") is not None][:5]:
        print(f"  {r['code']} {r['name']} {r['date']}: "
              f"score={r.get('score')} r5={r['r5']} r10={r['r10']} "
              f"bias_5={r.get('bias_5')} turnover={r.get('turnover')} amplitude={r.get('amplitude')}")


if __name__ == "__main__":
    main()

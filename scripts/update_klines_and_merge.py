#!/usr/bin/env python3
"""更新 K 线缓存到 9/10，多轮重试"""
import json, time, math
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
import urllib.request

MODEL_DIR = Path("data/ml_model")
CACHE_FILE = MODEL_DIR / "kline_cache.json"
EM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://quote.eastmoney.com/",
}

def em_fetch(code, beg="20260401", end="20260910"):
    market = 1 if code.startswith(("60", "68")) else 0
    url = (
        f"http://push2his.eastmoney.com/api/qt/stock/kline/get"
        f"?secid={market}.{code}&fields1=f1,f2,f3,f4,f5,f6"
        f"&fields2=f51,f52,f53,f54,f55,f56,f57"
        f"&klt=101&fqt=1&beg={beg}&end={end}&lmt=500"
    )
    req = urllib.request.Request(url, headers=EM_HEADERS)
    with urllib.request.urlopen(req, timeout=10) as resp:
        d = json.loads(resp.read().decode())
    result = []
    for k in d.get("data", {}).get("klines", []):
        p = k.split(",")
        result.append({
            "date": p[0], "open": float(p[1]), "close": float(p[2]),
            "high": float(p[3]), "low": float(p[4]), "volume": float(p[5]),
            "amount": float(p[6]),
        })
    return result


def compute_features(klines, target_date):
    if not klines or target_date not in [k["date"] for k in klines]:
        return {}
    idx = next(i for i, k in enumerate(klines) if k["date"] == target_date)
    if idx < 20:
        return {}
    cl = np.array([k["close"] for k in klines[:idx + 1]])
    vol = np.array([k["volume"] for k in klines[:idx + 1]])
    hi = np.array([k["high"] for k in klines[:idx + 1]])
    lo = np.array([k["low"] for k in klines[:idx + 1]])
    op = np.array([k["open"] for k in klines[:idx + 1]])
    cc = float(cl[-1])
    m5 = float(np.mean(cl[-5:]))
    m10 = float(np.mean(cl[-10:]))
    m20 = float(np.mean(cl[-20:]))
    rt = np.diff(cl[-21:]) / cl[-21:-1]
    v20 = float(np.std(rt[-20:])) if len(rt) >= 20 else 0
    return {
        "ma5_div": round((cc - m5) / m5 * 100, 2),
        "ma10_div": round((cc - m10) / m10 * 100, 2),
        "ma20_pos": round((cc - m20) / m20 * 100, 2),
        "ret5": round(float((cl[-1] - cl[-6]) / cl[-6] * 100), 2) if idx >= 5 else 0,
        "ret20": round(float((cl[-1] - cl[-21]) / cl[-21] * 100), 2) if idx >= 20 else 0,
        "vol20": round(v20 * 100, 2),
        "vol_ratio": round(float(np.mean(vol[-5:]) / np.mean(vol[-20:])), 2) if np.mean(vol[-20:]) > 0 else 0,
        "day_range": round(float((hi[-1] - lo[-1]) / cc * 100), 2),
        "amplitude": round(float(np.mean((hi[-20:] - lo[-20:]) / cl[-20:] * 100)), 2),
        "gap_up": round(float((op[-1] - cl[-2]) / cl[-2] * 100), 2) if idx >= 1 else 0,
        "ma20_slope": round((m20 - float(np.mean(cl[-23:-3]))) / float(np.mean(cl[-23:-3])) * 100, 2) if idx >= 23 else 0,
        "ret5_annual": round(float(np.std(np.diff(cl[-6:]) / cl[-6:-1]) * math.sqrt(252) * 100), 2) if idx >= 5 else 0,
        "close_price": cc,
    }


def compute_returns(klines, target_date):
    if not klines or target_date not in [k["date"] for k in klines]:
        return {"r3": None, "r5": None, "r10": None}
    idx = next(i for i, k in enumerate(klines) if k["date"] == target_date)
    cur = klines[idx]["close"]
    def safe(d):
        return round((klines[idx + d]["close"] - cur) / cur * 100, 2) if idx + d < len(klines) else None
    return {"r3": safe(3), "r5": safe(5), "r10": safe(10)}


def main():
    with open(MODEL_DIR / "dataset_v3.json") as f:
        v3 = json.load(f)
    existing = set((r["code"], r["date"]) for r in v3["records"])
    print(f"现有: {len(v3['records'])} 条")

    with open(CACHE_FILE) as f:
        cache = json.load(f)

    # 加载 recommendation_tracker
    with open("data/recommendation_tracker.json") as f:
        rt = json.load(f)

    # 多轮重试拉取 K 线（扩展到 9/10）
    codes_needed = set()
    for r in rt:
        c = r.get("code")
        if c and (c, r.get("date")) not in existing:
            codes_needed.add(c)

    print(f"需要 K 线的股票: {len(codes_needed)}")

    # 5 轮重试
    remaining = list(codes_needed)
    for rnd in range(5):
        if not remaining:
            break
        print(f"\n--- 第 {rnd+1} 轮 ({len(remaining)} 只) ---")
        still = []
        for code in remaining:
            try:
                fresh = em_fetch(code, "20260401", "20260910")
                if fresh:
                    cache[code] = fresh
                    print(f"  {code}: {len(fresh)}天")
                else:
                    still.append(code)
            except Exception as e:
                still.append(code)
            time.sleep(0.3)
        remaining = still
        if remaining and rnd < 4:
            print(f"  等待 3 秒...")
            time.sleep(3)

    # 合并记录
    new_count = 0
    for r in rt:
        code, date = r.get("code"), r.get("date")
        if not code or not date or (code, date) in existing:
            continue
        typ = r.get("type", "")
        score = 70 if typ == "主推" else 60
        existing.add((code, date))
        new = {"code": code, "name": r.get("name", ""), "date": date, "score": int(score), "is_main": typ == "主推"}
        kl = cache.get(code, [])
        if not kl:
            print(f"  {code}: 无K线")
            continue
        feats = compute_features(kl, date)
        if not feats:
            print(f"  {code} {date}: 特征失败")
            continue
        rets = compute_returns(kl, date)
        for k, v in feats.items():
            new[k] = v
        for k, v in rets.items():
            new[k] = v
        new["close_price"] = feats["close_price"]
        v3["records"].append(new)
        new_count += 1
        print(f"  + {code} {date} r5={new['r5']}")

    has_r5 = sum(1 for r in v3["records"] if r.get("r5") is not None)
    print(f"\n新增 {new_count} 条, 总计 {len(v3['records'])}, 有r5: {has_r5}")

    v3["total_entries"] = len(v3["records"])
    v3["records_with_r5"] = has_r5
    with open(MODEL_DIR / "dataset_v3.json", "w") as f:
        json.dump(v3, f, ensure_ascii=False, indent=2)
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, ensure_ascii=False)


if __name__ == "__main__":
    main()

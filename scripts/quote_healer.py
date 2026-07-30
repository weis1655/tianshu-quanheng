#!/usr/bin/env python3
"""
天枢权衡 — 行情数据自愈修复模块

功能：
1. 检测池中行情数据缺失（最新价=0/None/负值）
2. 多源轮询补数（腾讯行情→新浪行情→东方财富行情）
3. 补数后校验合理性（价格>0, 涨跌幅<20%）
4. 修复成功后更新池文件

用法:
    python scripts/quote_healer.py                      # 扫描所有池并补数
    python scripts/quote_healer.py --pool 快筛候选池    # 指定池
    python scripts/quote_healer.py --dry-run            # 预览
    python scripts/quote_healer.py --verbose            # 详细输出
"""

import os, sys, json, datetime, re, urllib.request, argparse
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
POOLS_DIR = PROJECT_ROOT / "五池管理"
DATA_DIR = PROJECT_ROOT / "data"
FIX_LOG = DATA_DIR / "dq" / "dq_fix_log.jsonl"

NOW = datetime.datetime.now()
TODAY = datetime.date.today()

# 行情源优先级
QUOTE_SOURCES = [
    {"name": "腾讯行情", "url_template": "https://qt.gtimg.cn/q={prefix}{code}", "encoding": "gbk"},
    {"name": "新浪行情", "url_template": "http://hq.sinajs.cn/list={prefix}{code}", "encoding": "gbk"},
    {"name": "东方财富", "url_template": "https://push2.eastmoney.com/api/qt/stock/get?secid={em_prefix}.{code}&fields=f43,f44,f45,f46,f47,f48,f50,f57,f58,f170", "encoding": "utf-8"},
]

# 市场前缀映射
MARKET_PREFIX = {
    "6": "sh", "5": "sh", "9": "sh",  # 上海
    "0": "sz", "2": "sz", "3": "sz",  # 深圳
    "4": "bj", "8": "bj",  # 北京
}


def get_market_prefix(code: str) -> str:
    """根据代码前缀返回市场前缀"""
    first = str(code)[0] if code else "0"
    return MARKET_PREFIX.get(first, "sz")


def get_em_prefix(code: str) -> int:
    """东方财富secid前缀"""
    first = str(code)[0] if code else "0"
    return 1 if first in ("6", "5", "9") else (2 if first in ("0", "2", "3") else 0)


def fetch_tencent_quote(code: str) -> dict | None:
    """从腾讯行情获取单只股票行情"""
    prefix = get_market_prefix(code)
    url = f"https://qt.gtimg.cn/q={prefix}{code}"
    try:
        req = urllib.request.Request(url, headers={
            "Referer": "https://finance.qq.com",
            "User-Agent": "Mozilla/5.0"
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("gbk", errors="replace")
        # 解析腾讯行情格式: v_sh600000="1~名称~...~现价~...~涨跌幅~...~换手率~..."
        if code not in body:
            return None
        parts = body.split("~")
        if len(parts) < 40:
            return None
        return {
            "source": "腾讯行情",
            "最新价": _safe_float(parts[3]),
            "涨跌幅": _safe_float(parts[32]),
            "涨跌额": _safe_float(parts[4]),
            "换手率": _safe_float(parts[38]),
            "量比": _safe_float(parts[39]),
            "振幅": _safe_float(parts[43]),
            "今开": _safe_float(parts[5]),
            "最高": _safe_float(parts[33]),
            "最低": _safe_float(parts[34]),
            "昨收": _safe_float(parts[4]),
        }
    except Exception:
        return None


def fetch_sina_quote(code: str) -> dict | None:
    """从新浪行情获取"""
    prefix = get_market_prefix(code)
    url = f"http://hq.sinajs.cn/list={prefix}{code}"
    try:
        req = urllib.request.Request(url, headers={
            "Referer": "https://finance.sina.com.cn",
            "User-Agent": "Mozilla/5.0"
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("gbk", errors="replace")
        # 解析新浪格式: var hq_str_sh600000="名称,今开,昨收,现价,最高,最低, ..., 涨跌幅, ...";
        if "hq_str" not in body:
            return None
        m = re.search(r'"(.*?)"', body)
        if not m:
            return None
        parts = m.group(1).split(",")
        if len(parts) < 32:
            return None
        price = _safe_float(parts[3])
        prev_close = _safe_float(parts[2])
        change_pct = 0.0
        if prev_close and prev_close > 0:
            change_pct = round((price - prev_close) / prev_close * 100, 2)
        return {
            "source": "新浪行情",
            "最新价": price,
            "涨跌幅": change_pct,
            "今开": _safe_float(parts[1]),
            "昨收": prev_close,
            "最高": _safe_float(parts[4]),
            "最低": _safe_float(parts[5]),
        }
    except Exception:
        return None


def fetch_eastmoney_quote(code: str) -> dict | None:
    """从东方财富获取"""
    em_p = get_em_prefix(code)
    url = f"https://push2.eastmoney.com/api/qt/stock/get?secid={em_p}.{code}&fields=f43,f44,f45,f46,f47,f48,f50,f57,f58,f170"
    try:
        req = urllib.request.Request(url, headers={
            "Referer": "https://quote.eastmoney.com",
            "User-Agent": "Mozilla/5.0"
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("data"):
            d = data["data"]
            price = _safe_float(d.get("f43"))
            prev_close = _safe_float(d.get("f44"))
            change_pct = 0.0
            if prev_close and prev_close > 0 and price:
                change_pct = round((price - prev_close) / prev_close * 100, 2)
            return {
                "source": "东方财富",
                "最新价": price,
                "涨跌幅": change_pct,
                "换手率": _safe_float(d.get("f50")),
                "量比": _safe_float(d.get("f48")),
                "振幅": _safe_float(d.get("f170")),
                "最高": _safe_float(d.get("f47")),
                "最低": _safe_float(d.get("f46")),
                "今开": _safe_float(d.get("f45")),
                "昨收": prev_close,
            }
        return None
    except Exception:
        return None


def _safe_float(v) -> float:
    """安全转float"""
    if v is None:
        return 0.0
    try:
        return float(v)
    except (ValueError, TypeError):
        return 0.0


def _get_stock_code(stock: dict) -> str:
    for key in ["代码", "股票代码", "code", "stock_code", "symbol"]:
        v = stock.get(key)
        if v:
            return str(v)
    return "?"


def _get_stock_name(stock: dict) -> str:
    for key in ["名称", "股票名称", "name", "stock_name"]:
        v = stock.get(key)
        if v:
            return str(v)
    return "?"


def _get_price(stock: dict) -> float | None:
    for key in ["最新价", "今日收盘", "现价", "current_price", "price", "入场价", "建仓价", "成本价"]:
        v = stock.get(key)
        if v is not None:
            try:
                return float(v)
            except (ValueError, TypeError):
                pass
    return None


def need_heal(stock: dict) -> tuple[bool, str]:
    """判断是否需要行情自愈"""
    price = _get_price(stock)
    code = _get_stock_code(stock)
    if price is None:
        return True, "价格缺失"
    if price <= 0:
        return True, "价格<=0"
    return False, ""


def validate_quote_result(quote: dict) -> bool:
    """校验行情修复结果合理性"""
    price = quote.get("最新价", 0)
    if not price or price <= 0:
        return False
    if price > 10000:
        return False
    change_pct = abs(quote.get("涨跌幅", 0))
    if change_pct > 20:
        return False
    return True


def heal_stock(code: str) -> tuple[dict | None, str]:
    """多源轮询修复单只股票行情"""
    errors = []
    for source in QUOTE_SOURCES:
        name = source["name"]
        try:
            if name == "腾讯行情":
                result = fetch_tencent_quote(code)
            elif name == "新浪行情":
                result = fetch_sina_quote(code)
            elif name == "东方财富":
                result = fetch_eastmoney_quote(code)
            else:
                continue

            if result and validate_quote_result(result):
                result["修复源"] = name
                return result, ""
            errors.append(f"{name}: 数据无效")
        except Exception as e:
            errors.append(f"{name}: {e}")

    return None, "; ".join(errors)


def write_fix_log(entry: dict):
    """写入修复日志，同时触发信号重生成标记"""
    FIX_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(FIX_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    # 信号重生成：行情修复后标记需要重算的信号
    signal_file = DATA_DIR / "dq" / f"signal_regen_{TODAY.isoformat()}.jsonl"
    signal_file.parent.mkdir(parents=True, exist_ok=True)
    with open(signal_file, "a", encoding="utf-8") as f:
        signal = {
            "time": NOW.strftime("%Y-%m-%d %H:%M:%S"),
            "pool": entry.get("target", "?").split("/")[0] if "/" in entry.get("target", "") else "?",
            "target": entry.get("target", "?"),
            "fix_type": "行情自愈",
            "before": entry.get("before", ""),
            "after": entry.get("after", ""),
            "needs_recalculation": True
        }
        f.write(json.dumps(signal, ensure_ascii=False) + "\n")


def scan_pool(pool_name: str, dry_run: bool = False) -> list[dict]:
    """扫描单个池，修复缺失行情"""
    pool_file = POOLS_DIR / f"{pool_name}.json"
    if not pool_file.exists():
        return []

    try:
        with open(pool_file, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"❌ 读取{pool_name}失败: {e}")
        return []

    stocks = data.get("stocks", [])
    fixes = []
    modified = False

    for i, stock in enumerate(stocks):
        code = _get_stock_code(stock)
        name = _get_stock_name(stock)
        needs, reason = need_heal(stock)
        if not needs:
            continue

        print(f"  🔍 {code} {name}: {reason} → 修复中...")
        result, error = heal_stock(code)

        if result and validate_quote_result(result):
            # 修复成功
            stock["最新价"] = result["最新价"]
            if result.get("涨跌幅"):
                stock["涨跌幅"] = result["涨跌幅"]
            if result.get("换手率"):
                stock["换手率"] = result["换手率"]
            if result.get("量比"):
                stock["量比"] = result["量比"]
            if result.get("振幅"):
                stock["振幅"] = result["振幅"]
            stock["更新时间"] = NOW.strftime("%Y-%m-%d %H:%M:%S")
            modified = True

            fix_entry = {
                "time": NOW.strftime("%Y-%m-%d %H:%M:%S"),
                "type": "行情自愈",
                "target": f"{pool_name}/{code} {name}",
                "before": f"价格缺失({reason})",
                "after": f"修复价={result['最新价']} (来源:{result['修复源']})",
                "validated": True
            }
            fixes.append(fix_entry)
            write_fix_log(fix_entry)
            print(f"    ✅ 修复成功: {result['最新价']}元 (来源:{result['修复源']})")
        else:
            error_msg = error or "所有行情源均失败"
            fix_entry = {
                "time": NOW.strftime("%Y-%m-%d %H:%M:%S"),
                "type": "行情自愈-失败",
                "target": f"{pool_name}/{code} {name}",
                "before": f"价格缺失({reason})",
                "after": f"修复失败: {error_msg}",
                "validated": False
            }
            fixes.append(fix_entry)
            write_fix_log(fix_entry)
            print(f"    ❌ 修复失败: {error_msg}")

    if modified and not dry_run:
        # 更新统计
        if "统计" in data:
            data["统计"]["更新日期"] = NOW.strftime("%Y-%m-%d %H:%M:%S")
        with open(pool_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"  ✅ {pool_name} 已更新")

    return fixes


def main():
    parser = argparse.ArgumentParser(description="天枢行情自愈修复")
    parser.add_argument("--pool", help="指定池名称（默认全部）")
    parser.add_argument("--dry-run", action="store_true", help="预览模式，不实际修改")
    parser.add_argument("--verbose", action="store_true", help="详细输出")
    args = parser.parse_args()

    pool_names = ["快筛候选池", "重点观察池", "S级操作池", "持仓池"]
    if args.pool:
        pool_names = [args.pool]

    total_fixes = 0
    total_failures = 0
    start = NOW

    print(f"📊 天枢行情自愈修复 | {start.strftime('%Y-%m-%d %H:%M:%S')}")
    if args.dry_run:
        print("  模式: 预览 (不修改)")
    print()

    for pool_name in pool_names:
        print(f"📁 扫描 {pool_name}...")
        fixes = scan_pool(pool_name, dry_run=args.dry_run)
        successes = [f for f in fixes if "修复成功" in f.get("after", "")]
        failures = [f for f in fixes if "修复失败" in f.get("after", "")]
        total_fixes += len(successes)
        total_failures += len(failures)
        if args.verbose:
            for f in fixes:
                status = "✅" if f["validated"] else "❌"
                print(f"  {status} {f['target']}: {f['before']} → {f['after']}")
        print(f"  → {len(successes)}成功, {len(failures)}失败")
        print()

    elapsed = (datetime.datetime.now() - start).total_seconds()
    print(f"📊 修复完成 | {total_fixes}成功, {total_failures}失败 | 耗时{elapsed:.1f}s")


if __name__ == "__main__":
    main()
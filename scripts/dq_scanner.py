#!/usr/bin/env python3
"""
天枢权衡 — 数据质量扫描脚本 v2

五维数据质量检测（增强版）：完整性、准确性、及时性、一致性、有效性。
支持 --fix 模式自动修复。覆盖20条检测规则。

用法:
    python scripts/dq_scanner.py             # 默认JSON输出
    python scripts/dq_scanner.py --verbose   # 详细文本报告
    python scripts/dq_scanner.py --alert     # 告警输出
    python scripts/dq_scanner.py --fix       # 自动修复
    python scripts/dq_scanner.py --fix --dry-run  # 预览修复
"""

import os, sys, json, datetime, glob, re, shutil, argparse
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
POOLS_DIR = PROJECT_ROOT / "五池管理"
DATA_DIR = PROJECT_ROOT / "data"
DQ_DIR = DATA_DIR / "dq"
FIX_LOG = DQ_DIR / "dq_fix_log.jsonl"
DQ_DIR.mkdir(parents=True, exist_ok=True)

TODAY = datetime.date.today()
NOW = datetime.datetime.now()

# 交易时段判断
MARKET_OPEN = 9    # 9:00
MARKET_CLOSE = 15  # 15:00
IS_TRADING_HOURS = MARKET_OPEN <= NOW.hour < MARKET_CLOSE

# 报告类型清单
REPORT_TYPES = [
    "宏观前置分析", "快筛报告", "技术面分析", "审查报告",
    "质疑审查报告", "质疑审查裁决", "决策报告", "四段闭环汇总"
]

# 五池名称
POOL_NAMES = ["快筛候选池", "重点观察池", "重点观察池_历史池", "边缘池", "持仓池", "S级操作池"]

# 池容量阈值
POOL_CAPACITY = {
    "快筛候选池": 20,
    "重点观察池": 20,
    "重点观察池_历史池": 200,
    "边缘池": 30,
    "持仓池": None,
    "S级操作池": 3,
}

# 必填字段（使用实际池中的字段名）
REQUIRED_FIELDS = {
    "快筛候选池": ["代码", "名称", "纳入日期"],
    "重点观察池": ["代码", "名称", "综合分", "纳入日期"],
    "S级操作池": ["代码", "名称", "综合评分", "纳入日期"],
    "边缘池": ["代码", "名称", "综合分", "降级时间"],
    "持仓池": ["代码", "名称", "持仓数量", "成本价"],
}

# 字段别名映射（一致性检测）
FIELD_ALIASES = {
    "代码": ["代码", "code", "stock_code", "股票代码", "symbol"],
    "名称": ["名称", "name", "stock_name", "股票名称"],
    "综合分": ["综合分", "综合评分", "评分", "score", "composite_score", "tech_score"],
    "综合评分": ["综合评分", "综合分", "composite_score", "评分"],
    "纳入日期": ["纳入日期", "日期", "entry_date", "add_date", "降级时间"],
}


def _get_stock_code(stock: dict) -> str:
    """从池条目中获取代码，支持多别名"""
    for key in ["代码", "股票代码", "code", "stock_code", "symbol"]:
        v = stock.get(key)
        if v:
            return str(v)
    return "?"


def _get_stock_name(stock: dict) -> str:
    """从池条目中获取名称，支持多别名"""
    for key in ["名称", "股票名称", "name", "stock_name"]:
        v = stock.get(key)
        if v:
            return str(v)
    return "?"


def _get_score(stock: dict) -> float | None:
    """从池条目中获取评分，支持多别名"""
    for key in ["综合分", "综合评分", "评分", "score", "composite_score", "tech_score"]:
        v = stock.get(key)
        if v is not None:
            try:
                return float(v)
            except (ValueError, TypeError):
                pass
    return None


def _get_price(stock: dict) -> float | None:
    """从池条目中获取价格"""
    for key in ["最新价", "今日收盘", "现价", "current_price", "price", "入场价", "建仓价", "成本价"]:
        v = stock.get(key)
        if v is not None:
            try:
                return float(v)
            except (ValueError, TypeError):
                pass
    return None


def _get_change_pct(stock: dict) -> float | None:
    """获取涨跌幅"""
    for key in ["涨跌幅", "今日涨跌", "change_pct", "change_percent"]:
        v = stock.get(key)
        if v is not None:
            try:
                s = str(v).replace("%", "").replace("+", "")
                return float(s)
            except (ValueError, TypeError):
                pass
    return None


def _get_turnover(stock: dict) -> float | None:
    """获取换手率"""
    for key in ["换手率", "turnover", "turnover_rate"]:
        v = stock.get(key)
        if v is not None:
            try:
                s = str(v).replace("%", "")
                return float(s)
            except (ValueError, TypeError):
                pass
    return None


def _get_update_time(stock: dict) -> str:
    """获取更新时间"""
    for key in ["更新时间", "update_time", "更新日期"]:
        v = stock.get(key)
        if v and isinstance(v, str):
            return v
    return ""


def _safe_parse_date(s: str) -> datetime.date | None:
    """安全解析日期字符串，支持多种格式"""
    if not s:
        return None
    s = s.strip()
    # 尝试 YYYY-MM-DD
    try:
        if len(s) >= 10:
            return datetime.datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        pass
    # 尝试 YYYY/MM/DD
    try:
        if len(s) >= 10:
            return datetime.datetime.strptime(s[:10].replace("/", "-"), "%Y-%m-%d").date()
    except ValueError:
        pass
    return None


def check_json_file(filepath: str) -> tuple[bool, any, str]:
    """检查JSON文件是否可解析，返回(是否成功, 数据, 错误信息)"""
    try:
        with open(filepath, encoding="utf-8") as f:
            data = json.load(f)
        return True, data, ""
    except json.JSONDecodeError as e:
        return False, None, f"JSON解析失败: {e}"
    except FileNotFoundError:
        return False, None, "文件不存在"
    except Exception as e:
        return False, None, str(e)


def check_pool_integrity(pool_name: str, data: dict) -> list[dict]:
    """检查池的完整性"""
    alerts = []
    stocks = data.get("stocks", [])
    required = REQUIRED_FIELDS.get(pool_name, [])

    for stock in stocks:
        code = _get_stock_code(stock)
        name = _get_stock_name(stock)
        for field in required:
            # 通过别名查找
            aliases = FIELD_ALIASES.get(field, [field])
            found = any(stock.get(a) is not None for a in aliases)
            if not found:
                alerts.append({
                    "level": "warning", "check": "完整性-必填字段",
                    "detail": f"{pool_name}/{code} {name}: 缺少必填字段 '{field}'"
                })

    # 统计信息检查
    stats = data.get("统计", {})
    if stats:
        count = stats.get("持仓数", stats.get("count", -1))
        if count >= 0 and count != len(stocks):
            alerts.append({
                "level": "warning", "check": "完整性-统计不一致",
                "detail": f"{pool_name}: 统计数({count})与实际数({len(stocks)})不一致"
            })

    return alerts


def check_price_anomaly(pool_name: str, data: dict) -> list[dict]:
    """DET-04/05/06: 检查价格/涨跌幅/换手率异常"""
    alerts = []
    stocks = data.get("stocks", [])
    for stock in stocks:
        code = _get_stock_code(stock)
        name = _get_stock_name(stock)

        # DET-04: 价格异常
        price = _get_price(stock)
        if price is not None:
            if price <= 0:
                alerts.append({
                    "level": "warning", "check": "准确性-价格异常",
                    "detail": f"{pool_name}/{code} {name}: 价格<=0 ({price})"
                })
            elif price > 10000:
                alerts.append({
                    "level": "warning", "check": "准确性-价格异常",
                    "detail": f"{pool_name}/{code} {name}: 价格异常高 ({price})"
                })

        # DET-05: 涨跌幅越界
        change_pct = _get_change_pct(stock)
        if change_pct is not None:
            if abs(change_pct) > 20:
                alerts.append({
                    "level": "warning", "check": "准确性-涨跌幅异常",
                    "detail": f"{pool_name}/{code} {name}: 涨跌幅={change_pct}%"
                })

        # DET-06: 换手率异常
        turnover = _get_turnover(stock)
        if turnover is not None:
            if turnover < 0:
                alerts.append({
                    "level": "warning", "check": "准确性-换手率异常",
                    "detail": f"{pool_name}/{code} {name}: 换手率为负 ({turnover})"
                })
            elif turnover > 50:
                alerts.append({
                    "level": "warning", "check": "准确性-换手率异常",
                    "detail": f"{pool_name}/{code} {name}: 换手率过高 ({turnover}%)"
                })

    return alerts


def check_score_accuracy(pool_name: str, data: dict) -> list[dict]:
    """检查评分准确性"""
    alerts = []
    stocks = data.get("stocks", [])

    for stock in stocks:
        code = _get_stock_code(stock)
        name = _get_stock_name(stock)
        score = _get_score(stock)
        entry_date = stock.get("纳入日期", stock.get("降级时间", ""))

        if score is not None:
            # DET-07: 评分越界
            if score < 0 or score > 100:
                alerts.append({
                    "level": "critical", "check": "准确性-评分越界",
                    "detail": f"{pool_name}/{code} {name}: 评分{score}不在0-100范围"
                })
            # DET-02: score=0 且入池>3天
            if score == 0 and entry_date:
                dt = _safe_parse_date(entry_date)
                if dt:
                    days = (TODAY - dt).days
                    if days >= 3:
                        alerts.append({
                            "level": "critical", "check": "准确性-score=0",
                            "detail": f"{pool_name}/{code} {name}: score=0已{days}天，应降级"
                        })
        else:
            # 评分缺失
            if pool_name in ("重点观察池", "快筛候选池", "S级操作池"):
                alerts.append({
                    "level": "info", "check": "完整性-评分缺失",
                    "detail": f"{pool_name}/{code} {name}: 缺少评分字段"
                })

    return alerts


def check_duplicates(pool_name: str, data: dict, all_codes: dict) -> list[dict]:
    """检查重复标的（DET-08: 跨池重复）"""
    alerts = []
    stocks = data.get("stocks", [])
    seen = set()

    for stock in stocks:
        code = _get_stock_code(stock)
        if not code:
            continue
        # 池内重复
        if code in seen:
            alerts.append({
                "level": "warning", "check": "一致性-池内重复",
                "detail": f"{pool_name}: {code} 存在重复条目"
            })
        seen.add(code)

        # 跨池重复（排除历史池）
        if code in all_codes and pool_name not in ("重点观察池_历史池",):
            prev_pool = all_codes[code]
            if prev_pool != pool_name:
                alerts.append({
                    "level": "warning", "check": "一致性-跨池重复",
                    "detail": f"{code}: 同时存在于 {prev_pool} 和 {pool_name}"
                })
        else:
            all_codes[code] = pool_name

    return alerts


def check_cross_pool_score_consistency(pool_name: str, data: dict, all_scores: dict) -> list[dict]:
    """DET-17: 检查跨池评分一致性"""
    alerts = []
    stocks = data.get("stocks", [])
    for stock in stocks:
        code = _get_stock_code(stock)
        score = _get_score(stock)
        if score is not None and code:
            if code in all_scores:
                prev_pool, prev_score = all_scores[code]
                if prev_pool != pool_name and abs(prev_score - score) > 10:
                    alerts.append({
                        "level": "info", "check": "一致性-评分不一致",
                        "detail": f"{code}: {prev_pool}({prev_score}分) vs {pool_name}({score}分) 差{abs(prev_score - score):.0f}分"
                    })
            else:
                all_scores[code] = (pool_name, score)
    return alerts


def check_timeliness(pool_name: str, data: dict) -> list[dict]:
    """检查数据及时性（DET-09: 池数据过期检测）"""
    alerts = []
    stats = data.get("统计", {})
    update_date_str = stats.get("更新日期", "")
    if not update_date_str:
        # 从个股更新时间取最大值
        stocks = data.get("stocks", [])
        max_time = ""
        for s in stocks:
            ut = _get_update_time(s)
            if ut and ut > max_time:
                max_time = ut
        if max_time:
            update_date_str = max_time

    if update_date_str:
        dt = _safe_parse_date(update_date_str)
        if dt:
            days_diff = (TODAY - dt).days
            if days_diff > 1:
                level = "warning" if days_diff <= 3 else "critical"
                alerts.append({
                    "level": level, "check": "及时性-数据陈旧",
                    "detail": f"{pool_name}: 最后更新于{update_date_str[:10]}，已{days_diff}天未更新"
                })
    else:
        alerts.append({
            "level": "info", "check": "及时性-缺少更新日期",
            "detail": f"{pool_name}: 缺少更新日期"
        })

    return alerts


def check_realtime_freshness(pool_name: str, data: dict) -> list[dict]:
    """DET-10: 盘中实时数据过期检测（盘中>15min，盘后>24h）"""
    if not IS_TRADING_HOURS and pool_name in ("持仓池", "重点观察池_历史池"):
        return []  # 非交易时段不检查实时性
    alerts = []
    stocks = data.get("stocks", [])
    for stock in stocks:
        code = _get_stock_code(stock)
        name = _get_stock_name(stock)
        ut = _get_update_time(stock)
        if not ut:
            continue
        try:
            ut_dt = datetime.datetime.strptime(ut, "%Y-%m-%d %H:%M")
            delta_min = (NOW - ut_dt).total_seconds() / 60
            if IS_TRADING_HOURS and delta_min > 15:
                alerts.append({
                    "level": "warning", "check": "及时性-盘中数据过期",
                    "detail": f"{pool_name}/{code} {name}: 数据已{delta_min:.0f}分钟未更新 (盘中>15min)"
                })
            elif not IS_TRADING_HOURS and delta_min > 1440:  # 24h
                alerts.append({
                    "level": "info", "check": "及时性-盘后数据陈旧",
                    "detail": f"{pool_name}/{code} {name}: 数据已{delta_min/60:.0f}小时未更新"
                })
        except ValueError:
            pass
    return alerts


def check_code_validity(pool_name: str, data: dict) -> list[dict]:
    """DET-03: 检查股票代码有效性"""
    alerts = []
    stocks = data.get("stocks", [])
    for stock in stocks:
        code = _get_stock_code(stock)
        name = _get_stock_name(stock)
        if code and not re.match(r"^\d{6}$", str(code)):
            alerts.append({
                "level": "warning", "check": "有效性-代码格式",
                "detail": f"{pool_name}/{code} {name}: 代码格式无效（非6位数字）"
            })
    return alerts


def check_date_format(pool_name: str, data: dict) -> list[dict]:
    """DET-15: 检查日期格式合法性"""
    alerts = []
    stocks = data.get("stocks", [])
    date_fields = ["纳入日期", "降级时间", "更新时间", "建仓日期", "卖出日期"]
    for stock in stocks:
        code = _get_stock_code(stock)
        name = _get_stock_name(stock)
        for field in date_fields:
            val = stock.get(field, "")
            if val and isinstance(val, str):
                if not _safe_parse_date(val):
                    alerts.append({
                        "level": "warning", "check": "有效性-日期格式",
                        "detail": f"{pool_name}/{code} {name}: {field}格式异常: {val[:20]}"
                    })
    return alerts


def check_field_naming_consistency(pool_name: str, data: dict) -> list[dict]:
    """DET-18: 检查字段命名一致性（综合分/综合评分混用）"""
    alerts = []
    stocks = data.get("stocks", [])
    score_field_names = {"综合分", "综合评分", "评分", "score"}
    found = set()
    for stock in stocks:
        for k in stock:
            if k in score_field_names:
                found.add(k)
    if len(found) > 1:
        # 混用告警
        sample_stock = stocks[0] if stocks else {}
        used = [k for k in sample_stock if k in score_field_names]
        alerts.append({
            "level": "info", "check": "一致性-字段命名",
            "detail": f"{pool_name}: 评分字段混用 {found}，最新使用: {used}"
        })
    return alerts


def check_pool_capacity(pool_name: str, data: dict) -> list[dict]:
    """DET-16: 检查池容量超限"""
    alerts = []
    stocks = data.get("stocks", [])
    cap = POOL_CAPACITY.get(pool_name)
    if cap is not None and len(stocks) > cap:
        alerts.append({
            "level": "warning", "check": "完整性-容量超限",
            "detail": f"{pool_name}: 当前{len(stocks)}只，容量上限{cap}只"
        })
    return alerts


def check_weight_config() -> list[dict]:
    """DET-14: 检查权重配置合法性"""
    alerts = []
    config_file = DATA_DIR / "权重配置.json"
    if not config_file.exists():
        return [{"level": "info", "check": "完整性-配置缺失", "detail": "权重配置文件不存在"}]
    ok, data, err = check_json_file(str(config_file))
    if not ok:
        return [{"level": "warning", "check": "完整性-配置解析失败", "detail": f"权重配置: {err}"}]
    # 检查四维权重
    weights = {}
    for key in ["驱动验证", "位置分析", "量能判断", "风险扫描"]:
        if key in data:
            weights[key] = float(data[key])
    if weights:
        total = sum(weights.values())
        if abs(total - 100) > 0.1:
            alerts.append({
                "level": "warning", "check": "准确性-权重配置",
                "detail": f"四维权重之和={total}，应为100: {weights}"
            })
    return alerts


def check_decision_log() -> list[dict]:
    """检查决策日志完整性（DET-13）"""
    alerts = []
    log_file = DATA_DIR / "decision_log.json"
    if not log_file.exists():
        return [{"level": "critical", "check": "完整性-文件缺失", "detail": "决策日志文件不存在"}]

    ok, data, err = check_json_file(str(log_file))
    if not ok:
        return [{"level": "critical", "check": "完整性-解析失败", "detail": f"决策日志: {err}"}]

    if isinstance(data, list):
        count = len(data)
        if count == 0:
            alerts.append({"level": "critical", "check": "完整性-空日志", "detail": "决策日志为空"})

        # 检查是否包含今日记录
        today_str = TODAY.isoformat()
        today_records = [r for r in data if r.get("date") == today_str]
        if not today_records:
            alerts.append({"level": "info", "check": "及时性-无今日记录", "detail": "决策日志无今日记录"})

        # 检查关键字段缺失率 (DET-13)
        key_fields = ["code", "name", "recommendation", "drive_type", "date"]
        field_missing = {f: 0 for f in key_fields}
        for r in data:
            for f in key_fields:
                if not r.get(f):
                    field_missing[f] += 1
        for f, missing in field_missing.items():
            rate = missing / count * 100 if count > 0 else 0
            if rate > 10:
                alerts.append({
                    "level": "info", "check": "完整性-字段缺失",
                    "detail": f"决策日志.{f} 缺失率: {rate:.0f}% ({missing}/{count})"
                })
    else:
        alerts.append({"level": "warning", "check": "完整性-格式异常", "detail": "决策日志格式非列表"})

    return alerts


def check_daily_reports() -> list[dict]:
    """检查当日报告是否完整（DET-11）"""
    alerts = []
    history_dir = DATA_DIR / "历史记录"
    if not history_dir.exists():
        return [{"level": "critical", "check": "完整性-目录缺失", "detail": "历史记录目录不存在"}]

    today_str = TODAY.isoformat()
    existing_reports = [f.name for f in history_dir.iterdir() if today_str in f.name]

    for rtype in REPORT_TYPES:
        if not any(rtype in f for f in existing_reports):
            alerts.append({"level": "info", "check": "完整性-报告缺失", "detail": f"今日未生成 {rtype}"})

    return alerts


def check_api_connectivity() -> list[dict]:
    """DET-19: 检查外部API连通性"""
    alerts = []
    import urllib.request
    try:
        req = urllib.request.Request("https://qt.gtimg.cn/q=sh000001",
                                     headers={"User-Agent": "Mozilla/5.0"},
                                     method="GET")
        resp = urllib.request.urlopen(req, timeout=5)
        body = resp.read().decode("gbk", errors="replace")
        if "sh000001" not in body:
            alerts.append({"level": "warning", "check": "可用性-API异常",
                           "detail": "腾讯行情API返回格式异常"})
    except Exception as e:
        alerts.append({"level": "warning", "check": "可用性-API超时",
                       "detail": f"腾讯行情API不可达: {type(e).__name__}"})
    return alerts


def check_rights_adjustment(pool_name: str, data: dict) -> list[dict]:
    """DET-21: 检查K线数据复权类型标识"""
    alerts = []
    stocks = data.get("stocks", [])
    for stock in stocks:
        code = _get_stock_code(stock)
        name = _get_stock_name(stock)
        has_adjust_tag = "复权类型" in stock or "adjust_type" in stock or "fq_type" in stock
        if not has_adjust_tag and pool_name in ("持仓池", "S级操作池"):
            alerts.append({
                "level": "info", "check": "有效性-复权标识",
                "detail": f"{pool_name}/{code} {name}: 缺少复权类型标识"
            })
    return alerts


def check_financial_data(pool_name: str, data: dict) -> list[dict]:
    """DET-22: 检查基本面/财务数据异常"""
    alerts = []
    stocks = data.get("stocks", [])
    for stock in stocks:
        code = _get_stock_code(stock)
        name = _get_stock_name(stock)
        for pe_key in ["PE", "市盈率", "pe", "pe_ttm"]:
            pe_val = stock.get(pe_key)
            if pe_val is not None:
                try:
                    pe = float(pe_val)
                    if pe < 0:
                        alerts.append({"level": "info", "check": "准确性-PE异常",
                                       "detail": f"{pool_name}/{code} {name}: PE为负值({pe})"})
                    elif pe > 500:
                        alerts.append({"level": "info", "check": "准确性-PE异常",
                                       "detail": f"{pool_name}/{code} {name}: PE过高({pe})"})
                except (ValueError, TypeError):
                    pass
                break
        for mkt_key in ["流通市值_亿", "流通市值", "market_cap", "总市值"]:
            mkt_val = stock.get(mkt_key)
            if mkt_val is not None:
                try:
                    mkt = float(mkt_val)
                    if mkt <= 0:
                        alerts.append({"level": "warning", "check": "准确性-市值异常",
                                       "detail": f"{pool_name}/{code} {name}: 流通市值<=0 ({mkt})"})
                except (ValueError, TypeError):
                    pass
                break
    return alerts


def trigger_signal_regeneration(fixes: list, pool_name: str, fix_type: str):
    """信号重生成：修复后标记需要重算的信号"""
    if not fixes:
        return
    today = NOW.strftime("%Y-%m-%d")
    signal_file = DATA_DIR / "dq" / f"signal_regen_{today}.jsonl"
    signal_file.parent.mkdir(parents=True, exist_ok=True)
    with open(signal_file, "a", encoding="utf-8") as f:
        for fix in fixes:
            signal = {
                "time": NOW.strftime("%Y-%m-%d %H:%M:%S"),
                "pool": pool_name,
                "target": fix.get("target", "?"),
                "fix_type": fix_type,
                "before": fix.get("before", ""),
                "after": fix.get("after", ""),
                "needs_recalculation": True
            }
            f.write(json.dumps(signal, ensure_ascii=False) + "\n")


def fix_score_zero(pool_name: str, data: dict, dry_run: bool = False) -> tuple[dict, list[dict]]:
    """自愈：score=0且入池>3天的标的，强制降级到边缘池"""
    fixes = []
    stocks = data.get("stocks", [])
    remaining = []
    demoted = []

    for stock in stocks:
        code = _get_stock_code(stock)
        name = _get_stock_name(stock)
        score = _get_score(stock)
        entry_date = stock.get("纳入日期", stock.get("降级时间", ""))

        should_demote = False
        days = 0
        if score is not None:
            try:
                if float(score) == 0 and entry_date:
                    dt = _safe_parse_date(entry_date)
                    if dt:
                        days = (TODAY - dt).days
                        if days >= 3:
                            should_demote = True
            except (ValueError, TypeError):
                pass

        if should_demote:
            demoted.append(stock)
            fixes.append({
                "time": NOW.strftime("%Y-%m-%d %H:%M:%S"),
                "type": "score=0降级",
                "target": f"{pool_name}/{code} {name}",
                "before": f"评分=0, 入池{days}天",
                "after": "降级到边缘池"
            })
        else:
            remaining.append(stock)

    if not dry_run and demoted:
        # 更新原池
        data["stocks"] = remaining
        if "统计" in data:
            data["统计"]["持仓数"] = len(remaining)
            data["统计"]["更新日期"] = NOW.strftime("%Y-%m-%d %H:%M:%S")

        # 写入边缘池
        edge_pool_file = POOLS_DIR / "边缘池.json"
        if edge_pool_file.exists():
            ok, edge_data, _ = check_json_file(str(edge_pool_file))
            if ok:
                edge_stocks = edge_data.get("stocks", [])
                for item in demoted:
                    # 确保有降级时间
                    item["降级时间"] = TODAY.isoformat()
                    edge_stocks.append(item)
                edge_data["stocks"] = edge_stocks
                if "统计" in edge_data:
                    edge_data["统计"]["持仓数"] = len(edge_stocks)
                    edge_data["统计"]["更新日期"] = NOW.strftime("%Y-%m-%d %H:%M:%S")
                with open(edge_pool_file, "w", encoding="utf-8") as f:
                    json.dump(edge_data, f, ensure_ascii=False, indent=2)

        # 写回原池
        pool_file = POOLS_DIR / f"{pool_name}.json"
        with open(pool_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    return data, fixes


def fix_duplicates(pool_name: str, data: dict, dry_run: bool = False) -> tuple[dict, list[dict]]:
    """自愈：去重，保留最新"""
    fixes = []
    stocks = data.get("stocks", [])
    seen = set()
    unique = []

    for stock in stocks:
        code = _get_stock_code(stock)
        if code in seen:
            name = _get_stock_name(stock)
            fixes.append({
                "time": NOW.strftime("%Y-%m-%d %H:%M:%S"),
                "type": "去重",
                "target": f"{pool_name}/{code} {name}",
                "before": "重复条目",
                "after": "已移除"
            })
            continue
        seen.add(code)
        unique.append(stock)

    if not dry_run and len(unique) < len(stocks):
        data["stocks"] = unique
        if "统计" in data:
            data["统计"]["持仓数"] = len(unique)
        pool_file = POOLS_DIR / f"{pool_name}.json"
        with open(pool_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    return data, fixes


def log_fixes(fixes: list[dict]):
    """记录修复日志"""
    if not fixes:
        return
    with open(FIX_LOG, "a", encoding="utf-8") as f:
        for fix in fixes:
            f.write(json.dumps(fix, ensure_ascii=False) + "\n")


def main():
    parser = argparse.ArgumentParser(description="天枢数据质量扫描 v2")
    parser.add_argument("--verbose", action="store_true", help="详细输出")
    parser.add_argument("--alert", action="store_true", help="告警输出")
    parser.add_argument("--fix", action="store_true", help="自动修复")
    parser.add_argument("--dry-run", action="store_true", help="预览修复")
    args = parser.parse_args()

    start = datetime.datetime.now()
    all_alerts = []
    all_fixes = []
    all_codes = {}
    all_scores = {}
    dim_results = {}
    total_checks = 0
    passed = 0
    warnings = 0
    criticals = 0

    # 1. 五池扫描
    for pool_name in POOL_NAMES:
        pool_file = POOLS_DIR / f"{pool_name}.json"
        if not pool_file.exists():
            all_alerts.append({"level": "critical", "check": "完整性-文件不存在", "detail": f"{pool_name}: 文件不存在"})
            criticals += 1
            continue

        ok, data, err = check_json_file(str(pool_file))
        if not ok:
            all_alerts.append({"level": "critical", "check": "完整性-解析失败", "detail": f"{pool_name}: {err}"})
            criticals += 1
            continue

        # 完整性检查
        for check_fn, check_name in [
            (check_pool_integrity, "完整性"),
            (check_score_accuracy, "准确性-评分"),
            (check_price_anomaly, "准确性-行情"),
            (check_duplicates, "一致性-重复"),
            (check_cross_pool_score_consistency, "一致性-评分"),
            (check_timeliness, "及时性"),
            (check_realtime_freshness, "及时性-实时"),
            (check_code_validity, "有效性-代码"),
            (check_date_format, "有效性-日期"),
            (check_field_naming_consistency, "一致性-字段命名"),
            (check_pool_capacity, "完整性-容量"),
            (check_financial_data, "准确性-财务"),
            (check_rights_adjustment, "有效性-复权"),
        ]:
            if check_name == "一致性-评分":
                alerts = check_fn(pool_name, data, all_scores)
            elif check_name == "一致性-重复":
                alerts = check_fn(pool_name, data, all_codes)
            else:
                alerts = check_fn(pool_name, data)
            all_alerts.extend(alerts)
            for a in alerts:
                if a["level"] == "critical":
                    criticals += 1
                elif a["level"] == "warning":
                    warnings += 1
            total_checks += 1

        # 自愈修复
        if args.fix or args.dry_run:
            data, score_fixes = fix_score_zero(pool_name, data, dry_run=args.dry_run)
            all_fixes.extend(score_fixes)
            data, dup_fixes = fix_duplicates(pool_name, data, dry_run=args.dry_run)
            all_fixes.extend(dup_fixes)
            # 信号重生成：修复后标记需要重算的信号
            if not args.dry_run:
                trigger_signal_regeneration(score_fixes, pool_name, "score=0降级")
                trigger_signal_regeneration(dup_fixes, pool_name, "去重")

    # 2. 决策日志检查
    log_alerts = check_decision_log()
    all_alerts.extend(log_alerts)
    for a in log_alerts:
        if a["level"] == "critical":
            criticals += 1
        elif a["level"] == "warning":
            warnings += 1
    total_checks += 1

    # 3. 报告检查
    report_alerts = check_daily_reports()
    all_alerts.extend(report_alerts)
    for a in report_alerts:
        if a["level"] == "warning":
            warnings += 1
    total_checks += 1

    # 4. 权重配置检查
    weight_alerts = check_weight_config()
    all_alerts.extend(weight_alerts)
    warnings += sum(1 for a in weight_alerts if a["level"] == "warning")
    total_checks += 1

    # 5. API连通性检查
    api_alerts = check_api_connectivity()
    all_alerts.extend(api_alerts)
    warnings += sum(1 for a in api_alerts if a["level"] == "warning")
    total_checks += 1

    # 记录修复日志
    if all_fixes:
        log_fixes(all_fixes)

    # 计算通过数
    passed = total_checks - warnings - criticals

    # 构建结果
    result = {
        "timestamp": NOW.strftime("%Y-%m-%d %H:%M:%S"),
        "status": "critical" if criticals > 0 else ("warning" if warnings > 0 else "healthy"),
        "exit_code": 2 if criticals > 0 else (1 if warnings > 0 else 0),
        "summary": {
            "total_checks": total_checks,
            "passed": passed,
            "warnings": warnings,
            "criticals": criticals,
            "fixed": len(all_fixes)
        },
        "alerts": all_alerts,
        "fixes": all_fixes
    }

    # 输出
    if args.verbose:
        print(f"📊 天枢数据质量扫描 v2 | {result['timestamp']}")
        print(f"状态: {'🔴' if criticals>0 else '🟡' if warnings>0 else '✅'} {result['status'].upper()}")
        print(f"检查: {total_checks}项 | ✅ {passed} | ⚠️ {warnings} | 🔴 {criticals} | 🔧 {len(all_fixes)}")
        if all_alerts:
            print(f"\n告警清单:")
            le_map = {"critical": "🔴", "warning": "🟡", "info": "ℹ️"}
            for a in all_alerts:
                le = le_map.get(a["level"], "❓")
                print(f"  {le} [{a['check']}] {a['detail']}")
        if all_fixes:
            print(f"\n修复记录:")
            for f in all_fixes:
                print(f"  🔧 [{f['type']}] {f['target']}: {f['before']} → {f['after']}")
    elif args.alert and all_alerts:
        le_map = {"critical": "🔴", "warning": "🟡", "info": "ℹ️"}
        for a in all_alerts:
            if a["level"] in ("critical", "warning"):
                le = le_map.get(a["level"], "❓")
                print(f"{le} [{a['check']}] {a['detail']}")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))

    # 保存结果
    result_file = DQ_DIR / f"dq_result_{TODAY.isoformat()}.json"
    with open(result_file, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    sys.exit(result["exit_code"])


if __name__ == "__main__":
    main()
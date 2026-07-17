#!/usr/bin/env python3
"""
天枢权衡 · A股交易合规管理器

对标监管规则：
- 上交所《交易规则》《程序化交易管理实施细则》
- 深交所《交易规则》《证券异常交易行为监控指引》
- 《证券法》第63条（举牌线）、第53条（内幕信息）
- 证监会《上市公司董事、监事和高级管理人员所持本公司股份及其变动管理规则》

设计原则：
1. 事前拦截 > 事中告警 > 事后留痕
2. 全量合规检查在交易执行前一次性完成
3. 合规日志独立存储，支持监管审计
"""

import json
import time
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Any
from collections import defaultdict

# ═══════════════════════════════════════════════════════════════
# 合规阈值配置
# ═══════════════════════════════════════════════════════════════

COMPLIANCE_CONFIG = {
    # C-001: 单日报单频率
    "max_daily_orders": 50,          # 单日最大报单次数（含撤单重报）
    "max_daily_cancel_ratio": 0.50,  # 单日最大撤单比例（撤单/报单）
    "max_orders_per_minute": 5,      # 每分钟最大报单次数

    # C-002: 频繁报撤单
    "freq_cancel_window": 300,       # 频繁撤单检测窗口（秒）
    "freq_cancel_threshold": 5,      # 窗口内撤单次数阈值

    # C-005: 举牌线
    "reporting_line_pct": 5.0,       # 5%举牌线
    "step_line_pct": 1.0,            # 每增减1%需公告

    # C-006: 流通股占比
    "max_float_pct": 4.99,           # 单票最大流通股占比（<5%免举牌）

    # C-010: 单票仓位
    "default_max_position_pct": 10.0, # 单票最大仓位（%总资金）
    "weak_market_max_position_pct": 5.0, # 弱市单票最大仓位
    "max_single_amount": 50000000,    # 单票最大金额（元）

    # C-011: 涨跌停申报价格
    "price_limit_pct": 10.0,         # 主板涨跌幅限制
    "st_price_limit_pct": 5.0,       # ST/*ST涨跌幅限制
    "kcb_price_limit_pct": 20.0,     # 科创板涨跌幅限制
    "cyb_price_limit_pct": 20.0,     # 创业板涨跌幅限制

    # C-013: T+1
    "t_plus_1_hold_days": 1,         # T+1最小持有天数

    # C-016: 敏感期
    "pre_report_days": 30,           # 定期报告前30天
    "pre_forecast_days": 10,         # 业绩预告前10天
}

# 受限标的池 — 可扩展，手动维护
RESTRICTED_STOCKS: Set[str] = set()  # 添加被限制交易的代码
ST_STOCKS: Set[str] = set()          # ST/*ST 标的（自动更新）
INSIDER_STOCKS: Set[str] = set()     # 内幕信息相关标的（手动维护）
BLACKLIST_STOCKS: Set[str] = set()   # 黑名单标的（不接受推荐）


# ═══════════════════════════════════════════════════════════════
# 合规日志
# ═══════════════════════════════════════════════════════════════

class ComplianceLogger:
    """合规审计日志 — C-018: 合规日志"""

    _instance = None

    def __new__(cls, log_dir: Optional[Path] = None):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, log_dir: Optional[Path] = None):
        if hasattr(self, '_initialized'):
            return
        self._initialized = True
        if log_dir is None:
            log_dir = Path.home() / "hermes-data" / "tianshu-quanheng" / "data" / "compliance"
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._today = datetime.now().strftime("%Y-%m-%d")
        self._log: List[Dict] = []

    def log(self, event_type: str, level: str, code: str, name: str,
            rule_id: str, detail: str, action: str = "block"):
        """记录合规事件"""
        entry = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f"),
            "type": event_type,
            "level": level,
            "code": code,
            "name": name,
            "rule": rule_id,
            "detail": detail,
            "action": action,
        }
        self._log.append(entry)
        # 同步写入日志文件
        log_file = self.log_dir / f"compliance_{self._today}.jsonl"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def get_today_log(self) -> List[Dict]:
        return self._log

    def get_alerts(self, min_level: str = "high") -> List[Dict]:
        levels = {"high": ["🔴 红线"], "medium": ["🔴 红线", "🟡 重要"], "low": ["🔴 红线", "🟡 重要", "🟢 低"]}
        return [e for e in self._log if e["level"] in levels.get(min_level, levels["medium"])]

    def summary(self) -> Dict:
        """合规日结摘要"""
        blocked = sum(1 for e in self._log if e["action"] == "block")
        warned = sum(1 for e in self._log if e["action"] == "warn")
        high = sum(1 for e in self._log if e["level"] == "🔴 红线")
        return {
            "date": self._today,
            "total_events": len(self._log),
            "blocked": blocked,
            "warned": warned,
            "high_risk": high,
        }


# ═══════════════════════════════════════════════════════════════
# 合规检查引擎
# ═══════════════════════════════════════════════════════════════

class ComplianceChecker:
    """合规规则引擎 — 事前拦截 + 事中告警"""

    def __init__(self):
        self.logger = ComplianceLogger()
        self._daily_order_count = 0
        self._daily_cancel_count = 0
        self._order_timeline: List[float] = []
        self._cancel_timeline: List[float] = []
        self._daily_positions: Dict[str, Dict] = {}  # {code: {qty, amount, pct}}

    # ═══════════════════════════════════════════════════════
    # C-007: ST/*ST/退市标的过滤
    # ═══════════════════════════════════════════════════════

    def check_st_stock(self, code: str, name: str) -> Tuple[bool, str]:
        """检查标的是否为ST/*ST或退市整理期"""
        if code in ST_STOCKS:
            return False, f"ST标的禁止交易: {name}({code})"
        if "ST" in name or "*ST" in name:
            ST_STOCKS.add(code)
            return False, f"ST标的禁止交易: {name}({code})"
        # 退市整理期标的（代码以400/420开头）
        if code.startswith(("400", "420")):
            return False, f"退市整理期标的禁止交易: {name}({code})"
        return True, ""

    # ═══════════════════════════════════════════════════════
    # C-011: 涨跌停价格申报限制
    # ═══════════════════════════════════════════════════════

    def check_price_limit(self, code: str, name: str, prev_close: float,
                          bid_price: float, is_st: bool = False,
                          is_kcb: bool = False, is_cyb: bool = False) -> Tuple[bool, str]:
        """检查申报价格是否超出涨跌停范围"""
        if is_st:
            limit = COMPLIANCE_CONFIG["st_price_limit_pct"]
        elif is_kcb or is_cyb:
            limit = COMPLIANCE_CONFIG["kcb_price_limit_pct"]
        else:
            limit = COMPLIANCE_CONFIG["price_limit_pct"]

        limit_range = prev_close * limit / 100
        upper_limit = prev_close + limit_range
        lower_limit = prev_close - limit_range

        if bid_price > upper_limit:
            return False, f"买入价{bid_price:.2f}超过涨停价{upper_limit:.2f}({name}({code}), 前收{prev_close})"
        if bid_price < lower_limit:
            return False, f"卖出价{bid_price:.2f}低于跌停价{lower_limit:.2f}({name}({code}), 前收{prev_close})"
        return True, ""

    # ═══════════════════════════════════════════════════════
    # C-013: T+1 交易规则
    # ═══════════════════════════════════════════════════════

    def check_t_plus_1(self, code: str, name: str, buy_date: Optional[str],
                       today: str) -> Tuple[bool, str]:
        """检查T+1 — 当日买入标的当日不得卖出"""
        if buy_date and buy_date == today:
            return False, f"T+1禁止: {name}({code}) 当日买入当日卖出（买入日{buy_date}）"
        return True, ""

    # ═══════════════════════════════════════════════════════
    # C-001 + C-002: 报单频率/撤单比例
    # ═══════════════════════════════════════════════════════

    def check_order_frequency(self) -> Tuple[bool, str]:
        """检查报单频率"""
        now = time.time()

        # 日累计报单次数
        if self._daily_order_count >= COMPLIANCE_CONFIG["max_daily_orders"]:
            return False, f"单日报单次数超限: {self._daily_order_count}/{COMPLIANCE_CONFIG['max_daily_orders']}"

        # 每分钟报单频率
        recent = [t for t in self._order_timeline if now - t < 60]
        if len(recent) >= COMPLIANCE_CONFIG["max_orders_per_minute"]:
            return False, f"每分钟报单频率超限: {len(recent)}/{COMPLIANCE_CONFIG['max_orders_per_minute']}"

        # 撤单比例
        if self._daily_order_count > 0:
            cancel_ratio = self._daily_cancel_count / self._daily_order_count
            if cancel_ratio >= COMPLIANCE_CONFIG["max_daily_cancel_ratio"]:
                return False, f"撤单比例超限: {cancel_ratio:.0%}/{COMPLIANCE_CONFIG['max_daily_cancel_ratio']:.0%}"

        # 频繁撤单检测
        recent_cancels = [t for t in self._cancel_timeline if now - t < COMPLIANCE_CONFIG["freq_cancel_window"]]
        if len(recent_cancels) >= COMPLIANCE_CONFIG["freq_cancel_threshold"]:
            return False, f"频繁撤单: {len(recent_cancels)}次/{COMPLIANCE_CONFIG['freq_cancel_window']}秒"

        return True, ""

    def record_order(self, is_cancel: bool = False):
        """记录报单/撤单行为"""
        self._daily_order_count += 1
        self._order_timeline.append(time.time())
        if is_cancel:
            self._daily_cancel_count += 1
            self._cancel_timeline.append(time.time())

    # ═══════════════════════════════════════════════════════
    # C-005 + C-006: 持仓比例/举牌线
    # ═══════════════════════════════════════════════════════

    def check_position_limit(self, code: str, name: str, buy_amount: float,
                              total_capital: float, current_position_pct: float,
                              current_float_pct: float = 0,
                              market_state: str = "震荡") -> Tuple[bool, str]:
        """检查持仓合规"""
        # 单票仓位百分比
        max_pos = (COMPLIANCE_CONFIG["weak_market_max_position_pct"]
                   if market_state in ("偏空", "震荡偏弱")
                   else COMPLIANCE_CONFIG["default_max_position_pct"])
        buy_pct = buy_amount / total_capital * 100 if total_capital > 0 else 0
        new_pct = current_position_pct + buy_pct

        if new_pct > max_pos:
            return False, f"单票仓位超限: {name}({code}) 当前{current_position_pct:.1f}%+买入{buy_pct:.1f}%={new_pct:.1f}%>{max_pos}%"

        # 单票金额限制
        if buy_amount > COMPLIANCE_CONFIG["max_single_amount"]:
            return False, f"单票金额超限: {name}({code}) 买入{buy_amount:.0f}>{COMPLIANCE_CONFIG['max_single_amount']:.0f}"

        # 举牌线检查
        new_float_pct = current_float_pct + (buy_amount / total_capital * 100)
        if new_float_pct >= COMPLIANCE_CONFIG["reporting_line_pct"]:
            return False, f"举牌线预警: {name}({code}) 拟持仓{new_float_pct:.2f}% ≥5%举牌线，需披露"

        return True, ""

    # ═══════════════════════════════════════════════════════
    # C-015 + C-016: 内幕信息/敏感期
    # ═══════════════════════════════════════════════════════

    def check_info_sensitive(self, code: str, name: str) -> Tuple[bool, str]:
        """检查内幕信息/敏感期"""
        if code in INSIDER_STOCKS:
            return False, f"内幕信息标的: {name}({code}) 在敏感期内禁止交易"
        if code in BLACKLIST_STOCKS:
            return False, f"黑名单标的: {name}({code}) 禁止交易"
        return True, ""

    # ═══════════════════════════════════════════════════════
    # C-020: 合规白名单
    # ═══════════════════════════════════════════════════════

    def check_whitelist(self, code: str, name: str, whitelist: Optional[Set[str]] = None) -> Tuple[bool, str]:
        """检查标的是否在可交易白名单中"""
        if whitelist is not None and code not in whitelist:
            return False, f"标的不在交易白名单: {name}({code})"
        return True, ""

    # ═══════════════════════════════════════════════════════
    # 全量合规检查
    # ═══════════════════════════════════════════════════════

    def check_all(self, code: str, name: str, buy_amount: float,
                  total_capital: float, prev_close: float, bid_price: float,
                  current_position_pct: float = 0, current_float_pct: float = 0,
                  buy_date: Optional[str] = None, today: Optional[str] = None,
                  is_st: bool = False, is_kcb: bool = False, is_cyb: bool = False,
                  market_state: str = "震荡", whitelist: Optional[Set[str]] = None) -> Tuple[bool, List[str]]:
        """
        全量合规检查 — 一次性前置拦截

        Returns:
            (通过, [失败原因列表])
        """
        today = today or datetime.now().strftime("%Y-%m-%d")
        failures = []

        # 按检查顺序：先高频低频，再标的级规则
        checks = [
            ("C-007", "ST/退市", self.check_st_stock(code, name)),
            ("C-015", "内幕信息", self.check_info_sensitive(code, name)),
            ("C-020", "白名单", self.check_whitelist(code, name, whitelist)),
            ("C-001", "报单频率", self.check_order_frequency()),
        ]

        for rule_id, rule_name, (passed, reason) in checks:
            if not passed:
                failures.append(f"[{rule_id}] {reason}")
                self.logger.log("pre_trade", "🔴 红线", code, name, rule_id, reason, "block")

        # 检查价格限制
        price_passed, price_reason = self.check_price_limit(code, name, prev_close, bid_price, is_st, is_kcb, is_cyb)
        if not price_passed:
            failures.append(f"[C-011] {price_reason}")
            self.logger.log("pre_trade", "🔴 红线", code, name, "C-011", price_reason, "block")

        # 检查T+1
        t1_passed, t1_reason = self.check_t_plus_1(code, name, buy_date, today)
        if not t1_passed:
            failures.append(f"[C-013] {t1_reason}")
            self.logger.log("pre_trade", "🔴 红线", code, name, "C-013", t1_reason, "block")

        # 检查持仓
        pos_passed, pos_reason = self.check_position_limit(code, name, buy_amount, total_capital,
                                                            current_position_pct, current_float_pct, market_state)
        if not pos_passed:
            failures.append(f"[C-005/006/010] {pos_reason}")
            self.logger.log("pre_trade", "🔴 红线", code, name, "C-005", pos_reason, "block")

        return len(failures) == 0, failures


# ═══════════════════════════════════════════════════════════════
# 单例
# ═══════════════════════════════════════════════════════════════

_checker_instance: Optional[ComplianceChecker] = None


def get_checker() -> ComplianceChecker:
    global _checker_instance
    if _checker_instance is None:
        _checker_instance = ComplianceChecker()
    return _checker_instance


def get_logger() -> ComplianceLogger:
    return get_checker().logger
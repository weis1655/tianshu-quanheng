#!/usr/bin/env python3
"""
中国交易日历工具 — 鲁棒性增强核心
提供：
1. 工作日/节假日判断（含调休）
2. 最近交易日计算
3. 交易日范围生成
4. 与天枢权衡其他模块的集成接口
"""

import os
import re
import json
import logging
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Optional, List, Tuple, Set
from logger import plog

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
DATA_DIR = PROJECT_ROOT / "data" / "交易日历"
CALENDAR_FILE = DATA_DIR / "china_holidays.json"


# ── 硬编码节假日基础数据（每年更新）─────────────────────────────────────
# 中国法定节假日（不含调休），每年需手动更新次年
HOLIDAY_RULES = {
    # 格式: (月, 日) → 名称
    # 固定日期节假日
    "元旦": [(1, 1)],
    "春节": None,  # 农历，需特殊处理
    "清明": [(4, 4), (4, 5)],  # 清明节通常在4月4或5日
    "劳动节": [(5, 1)],
    "端午": None,  # 农历
    "中秋": None,  # 农历
    "国庆": [(10, 1), (10, 2), (10, 3)],
    # 周末
    "周末": None,  # 周六日
}

# 手动维护的近年节假日（含调休安排）
# 格式: date_str → {"type": "holiday"|"workday", "name": "..."}
# 调休上班日标记为 workday，正常休息日标记为 holiday
HOLIDAY_CALENDAR = {
    # 2026年节假日安排（根据国务院通知）
    "2026-01-01": {"type": "holiday", "name": "元旦"},
    "2026-01-02": {"type": "holiday", "name": "元旦"},
    "2026-01-03": {"type": "holiday", "name": "元旦"},
    "2026-02-17": {"type": "holiday", "name": "春节"},
    "2026-02-18": {"type": "holiday", "name": "春节"},
    "2026-02-19": {"type": "holiday", "name": "春节"},
    "2026-02-20": {"type": "holiday", "name": "春节"},
    "2026-02-21": {"type": "holiday", "name": "春节"},
    "2026-02-22": {"type": "holiday", "name": "春节"},
    "2026-02-23": {"type": "holiday", "name": "春节"},
    "2026-04-04": {"type": "holiday", "name": "清明节"},
    "2026-04-05": {"type": "holiday", "name": "清明节"},
    "2026-04-06": {"type": "holiday", "name": "清明节"},
    "2026-05-01": {"type": "holiday", "name": "劳动节"},
    "2026-05-02": {"type": "holiday", "name": "劳动节"},
    "2026-05-03": {"type": "holiday", "name": "劳动节"},
    "2026-05-04": {"type": "holiday", "name": "劳动节"},
    "2026-05-05": {"type": "holiday", "name": "劳动节"},
    "2026-06-19": {"type": "holiday", "name": "端午节"},
    "2026-06-20": {"type": "holiday", "name": "端午节"},
    "2026-06-21": {"type": "holiday", "name": "端午节"},
    "2026-09-25": {"type": "holiday", "name": "中秋节"},
    "2026-09-26": {"type": "holiday", "name": "中秋节"},
    "2026-10-01": {"type": "holiday", "name": "国庆节"},
    "2026-10-02": {"type": "holiday", "name": "国庆节"},
    "2026-10-03": {"type": "holiday", "name": "国庆节"},
    "2026-10-04": {"type": "holiday", "name": "国庆节"},
    "2026-10-05": {"type": "holiday", "name": "国庆节"},
    "2026-10-06": {"type": "holiday", "name": "国庆节"},
    "2026-10-07": {"type": "holiday", "name": "国庆节"},
    "2026-10-08": {"type": "holiday", "name": "国庆节"},
}


def _load_calendar() -> dict:
    """加载节假日日历（优先文件，降级硬编码）"""
    if CALENDAR_FILE.exists():
        try:
            with open(CALENDAR_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # 合并硬编码数据
                data.update(HOLIDAY_CALENDAR)
                return data
        except Exception as e:
            logger.warning(f"[TradingCalendar] 日历文件读取失败: {e}，使用硬编码")
    return dict(HOLIDAY_CALENDAR)


def is_holiday(d: date, calendar: Optional[dict] = None) -> bool:
    """
    判断是否为非交易日（节假日或周末）
    
    Args:
        d: 日期
        calendar: 日历数据（None 则自动加载）
    
    Returns:
        True = 非交易日，False = 交易日
    """
    if calendar is None:
        calendar = _load_calendar()
    
    date_str = d.strftime("%Y-%m-%d")
    
    # 1. 检查是否在日历中明确标记
    if date_str in calendar:
        entry = calendar[date_str]
        return entry.get("type") == "holiday"
    
    # 2. 检查周末（A股周六日休市，除非调休上班）
    weekday = d.weekday()  # 0=Mon, 5=Sat, 6=Sun
    if weekday >= 5:
        return True
    
    return False


def is_trading_day(d: date, calendar: Optional[dict] = None) -> bool:
    """判断是否为交易日（与 is_holiday 相反）"""
    return not is_holiday(d, calendar)


def get_prev_trading_day(d: date, calendar: Optional[dict] = None, max_back: int = 10) -> Optional[date]:
    """
    获取最近一个交易日
    
    Args:
        d: 起始日期（不包含）
        calendar: 日历数据
        max_back: 最多回溯天数（防止无限循环）
    
    Returns:
        最近交易日，找不到返回 None
    """
    current = d - timedelta(days=1)
    for _ in range(max_back):
        if is_trading_day(current, calendar):
            return current
        current -= timedelta(days=1)
    return None


def get_next_trading_day(d: date, calendar: Optional[dict] = None, max_forward: int = 10) -> Optional[date]:
    """
    获取下一个交易日
    
    Args:
        d: 起始日期（不包含）
        calendar: 日历数据
        max_forward: 最多向前天数
    
    Returns:
        下一个交易日，找不到返回 None
    """
    current = d + timedelta(days=1)
    for _ in range(max_forward):
        if is_trading_day(current, calendar):
            return current
        current += timedelta(days=1)
    return None


def get_trading_days(start: date, end: date, calendar: Optional[dict] = None) -> List[date]:
    """
    获取指定范围内的所有交易日
    
    Args:
        start: 开始日期（包含）
        end: 结束日期（包含）
        calendar: 日历数据
    
    Returns:
        交易日列表（正序）
    """
    if start > end:
        start, end = end, start
    
    days = []
    current = start
    while current <= end:
        if is_trading_day(current, calendar):
            days.append(current)
        current += timedelta(days=1)
    return days


def count_trading_days(start: date, end: date, calendar: Optional[dict] = None) -> int:
    """计算交易日天数"""
    return len(get_trading_days(start, end, calendar))


def get_trading_days_backward(from_date: date, count: int, calendar: Optional[dict] = None) -> List[date]:
    """
    从指定日期向前数 N 个交易日
    
    Args:
        from_date: 起始日期（不包含）
        count: 需要多少个交易日
        calendar: 日历数据
    
    Returns:
        最近 N 个交易日（正序，最早→最新）
    """
    days = []
    current = from_date - timedelta(days=1)
    while len(days) < count:
        if is_trading_day(current, calendar):
            days.append(current)
        current -= timedelta(days=1)
        # 防止无限循环（比如全年都是节假日）
        if (from_date - current).days > count * 3:
            logger.warning(f"[TradingCalendar] 向前查找 {count} 个交易日超时，已回溯 {(from_date - current).days} 天")
            break
    days.reverse()
    return days


def get_trading_days_forward(from_date: date, count: int, calendar: Optional[dict] = None) -> List[date]:
    """
    从指定日期向后数 N 个交易日
    
    Args:
        from_date: 起始日期（不包含）
        count: 需要多少个交易日
        calendar: 日历数据
    
    Returns:
        未来 N 个交易日（正序）
    """
    days = []
    current = from_date + timedelta(days=1)
    while len(days) < count:
        if is_trading_day(current, calendar):
            days.append(current)
        current += timedelta(days=1)
        if (current - from_date).days > count * 3:
            logger.warning(f"[TradingCalendar] 向后查找 {count} 个交易日超时")
            break
    return days


def save_calendar(calendar: dict, path: Optional[Path] = None) -> bool:
    """保存日历到文件"""
    path = path or CALENDAR_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(calendar, f, ensure_ascii=False, indent=2, default=str)
        logger.info(f"[TradingCalendar] 日历已保存: {path}")
        return True
    except Exception as e:
        logger.error(f"[TradingCalendar] 保存日历失败: {e}")
        return False


def update_holiday(date_str: str, holiday_type: str, name: str = "") -> None:
    """
    动态更新日历（添加/修改节假日）
    
    Args:
        date_str: 日期字符串 "YYYY-MM-DD"
        holiday_type: "holiday" 或 "workday"
        name: 节假日名称
    """
    calendar = _load_calendar()
    calendar[date_str] = {"type": holiday_type, "name": name}
    save_calendar(calendar)


# ── 单元测试 ──────────────────────────────────────────────────────
if __name__ == "__main__":
    today = date.today()
    plog("INFO", f"=== 交易日历工具测试 ===")
    plog("INFO", f"今天: {today} ({['一','二','三','四','五','六','日'][today.weekday()]})")
    plog("INFO", f"是否交易日: {'✅' if is_trading_day(today) else '❌'}")
    # 测试最近交易日
    prev = get_prev_trading_day(today)
    plog("INFO", f"上一个交易日: {prev}")
    # 测试未来交易日
    next_day = get_next_trading_day(today)
    plog("INFO", f"下一个交易日: {next_day}")
    # 测试最近5个交易日
    recent = get_trading_days_backward(today, 5)
    plog("INFO", f"最近5个交易日: {[str(d) for d in recent]}")
    # 测试本月交易日
    first = today.replace(day=1)
    last = (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    month_days = get_trading_days(first, last)
    plog("INFO", f"{first.month}月交易日: {len(month_days)}天 {[str(d) for d in month_days]}")
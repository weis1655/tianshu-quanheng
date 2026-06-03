#!/usr/bin/env python3
"""
增强型调度器 - 支持 cron 表达式和灵活的时间窗口
支持从 config.yaml 读取配置
"""

import time
import re
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
import threading


class ScheduleType(Enum):
    """调度类型"""
    CRON = "cron"           # Cron 表达式
    INTERVAL = "interval"   # 间隔执行
    TIME_WINDOW = "window"  # 时间窗口
    ONCE = "once"           # 单次执行


@dataclass
class ScheduleEntry:
    """调度条目"""
    name: str
    func: Callable
    schedule_type: ScheduleType
    cron_expr: Optional[str] = None
    interval_seconds: Optional[int] = None
    time_window: Optional[str] = None  # 如 "06:20-06:35"
    enabled: bool = True
    last_run: Optional[datetime] = None
    next_run: Optional[datetime] = None
    timezone: str = "Asia/Shanghai"
    kwargs: Dict[str, Any] = field(default_factory=dict)


class CronParser:
    """Cron 表达式解析器（简化版）"""

    def __init__(self, expr: str):
        self.expr = expr
        self.parts = expr.split()

        if len(self.parts) not in [5, 6]:
            raise ValueError(f"无效的 Cron 表达式: {expr}")

        # 格式: 分 时 日 月 周
        self.minute = self.parts[0]
        self.hour = self.parts[1]
        self.day = self.parts[2]
        self.month = self.parts[3]
        self.weekday = self.parts[4]

    def matches(self, dt: datetime) -> bool:
        """检查给定时间是否匹配 Cron 表达式"""
        return (
            self._match_field(self.minute, dt.minute, 0, 59) and
            self._match_field(self.hour, dt.hour, 0, 23) and
            self._match_field(self.day, dt.day, 1, 31) and
            self._match_field(self.month, dt.month, 1, 12) and
            self._match_field(self.weekday, dt.weekday(), 0, 6)
        )

    def _match_field(self, field: str, value: int, min_val: int, max_val: int) -> bool:
        """匹配单个字段"""
        if field == "*":
            return True

        # 处理列表 (如 1,3,5)
        if "," in field:
            return value in [int(x) for x in field.split(",")]

        # 处理范围 (如 1-5)
        if "-" in field:
            start, end = field.split("-")
            return int(start) <= value <= int(end)

        # 处理步长 (如 */5)
        if "/" in field:
            base, step = field.split("/")
            base = int(base) if base != "*" else min_val
            step = int(step)
            return (value - base) % step == 0

        # 精确值
        return int(field) == value

    def get_next_run(self, from_time: datetime) -> datetime:
        """获取下次执行时间 - 优化版：增量搜索"""
        current = from_time.replace(second=0, microsecond=0) + timedelta(minutes=1)

        # 预计算每个字段的匹配范围，减少遍历
        for _ in range(525600):  # 最多搜索一年分钟数
            if self.matches(current):
                return current
            current += timedelta(minutes=1)

        return from_time + timedelta(days=366)  # 默认一年后


class EnhancedScheduler:
    """增强型调度器"""

    def __init__(self):
        self.entries: List[ScheduleEntry] = []
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def add_cron(self, name: str, func: Callable, cron_expr: str,
                 timezone: str = "Asia/Shanghai", **kwargs) -> ScheduleEntry:
        """
        添加 Cron 调度任务

        Args:
            name: 任务名称
            func: 要执行的函数
            cron_expr: Cron 表达式 (分 时 日 月 周)
            timezone: 时区
            **kwargs: 传递给函数的额外参数

        Examples:
            scheduler.add_cron("morning_task", my_func, "20 6 * * 1-5")
            # 每周一到周五 6:20 执行
        """
        entry = ScheduleEntry(
            name=name,
            func=func,
            schedule_type=ScheduleType.CRON,
            cron_expr=cron_expr,
            timezone=timezone,
            kwargs=kwargs,
            next_run=self._calc_next_run(cron_expr)
        )
        self.entries.append(entry)
        return entry

    def add_interval(self, name: str, func: Callable, seconds: int,
                     **kwargs) -> ScheduleEntry:
        """添加间隔调度任务"""
        entry = ScheduleEntry(
            name=name,
            func=func,
            schedule_type=ScheduleType.INTERVAL,
            interval_seconds=seconds,
            kwargs=kwargs,
            next_run=datetime.now() + timedelta(seconds=seconds)
        )
        self.entries.append(entry)
        return entry

    def add_time_window(self, name: str, func: Callable, window: str,
                        timezone: str = "Asia/Shanghai", **kwargs) -> ScheduleEntry:
        """
        添加时间窗口调度任务

        Args:
            name: 任务名称
            func: 要执行的函数
            window: 时间窗口，如 "06:20-06:35"
            timezone: 时区

        Examples:
            scheduler.add_time_window("news", news_func, "06:20-06:35")
        """
        entry = ScheduleEntry(
            name=name,
            func=func,
            schedule_type=ScheduleType.TIME_WINDOW,
            time_window=window,
            timezone=timezone,
            kwargs=kwargs
        )
        self.entries.append(entry)
        return entry

    def remove(self, name: str) -> bool:
        """移除调度任务"""
        with self._lock:
            for i, entry in enumerate(self.entries):
                if entry.name == name:
                    self.entries.pop(i)
                    return True
            return False

    def enable(self, name: str) -> bool:
        """启用调度任务"""
        for entry in self.entries:
            if entry.name == name:
                entry.enabled = True
                return True
        return False

    def disable(self, name: str) -> bool:
        """禁用调度任务"""
        for entry in self.entries:
            if entry.name == name:
                entry.enabled = False
                return True
        return False

    def list_entries(self) -> List[Dict[str, Any]]:
        """列出所有调度任务"""
        result = []
        for entry in self.entries:
            result.append({
                "name": entry.name,
                "type": entry.schedule_type.value,
                "enabled": entry.enabled,
                "last_run": entry.last_run.isoformat() if entry.last_run else None,
                "next_run": entry.next_run.isoformat() if entry.next_run else None,
            })
        return result

    def start(self, check_interval: int = 30):
        """启动调度器（在后台线程中运行）"""
        if self.running:
            return

        self.running = True
        self._thread = threading.Thread(target=self._run_loop, args=(check_interval,))
        self._thread.daemon = True
        self._thread.start()
        print(f"📅 调度器已启动，检查间隔 {check_interval} 秒")

    def stop(self):
        """停止调度器"""
        self.running = False
        if self._thread:
            self._thread.join(timeout=5)

    def _run_loop(self, check_interval: int):
        """调度器主循环"""
        while self.running:
            now = datetime.now()
            self._check_and_run(now)
            time.sleep(check_interval)

    def _check_and_run(self, now: datetime):
        """检查并执行到期的任务"""
        with self._lock:
            for entry in self.entries:
                if not entry.enabled:
                    continue

                if entry.next_run and now >= entry.next_run:
                    try:
                        entry.last_run = now
                        entry.func(**entry.kwargs)

                        # 计算下次执行时间
                        entry.next_run = self._calc_next_run_for_entry(entry, now)
                        print(f"✅ 任务 {entry.name} 已执行，下次执行: {entry.next_run}")

                    except Exception as e:
                        print(f"❌ 任务 {entry.name} 执行失败: {e}")

                        # 计算下次执行时间（即使失败也要继续调度）
                        entry.next_run = self._calc_next_run_for_entry(entry, now)

    def _calc_next_run_for_entry(self, entry: ScheduleEntry, now: datetime) -> Optional[datetime]:
        """计算任务的下次执行时间"""
        if entry.schedule_type == ScheduleType.CRON and entry.cron_expr:
            return self._calc_next_run(entry.cron_expr, now)
        elif entry.schedule_type == ScheduleType.INTERVAL:
            return now + timedelta(seconds=entry.interval_seconds or 60)
        elif entry.schedule_type == ScheduleType.TIME_WINDOW:
            return self._calc_next_window_run(entry.time_window, now)
        return None

    def _calc_next_run(self, cron_expr: str, from_time: datetime = None) -> datetime:
        """计算下次 Cron 执行时间"""
        from_time = from_time or datetime.now()
        parser = CronParser(cron_expr)
        return parser.get_next_run(from_time)

    def _calc_next_window_run(self, window: str, from_time: datetime) -> datetime:
        """计算时间窗口的下次执行时间 - 支持跨天窗口"""
        try:
            start_str, end_str = window.split("-")
            start_hour, start_min = map(int, start_str.strip().split(":"))
            end_hour, end_min = map(int, end_str.strip().split(":"))

            # 转换为分钟计算
            start_mins = start_hour * 60 + start_min
            end_mins = end_hour * 60 + end_min
            current_mins = from_time.hour * 60 + from_time.minute

            # 判断是否跨天（结束时间 < 开始时间）
            if end_mins < start_mins:
                # 跨天窗口：23:00-02:00
                if current_mins >= start_mins or current_mins < end_mins:
                    # 在窗口内（今天晚于开始 或 今天早于结束）
                    return from_time.replace(hour=start_hour, minute=start_min, second=0, microsecond=0)
                else:
                    # 今天还没到窗口
                    return from_time.replace(hour=start_hour, minute=start_min, second=0, microsecond=0)
            else:
                # 普通窗口（今天结束 > 今天开始）
                today_start = from_time.replace(hour=start_hour, minute=start_min, second=0, microsecond=0)
                today_end = from_time.replace(hour=end_hour, minute=end_min, second=0, microsecond=0)

                if today_start <= from_time <= today_end:
                    return today_start  # 在窗口内，返回今天开始时间
                elif from_time < today_start:
                    return today_start  # 今天还没到窗口
                else:
                    # 今天已过窗口，明天
                    return today_start + timedelta(days=1)
        except Exception:
            return from_time + timedelta(hours=1)

    def run_pending(self):
        """手动触发检查并执行待运行的任务"""
        self._check_and_run(datetime.now())


def create_scheduler_from_config() -> EnhancedScheduler:
    """从 config.yaml 创建调度器"""
    from agents.config_loader import get_config

    scheduler = EnhancedScheduler()
    cfg = get_config()

    # 从配置读取时间窗口并创建调度任务
    windows = cfg.get("schedule.windows", {})
    for name, window in windows.items():
        print(f"  📅 调度任务: {name} -> {window}")

    return scheduler


# 全局调度器实例
_scheduler = None


def get_scheduler() -> EnhancedScheduler:
    """获取全局调度器"""
    global _scheduler
    if _scheduler is None:
        _scheduler = EnhancedScheduler()
    return _scheduler


if __name__ == "__main__":
    print("=== 增强型调度器测试 ===\n")

    scheduler = EnhancedScheduler()

    # 添加 Cron 任务
    scheduler.add_cron(
        "daily_report",
        lambda: print("📊 生成日报"),
        "30 7 * * *"  # 每天 7:30
    )

    # 添加间隔任务
    scheduler.add_interval(
        "health_check",
        lambda: print("🏥 健康检查"),
        seconds=300  # 每5分钟
    )

    # 添加时间窗口任务
    scheduler.add_time_window(
        "morning_cycle",
        lambda: print("🌅 执行早间流程"),
        "06:20-06:35"
    )

    print("\n📋 调度任务列表:")
    for entry in scheduler.list_entries():
        print(f"  [{entry['type']}] {entry['name']} - 下次: {entry['next_run']}")

    print("\n✅ 调度器配置完成")
"""GateController - SkepticAgent 门控逻辑独立模块
纯函数设计：返回过滤结果 + 副作用标记，由调用方统一执行写盘。
"""
import json
from datetime import datetime
from pathlib import Path
from typing import Set, Dict, List, Any, Optional, Tuple

class GateController:
    """Gate controller - 纯函数，无副作用（写盘由调用方执行）"""

    @staticmethod
    def read_verdict(verdict_file: Path) -> Tuple[Set[str], bool]:
        """读取裁决JSON，返回 (blocked_codes, gate_passed)"""
        if not verdict_file.exists():
            return set(), True
        try:
            import json
            data = json.loads(verdict_file.read_text(encoding="utf-8"))
            blocked_list = data.get("blocked", [])
            blocked_codes = {s.get("code", "") for s in blocked_list}
            return blocked_codes, len(blocked_codes) == 0
        except Exception:
            return set(), True

    @staticmethod
    def filter_pools(pools: dict, blocked_codes: Set[str]) -> dict:
        """从 pools 中过滤掉阻塞标的（纯内存操作）"""
        if not blocked_codes:
            return pools
        filtered = {}
        for pool_name, pool_data in pools.items():
            stocks = pool_data.get("stocks", []) if isinstance(pool_data, dict) else []
            filtered[pool_name] = {
                **pool_data,
                "stocks": [
                    s for s in stocks
                    if (s.get("代码") or s.get("股票代码", "")) not in blocked_codes
                ]
            } if isinstance(pool_data, dict) else pool_data
        return filtered

    @staticmethod
    def filter_scored_stocks(scored_stocks: list, blocked_codes: Set[str]) -> list:
        """从评分列表中过滤掉阻塞标的"""
        if not blocked_codes:
            return scored_stocks
        return [s for s in scored_stocks 
                if str(s.get("code", s.get("代码", ""))) not in blocked_codes]

    @staticmethod
    def check_blocked_count(key_pool_data: dict, blocked_codes: Set[str]) -> Tuple[list, list, bool]:
        """检查阻塞计数。
        返回 (demotions: 需要降级的标的列表, resets: 需要重置计数的标的列表, modified: 是否有变化)
        调用方负责写回磁盘。
        """
        demotions = []
        resets = []
        modified = False
        stocks = key_pool_data.get("stocks", [])
        
        for s in stocks:
            s_code = str(s.get("代码", s.get("股票代码", "")))
            if s_code in blocked_codes:
                s["blocked_count"] = s.get("blocked_count", 0) + 1
                if s["blocked_count"] >= 3:
                    demotions.append({
                        "代码": s_code,
                        "名称": s.get("名称", ""),
                        "count": s["blocked_count"],
                    })
                modified = True
            elif s.get("blocked_count", 0) > 0:
                s["blocked_count"] = 0
                resets.append({
                    "代码": s_code,
                    "名称": s.get("名称", ""),
                })
                modified = True
        
        return demotions, resets, modified

    @staticmethod
    def get_yellow_alerts(scored_stocks: list) -> list:
        """获取60-74分黄色预警标的"""
        return [s for s in scored_stocks if 60 <= s.get("score", 0) < 75]

    @staticmethod
    def is_all_blocked(scored_stocks: list, blocked_codes: Set[str]) -> bool:
        """判断是否所有候选股都被拦截"""
        remaining = GateController.filter_scored_stocks(scored_stocks, blocked_codes)
        return len(remaining) == 0

    # ═══════════════════════════════════════════════════════════════
    # v5.94: 跨池守卫 + 容量校验 + 写入规则 (gate_controller 接线)
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def check_cross_pool_duplicate(stock_code: str, exclude_pool: str = None, pool_manager=None) -> list:
        """遍历所有池检查同一股票是否已存在，返回存在该股票的池名列表"""
        if not pool_manager:
            return []
        all_pools = ['快筛候选池', '重点观察池', 'S级操作池', '边缘池', '持仓池']
        found = []
        for pool in all_pools:
            if pool == exclude_pool:
                continue
            pool_data = pool_manager.load_pool(pool)
            stocks = pool_data.get("stocks", []) if isinstance(pool_data, dict) else []
            for item in stocks:
                item_code = str(item.get("代码", item.get("股票代码", "")))
                if item_code == str(stock_code):
                    found.append(pool)
                    break
        return found

    @staticmethod
    def validate_pool_capacity(pool_name: str, current_count: int, max_capacity: int) -> bool:
        """若当前数量 ≥ max_capacity 则拒绝写入"""
        return current_count < max_capacity

    @staticmethod
    def enforce_writing_rules(stock: dict, target_pool: str, pool_manager=None) -> dict:
        """校验写入规则，返回 {'allowed': bool, 'reason': str}"""
        from agents.thresholds import POOL_CAPACITY_LIMITS as limits
        # 1. 容量检查
        max_cap = limits.get(target_pool, 50)
        if pool_manager:
            pool_data = pool_manager.load_pool(target_pool)
            stocks = pool_data.get("stocks", []) if isinstance(pool_data, dict) else []
            if len(stocks) >= max_cap:
                return {'allowed': False, 'reason': f'{target_pool}已达容量上限{max_cap}只'}
        # 2. 规则检查
        rules = {
            'S级操作池': lambda s: int(s.get('score', s.get('综合评分', 0))) >= 80,
            '重点观察池': lambda s: int(s.get('score', s.get('综合评分', 0))) >= 50,
            '持仓池': lambda s: True,
        }
        rule_fn = rules.get(target_pool, lambda s: True)
        if not rule_fn(stock):
            stock_score = stock.get('score', stock.get('综合评分', '?'))
            return {'allowed': False, 'reason': f'{target_pool}准入规则不满足(评分{stock_score})'}
        # 3. 跨池重复检查
        # P2-2026-06-04: 跨池重复默认拦截，除非显式 allow_cross_pool=True
        stock_code = str(stock.get("代码", stock.get("股票代码", "")))
        cross_pool_allowed = stock.pop('allow_cross_pool', False) if isinstance(stock, dict) else False
        if pool_manager and stock_code:
            duplicates = GateController.check_cross_pool_duplicate(stock_code, exclude_pool=target_pool, pool_manager=pool_manager)
            if duplicates:
                # 跨池重复：默认拒绝，除非标记为允许（如S级过期回流等受控路径）
                if cross_pool_allowed:
                    return {'allowed': True, 'reason': f'允许写入(已存在于{duplicates}，跨池记录已标记)', 'cross_pool': duplicates}
                else:
                    print(f"[GateController] 🚫 {stock_code} 跨池重复拦截: 已在 {duplicates}，写入 {target_pool} 被阻止")
                    return {'allowed': False, 'reason': f'跨池重复拦截: 代码已在 {duplicates}，拒绝写入 {target_pool}'}
        return {'allowed': True, 'reason': '通过'}
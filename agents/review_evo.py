#!/usr/bin/env python3
"""
复盘进化模块 - 天枢权衡自我进化核心

功能：
1. 记录每次决策的输入特征（新闻驱动、行情数据、基本面评分）
2. 定期统计胜率（从持仓池数据中获取实际涨跌）
3. 自动调整下次筛选的权重

胜率计算：
- 每次决策记录：推荐日期、推荐代码、推荐理由
- N天后从实际行情获取涨跌幅
- 计算胜率 = 盈利次数 / 总决策次数
- 权重调整 = 按胜率高的驱动类型加分
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict
from collections import defaultdict

from safe_file_utils import safe_read_json, safe_write_file
from logger import plog

logger = logging.getLogger(__name__)


class ReviewEvo:
    """复盘进化引擎"""
    
    def __init__(self, root: Path = None):
        self.root = root or Path(__file__).parent.parent.resolve()
        self.memory_dir = self.root / "data" / "复盘记录"
        self.decision_log = self.memory_dir / "决策日志.json"
        self.weight_file = self.memory_dir / "权重参数.json"
        self.std_log = self.root / "data" / "decision_log.json"  # feedback_loop 读取的标准化路径
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_files()
    
    def _ensure_files(self):
        # 决策日志
        if not self.decision_log.exists():
            success = safe_write_file(self.decision_log, json.dumps({"决策记录": [], "统计": {"总决策数": 0, "盈利数": 0, "胜率": 0}}, ensure_ascii=False, indent=2))
            if not success:
                logger.error(f"[ReviewEvo] 初始化决策日志失败: {self.decision_log}")
        
        # 标准化格式（供 feedback_loop 读取）
        if not self.std_log.exists():
            success = safe_write_file(self.std_log, json.dumps([], ensure_ascii=False, indent=2))
            if not success:
                logger.error(f"[ReviewEvo] 初始化标准化日志失败: {self.std_log}")
        
        # 权重参数
        if not self.weight_file.exists():
            default_weights = {
                "技术面权重": 30,
                "基本面权重": 25,
                "新闻驱动权重": 25,
                "情绪评分权重": 20,
                "更新时间": datetime.now().isoformat(),
            }
            success = safe_write_file(self.weight_file, json.dumps(default_weights, ensure_ascii=False, indent=2))
            if not success:
                logger.error(f"[ReviewEvo] 初始化权重参数失败: {self.weight_file}")
    
    def record_decision(self, stock_code: str, stock_name: str, reason: str, 
                      driver: str, tech_score: int, fundamental_score: int, 
                      recommendation: str, confidence: str, entry_price: float = 0,
                      hypothesis: str = "", expected_logic: str = "",
                      is_executed: bool = True):
        """记录一次决策（包含可验证假设）"""
        log = safe_read_json(self.decision_log, default={"决策记录": [], "统计": {"总决策数": 0, "盈利数": 0, "胜率": 0}}, required=False, log_error=False)
        if log is None:
            log = {"决策记录": [], "统计": {"总决策数": 0, "盈利数": 0, "胜率": 0}}
        
        record = {
            "日期": datetime.now().strftime("%Y-%m-%d"),
            "时间戳": datetime.now().isoformat(),
            "股票代码": stock_code,
            "股票名称": stock_name,
            "推荐操作": recommendation,
            "信心度": confidence,
            "驱动类型": driver,
            "技术面评分": tech_score,
            "基本面评分": fundamental_score,
            "推荐价格": entry_price,
            "决策理由": reason,
            "$is_executed": is_executed,  # 标记是否实际执行（非模板兜底）
            "假设": hypothesis,
            "预期逻辑": expected_logic,
            "验证时间点": (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d"),
            "实际结果": None,
            "复盘日期": None,
            "假设验证": None,
            "实际涨跌": None,
        }
        
        log["决策记录"].append(record)
        log["统计"]["总决策数"] = len(log["决策记录"])
        
        success = safe_write_file(self.decision_log, json.dumps(log, ensure_ascii=False, indent=2))
        if not success:
            logger.error(f"[ReviewEvo] 记录决策失败: {self.decision_log}")
        
        # 同步写入标准化格式
        self._sync_std_log()
        
        return record
    
    def _sync_std_log(self):
        """将原始日志同步写入标准化格式，供 feedback_loop 读取"""
        raw = safe_read_json(self.decision_log, default={"决策记录": [], "统计": {"总决策数": 0, "盈利数": 0, "胜率": 0}}, required=False, log_error=False)
        if raw is None:
            raw = {"决策记录": [], "统计": {"总决策数": 0, "盈利数": 0, "胜率": 0}}
        
        normalized = []
        for r in raw.get("决策记录", []):
            normalized.append({
                "code": r.get("股票代码", ""),
                "name": r.get("股票名称", ""),
                "entry_price": r.get("推荐价格", 0),
                "actual_pnl": r.get("实际结果"),
                "drive_type": r.get("驱动类型", "未知"),
                "date": r.get("日期", ""),
                "recommendation": r.get("推荐操作", ""),
                "confidence": r.get("信心度", ""),
                "tech_score": r.get("技术面评分", 0),
                "fundamental_score": r.get("基本面评分", 0),
                "$is_executed": r.get("$is_executed", True),
                "hypothesis": r.get("假设", ""),
                "expected_logic": r.get("预期逻辑", ""),
                "verify_date": r.get("验证时间点", ""),
                "hypothesis_result": r.get("假设验证", None),
                "actual_change": r.get("实际涨跌", None),
                "reflection": r.get("反思", ""),
            })
        
        success = safe_write_file(self.std_log, json.dumps(normalized, ensure_ascii=False, indent=2))
        if not success:
            logger.error(f"[ReviewEvo] 同步标准化日志失败: {self.std_log}")

    def _load_normalized(self) -> List[dict]:
        """加载标准化决策记录（供 get_stock_history 使用）"""
        if self.std_log.exists():
            data = safe_read_json(self.std_log, default=None, required=False, log_error=False)
            return data if data is not None else []
        # 兜底：直接从原始日志加载并转换
        raw = safe_read_json(self.decision_log, default={"决策记录": [], "统计": {"总决策数": 0, "盈利数": 0, "胜率": 0}}, required=False, log_error=False)
        if raw is None: raw = {"决策记录": [], "统计": {"总决策数": 0, "盈利数": 0, "胜率": 0}}
        normalized = []
        for r in raw.get("决策记录", []):
            normalized.append({
                "code": r.get("股票代码", ""),
                "name": r.get("股票名称", ""),
                "entry_price": r.get("推荐价格", 0),
                "actual_pnl": r.get("实际结果"),
                "drive_type": r.get("驱动类型", "未知"),
                "date": r.get("日期", ""),
                "recommendation": r.get("推荐操作", ""),
                "confidence": r.get("信心度", ""),
                "tech_score": r.get("技术面评分", 0),
                "fundamental_score": r.get("基本面评分", 0),
                "hypothesis": r.get("假设", ""),
                "expected_logic": r.get("预期逻辑", ""),
                "verify_date": r.get("验证时间点", ""),
                "hypothesis_result": r.get("假设验证", None),
                "actual_change": r.get("实际涨跌", None),
                "reflection": r.get("反思", ""),
            })
        return normalized
    
    def get_decisions(self, days: int = 30) -> List[dict]:
        """获取最近N天的决策记录"""
        log = safe_read_json(self.decision_log, default={"decision_records": []}, required=False, log_error=False)
        if log is None: log = {"decision_records": []}
        
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        return [r for r in log["决策记录"] if r.get("日期", "") >= cutoff]
    
    def calculate_win_rate(self, days: Optional[int] = None) -> dict:
        """计算胜率（基于已复盘的决策），支持时间窗口过滤

        Args:
            days: 时间窗口天数，None或0表示全量
        """
        log = safe_read_json(self.decision_log, default={"decision_records": []}, required=False, log_error=False)
        if log is None: log = {"decision_records": []}

        records = log["决策记录"]

        # 时间窗口过滤
        if days and days > 0:
            cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            records = [r for r in records if r.get("日期", "") >= cutoff]

        # 只统计有实际结果的（兼容 actual_pnl / 实际结果 两种字段名）
        closed = [r for r in records
                  if r.get("actual_pnl") is not None or r.get("实际结果") is not None]

        if not closed:
            return {
                "胜率": 0, "盈利数": 0, "总数": 0,
                "待复盘": len(records),
                "窗口天数": days if days else "全量"
            }

        wins = sum(
            1 for r in closed
            if (r.get("actual_pnl") or r.get("实际结果", 0)) > 0
        )
        win_rate = wins / len(closed) * 100

        log["统计"]["盈利数"] = wins
        log["统计"]["胜率"] = round(win_rate, 1)
        log["统计"]["总数"] = len(closed)

        return {
            "胜率": win_rate, "盈利数": wins, "总数": len(closed),
            "窗口天数": days if days else "全量"
        }
    
    def get_driver_stats(self) -> dict:
        """按驱动类型统计胜率"""
        log = safe_read_json(self.decision_log, default={"decision_records": []}, required=False, log_error=False)
        if log is None: log = {"decision_records": []}
        
        driver_results = defaultdict(list)
        for r in log["决策记录"]:
            if r.get("实际结果") is not None:
                driver = r.get("驱动类型", "未知")
                driver_results[driver].append(r["实际结果"])
        
        stats = {}
        for driver, results in driver_results.items():
            wins = sum(1 for x in results if x > 0)
            stats[driver] = {
                "次数": len(results),
                "盈利": wins,
                "胜率": round(wins / len(results) * 100, 1) if results else 0,
            }
        
        return stats
    
    def get_weights(self) -> dict:
        """获取当前权重"""
        data = safe_read_json(self.weight_file, default=None, required=False, log_error=False)
        return data if data is not None else {}
    
    def adjust_weights(self):
        """根据胜率自动调整权重"""
        weights = self.get_weights()
        stats = self.get_driver_stats()
        
        if not stats:
            return {"action": "无足够数据", "weights": weights}
        
        # 找出胜率最高的驱动类型
        best_driver = max(stats.items(), key=lambda x: x[1]["胜率"])
        worst_driver = min(stats.items(), key=lambda x: x[1]["胜率"])
        
        # 调整权重
        if best_driver[1]["胜率"] > 60 and best_driver[1]["次数"] >= 3:
            # 高胜率驱动类型 +5%
            old = weights.get("新闻驱动权重", 25)
            weights["新闻驱动权重"] = min(40, old + 5)
            action = f"驱动类型{best_driver[0]}胜率{int(best_driver[1]['胜率'])}%，权重+5%"
        elif worst_driver[1]["胜率"] < 30 and worst_driver[1]["次数"] >= 3:
            # 低胜率驱动类型 -5%
            old = weights.get("新闻驱动权重", 25)
            weights["新闻驱动权重"] = max(10, old - 5)
            action = f"驱动类型{worst_driver[0]}胜率{int(worst_driver[1]['胜率'])}%，权重-5%"
        else:
            action = "胜率在合理区间，不调整"
        
        weights["更新时间"] = datetime.now().isoformat()
        
        success = safe_write_file(self.weight_file, json.dumps(weights, ensure_ascii=False, indent=2))
        if not success:
            logger.error(f"[ReviewEvo] 保存权重失败: {self.weight_file}")
        
        return {"action": action, "weights": weights, "driver_stats": stats}
    
    def update_result(self, stock_code: str, result_pct: float, pm=None):
        """更新实际结果，并判断假设是否兑现
        可选传入 PoolManager，触发五池联动降级
        """
        from pathlib import Path
        root = self.root

        log = safe_read_json(self.decision_log, default={"decision_records": []}, required=False, log_error=False)
        if log is None: log = {"decision_records": []}

        pool_action = None  # 五池联动动作

        for r in log["决策记录"]:
            if r.get("股票代码") == stock_code and r.get("实际结果") is None:
                r["实际结果"] = result_pct
                r["实际涨跌"] = result_pct
                r["复盘日期"] = datetime.now().strftime("%Y-%m-%d")
                # 判断假设是否兑现：盈利 ≥2% 视为逻辑兑现
                if result_pct >= 2.0:
                    r["假设验证"] = "✅兑现"
                elif result_pct <= -2.0:
                    r["假设验证"] = "❌未兑现"
                else:
                    r["假设验证"] = "⏳待确认"

                # 五池联动：假设未兑现时自动降级
                if pm is not None and r.get("假设验证") == "❌未兑现":
                    from_pool = self._find_stock_pool(pm, stock_code)
                    target_pool = self._downgrade_target(from_pool)
                    if target_pool:
                        success = pm.move_stock(from_pool, target_pool, stock_code)
                        if success:
                            pool_action = {
                                "code": stock_code,
                                "from_pool": from_pool,
                                "to_pool": target_pool,
                                "reason": "假设未兑现自动降级",
                            }

        success = safe_write_file(self.decision_log, json.dumps(log, ensure_ascii=False, indent=2))
        if not success:
            logger.error(f"[ReviewEvo] 保存决策日志失败: {self.decision_log}")

        # 同步写入标准化格式
        self._sync_std_log()

        # 重新计算胜率
        stats = self.calculate_win_rate()
        return pool_action

    def _find_stock_pool(self, pm, stock_code: str) -> str:
        """查找股票当前所在池"""
        from pool_manager import PoolManager
        POOL_NAMES = [
            "快筛候选池", "重点观察池", "边缘池", "持仓池", "S级操作池"
        ]
        for pool_name in POOL_NAMES:
            stocks = pm.get_stocks(pool_name)
            for s in stocks:
                if (s.get("股票代码") or s.get("代码", "")) == stock_code:
                    return pool_name
        return ""

    def _downgrade_target(self, current_pool: str) -> str | None:
        """降级目标池映射"""
        mapping = {
            "快筛候选池": "边缘池",
            "重点观察池": "快筛候选池",
            "持仓池": "快筛候选池",
                    }
        return mapping.get(current_pool)
    
    def summarize(self) -> str:
        """生成复盘摘要"""
        log = safe_read_json(self.decision_log, default={"decision_records": []}, required=False, log_error=False)
        if log is None: log = {"decision_records": []}
        
        stats = self.calculate_win_rate()
        driver_stats = self.get_driver_stats()
        weights = self.get_weights()
        
        lines = [
            "📊 复盘统计",
            f"- 总决策数: {stats['总数']}",
            f"- 盈利数: {stats['盈利数']}", 
            f"- 胜率: {stats['胜率']:.1f}%",
            f"- 待复盘: {stats.get('待复盘', 0)}",
            "",
            "按驱动类型胜率:",
        ]
        for driver, s in sorted(driver_stats.items(), key=lambda x: -x[1]["胜率"]):
            lines.append(f"  - {driver}: {s['胜率']}% ({s['次数']}次)")
        
        lines.extend([
            "",
            "当前权重:",
            f"  - 技术面: {weights.get('技术面权重', 30)}%",
            f"  - 基本面: {weights.get('基本面权重', 25)}%", 
            f"  - 新闻驱动: {weights.get('新闻驱动权重', 25)}%",
            f"  - 情绪评分: {weights.get('情绪评分权重', 20)}%",
        ])
        
        return "\n".join(lines)



    def record_verification(self, code: str, decision_date: str, actual_pnl_pct: float, tn_date: str):
        """T+N 验证回填：持仓池触发验证节点后，填入实际结果。"""
        data = safe_read_json(self.decision_log, default={"decision_records": []}, required=False, log_error=False)
        if data is None: data = {"decision_records": []}
        updated = False
        for r in data.get("决策记录", []):
            r_code = str(r.get("股票代码", "")).strip()
            r_date = r.get("日期", "")
            if r_code == code and r_date == decision_date:
                r["实际结果"] = actual_pnl_pct
                r["实际涨跌"] = "{:+.2f}%".format(actual_pnl_pct)
                r["复盘日期"] = tn_date
                r["假设验证"] = "✅兑现" if actual_pnl_pct >= 0 else "❌未兑现"
                updated = True
                break
        if updated:
            success = safe_write_file(self.decision_log, json.dumps(data, ensure_ascii=False, indent=2))
            if not success:
                logger.error(f"[ReviewEvo] 保存决策日志失败: {self.decision_log}")
            return True
        return False

    def append_reflection(self, code: str, actual_pnl_pct: float, record: dict) -> str:
        """生成反思段落并追加到复盘记录。"""
        decision_date = record.get("日期", "N/A")
        assumption = record.get("假设", "无明确假设")
        expected_logic = record.get("预期逻辑", "无预期逻辑")
        verification = "✅兑现" if actual_pnl_pct >= 0 else "❌未兑现"
        if actual_pnl_pct >= 5:
            verdict = "大幅盈利"
        elif actual_pnl_pct >= 0:
            verdict = "小幅盈利"
        elif actual_pnl_pct >= -5:
            verdict = "小幅亏损"
        else:
            verdict = "大幅亏损"
        reflection = ("[反思 {}→{}] {}（{:+.2f}%），假设「{}」，预期逻辑「{}」，验证{}。"
            .format(decision_date, datetime.now().strftime("%Y-%m-%d"),
                     verdict, actual_pnl_pct, assumption, expected_logic, verification))
        data = safe_read_json(self.decision_log, default={"decision_records": []}, required=False, log_error=False)
        if data is None: data = {"decision_records": []}
        for r in data.get("决策记录", []):
            if str(r.get("股票代码", "")).strip() == code and r.get("日期") == decision_date:
                existing = r.get("反思", "")
                r["反思"] = (existing + "\n" + reflection).strip() if existing else reflection
                break
        success = safe_write_file(self.decision_log, json.dumps(data, ensure_ascii=False, indent=2))
        if not success:
            logger.error(f"[ReviewEvo] 保存决策日志失败: {self.decision_log}")
        return reflection

    def get_stock_history(self, code: str, limit: int = 3) -> list[dict]:
        """获取某股票最近的历史决策记录（供 DecisionAgent 注入 prompt）。"""
        records = self._load_normalized()
        matched = [r for r in records if r.get("code", "").strip() == code.strip()]
        matched.sort(key=lambda x: (x.get("actual_pnl") is None, x.get("date", "")), reverse=True)
        return matched[:limit]


def run():
    """测试"""
    evo = ReviewEvo()
    evo.record_decision(
        stock_code="600118", stock_name="中国卫星",
        driver="S级", tech_score=75, fundamental_score=65,
        recommendation="建议关注", confidence="高",
        reason="卫星互联网绝对龙头"
    )
    plog("INFO", evo.summarize())
if __name__ == "__main__":
    run()

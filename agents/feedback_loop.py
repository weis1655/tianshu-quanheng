#!/usr/bin/env python3
"""
Feedback Loop Module - 天枢权衡自我进化核心
使用BaseAgent和PoolManager重构
"""

import json
import sys
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional

from safe_file_utils import safe_read_json, safe_write_file
from path_config import PathConfig

logger = logging.getLogger(__name__)

from market_agent import fetch_quotes
from base_agent import BaseAgent, add_market_prefix
from pool_manager import PoolManager
from logger import get_logger

cfg = PathConfig()


class FeedbackLoopAgent(BaseAgent):
    """反馈闭环Agent"""
    
    def __init__(self):
        super().__init__("FeedbackLoop")
        self.pool_manager = PoolManager()
        self.logger = get_logger("FeedbackLoop")

    def run(self) -> Dict[str, Any]:
        """运行完整反馈闭环"""
        self.logger.log_agent_start("FeedbackLoop", "full_loop")
        
        results = {}
        start_time = datetime.now()
        
        try:
            # 1. 检查市场熔断
            circuit_triggered = self.check_market_circuit()
            results["circuit"] = circuit_triggered
            
            # 2. 分析持仓
            holdings = self.analyze_holdings()
            results["holdings"] = holdings
            
            # 3. 计算胜率
            stats = self.calculate_win_rate()
            results["stats"] = stats
            
            # 4. 自动调整权重（如果决策足够多）
            if stats.get("total", 0) >= 5:
                self.auto_adjust_weights(stats.get("rate", 0), stats.get("by_type", {}))
            
            # 5. 记录状态
            duration = (datetime.now() - start_time).total_seconds()
            self.logger.info(f"反馈闭环执行完成", 
                          holdings_count=len(holdings or []),
                          total_decisions=stats.get("total", 0),
                          duration_s=round(duration, 2))
            
            # 6. 生成反馈闭环报告
            report_path = PathConfig().data_dir.parent / "data" / "历史记录" / f"{datetime.now().strftime('%Y-%m-%d')}_反馈闭环报告.md"
            try:
                lines = ["# 反馈闭环报告", f"**日期**: {datetime.now().strftime('%Y-%m-%d')}", ""]
                if circuit_triggered:
                    lines.append("## 🔴 市场熔断触发")
                lines.append(f"## 持仓分析: {len(holdings or [])}只")
                lines.append(f"## 决策统计: 总{stats.get('total',0)} 盈利{stats.get('wins',0)} 胜率{stats.get('rate',0):.1f}%")
                report_text = "\n".join(lines)
                safe_write_file(report_path, report_text)
                self.logger.info(f"反馈闭环报告已保存: {report_path}")
            except Exception as e:
                self.logger.warning(f"反馈闭环报告写入失败: {e}")

            results["success"] = True
            
        except Exception as e:
            self.logger.log_error(e, "feedback_loop")
            results["success"] = False
            results["error"] = str(e)
        
        self.logger.log_agent_end(
            "FeedbackLoop", 
            results.get("success", False),
            duration=(datetime.now() - start_time).total_seconds()
        )
        
        return results
    
    def check_market_circuit(self) -> bool:
        """检查市场熔断条件"""
        self.logger.info("检查市场熔断...")
        
        quotes = fetch_quotes(["sh000001", "sz399001", "sz399006"])  # 上证+深成指+创业板
        if not quotes:
            self.logger.warning("无法获取上证指数")
            return False
        
        sh_change = quotes[0].get("涨跌幅", 0)
        # 处理 -0.0 的显示问题（腾讯API对微跌返回 -0.00）
        if abs(sh_change) < 0.005:
            sh_change = 0.0
        self.logger.info(f"上证指数今日涨跌: {sh_change:+.2f}%")
        
        CIRCUIT_BREAKER = -3.0
        
        if sh_change < CIRCUIT_BREAKER:
            self.logger.warning(f"市场熔断触发! 单日下跌 {sh_change:.2f}%")
            return True
        else:
            self.logger.info("市场正常，无需熔断")
            return False
    
    def analyze_holdings(self) -> List[Dict[str, Any]]:
        """分析持仓池，计算真实盈亏"""
        self.logger.info("分析持仓池...")
        
        # 使用PoolManager加载持仓
        holdings = self.pool_manager.get_stocks("持仓池")
        
        if not holdings:
            self.logger.info("持仓池为空")
            return []
        
        # 提取股票代码（添加市场前缀）
        codes = []
        for s in holdings:
            code = s.get("股票代码") or s.get("代码", "")
            if code:
                prefixed = add_market_prefix(code)
                if prefixed:
                    codes.append(prefixed)
        
        if not codes:
            self.logger.warning("无有效股票代码")
            return []
        
        # 获取实时行情
        quotes = fetch_quotes(codes)
        
        if not quotes:
            self.logger.warning("无法获取行情数据")
            return []
        
        # 计算涨跌幅
        results = []
        today_str = datetime.now().strftime("%Y-%m-%d")
        
        for holding in holdings:
            code = holding.get("股票代码") or holding.get("代码", "")
            name = holding.get("股票名称") or holding.get("名称", "未知")
            
            quote = next((q for q in quotes if q.get("代码") == code), None)
            
            if quote:
                change_pct = quote.get("涨跌幅", 0)
                price = quote.get("现价", 0)
                prev = quote.get("昨收", price)
                
                entry_date = holding.get("纳入日期") or holding.get("决策时间") or today_str
                entry_price = holding.get("买入价") or holding.get("成本") or prev
                
                if entry_price and price:
                    pnl_pct = ((price - entry_price) / entry_price) * 100
                else:
                    pnl_pct = change_pct
                
                result = {
                    "code": code,
                    "name": name,
                    "price": price,
                    "change_pct": change_pct,
                    "pnl_pct": pnl_pct,
                    "entry_price": entry_price,
                    "entry_date": entry_date,
                }
                results.append(result)
                
                self.logger.info(f"{code} {name}: 现价{price:.2f} 涨跌{change_pct:+.2f}%")
            else:
                self.logger.warning(f"{code} {name}: 无行情数据")
        
        return results
    
    def calculate_win_rate(self, days: int = 0) -> Dict[str, Any]:
        """计算决策胜率，支持时间窗口过滤

        委托给 ReviewEvo 计算（避免重复逻辑），只补充 by_type 字段。
        """
        from review_evo import ReviewEvo
        evo = ReviewEvo(root=self.root)

        # 读时间窗口配置（默认30天）
        if days <= 0:
            days = self._get_config().get("feedback", {}).get("win_rate", {}).get("window_days", 0)
        window_label = f"近{days}天" if days > 0 else "全量"

        stats = evo.calculate_win_rate(days=days)
        driver_stats = evo.get_driver_stats()

        # 兼容旧接口：按驱动类型统计
        by_type = {}
        for driver, s in driver_stats.items():
            by_type[driver] = {"total": s["次数"], "wins": s["盈利"]}

        rate = stats.get("胜率", 0)
        wins = stats.get("盈利数", 0)
        total = stats.get("总数", 0)

        self.logger.info(f"总决策: {total} | 盈利: {wins} | 胜率: {rate:.1f}%")

        return {
            "total": total,
            "wins": wins,
            "rate": rate,
            "by_type": by_type,
            "window": window_label,
        }
    
    def auto_adjust_weights(self, win_rate: float, by_type: Dict) -> None:
        """根据胜率自动调整权重"""
        if win_rate < 50:
            # 胜率低于50%：执行调权（当前为空壳，预留调权接口）
            self.logger.info(f"胜率 {win_rate:.1f}% 低于基准，预留调权逻辑")
            # TODO: 接入权重计算算法
        else:
            self.logger.info(f"胜率 {win_rate:.1f}% 正常，无需调整")



def run_full_loop():
    """运行完整闭环（保留原接口）"""
    agent = FeedbackLoopAgent()
    return agent.run()


class FeedbackLoop:
    """反馈闭环类（保留原接口）"""
    def run(self):
        return run_full_loop()


if __name__ == "__main__":
    agent = FeedbackLoopAgent()
    result = agent.run()
    print(f"\n执行完成: {result.get('success')}")
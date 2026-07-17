#!/usr/bin/env python3
"""
Research Agent - 研报数据 Agent
通过搜索引擎获取券商研报摘要

设计原则：
- 0次本地LLM，用搜索API获取研报
- 不直接调用付费研报数据库
"""
import subprocess
import json
import sys
from pathlib import Path
from typing import Optional, List, Dict, Any
from path_config import ensure_agent_paths; ensure_agent_paths()
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, List

from safe_file_utils import safe_read_json
from logger import plog

logger = logging.getLogger(__name__)


def search_research_reports(code: str, name: str, limit: int = 3) -> dict:
    """
    搜索券商研报（通过搜索引擎）
    """
    query = f"{name} {code} 券商研报 买入评级"
    import shlex
    safe_query = shlex.quote(query)
    cmd = f'curl -sL --max-time 15 "https://duckduckgo.com/html/?q={safe_query}&format=json"'
    
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, timeout=20)
        content = r.stdout.decode("utf-8", errors="replace")[:3000]
        
        # 从搜索结果中提取研报信息
        items = []
        # 简单解析标题和链接
        lines = content.split('\n')
        for line in lines:
            if 'result' in line.lower() or 'title' in line.lower():
                if len(items) < limit:
                    items.append(line.strip()[:200])
        
        return {
            "success": True,
            "query": query,
            "results": items[:limit],
            "count": len(items),
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "results": [],
        }


def analyze_fundamental(stock_data: dict) -> dict:
    """
    基于基本面数据计算评分（规则引擎，0次LLM）
    
    综合考虑：
    - PE估值：15-30合理，太高太低估分
    - PB估值：<3安全，>10风险
    - 换手率：活跃度
    - 市值：适中优先
    - 52周位置：低位加分
    """
    scores = {"pe_score": 0, "pb_score": 0, "turnover_score": 0, "cap_score": 0, "position_score": 0}
    
    # PE评分：15-30得满分，<0或>100得0分
    pe = stock_data.get("市盈率_TTM", 0)
    if pe > 0:
        if 10 <= pe <= 25:
            scores["pe_score"] = 100
        elif pe < 10:
            scores["pe_score"] = 80
        elif pe < 40:
            scores["pe_score"] = 60
        elif pe < 80:
            scores["pe_score"] = 30
        else:
            scores["pe_score"] = 10
    
    # PB评分：<3高分，>10低分
    pb = stock_data.get("市净率", 0)
    if pb > 0:
        if pb < 2:
            scores["pb_score"] = 100
        elif pb < 3:
            scores["pb_score"] = 80
        elif pb < 5:
            scores["pb_score"] = 60
        elif pb < 10:
            scores["pb_score"] = 30
        else:
            scores["pb_score"] = 10
    
    # 换手率评分：3-10%活跃
    turnover = stock_data.get("换手率", 0)
    if turnover > 0:
        if 3 <= turnover <= 10:
            scores["turnover_score"] = 100
        elif turnover < 1:
            scores["turnover_score"] = 40
        elif turnover < 3:
            scores["turnover_score"] = 60
        elif turnover < 20:
            scores["turnover_score"] = 80
        else:
            scores["turnover_score"] = 50  # 过高可能有问题
    
    # 市值评分：200-2000亿适中
    cap = stock_data.get("流通市值_亿", 0)
    if cap > 0:
        if 100 <= cap <= 1000:
            scores["cap_score"] = 100
        elif cap < 50:
            scores["cap_score"] = 50
        elif cap < 200:
            scores["cap_score"] = 80
        elif cap < 3000:
            scores["cap_score"] = 60
        else:
            scores["cap_score"] = 40
    
    # 52周位置评分
    price = stock_data.get("现价", 0)
    high_52w = stock_data.get("52周最高", 0)
    low_52w = stock_data.get("52周最低", 0)
    if price > 0 and high_52w > low_52w:
        position = (price - low_52w) / (high_52w - low_52w)
        if position < 0.3:
            scores["position_score"] = 100  # 低位
        elif position < 0.5:
            scores["position_score"] = 80
        elif position < 0.7:
            scores["position_score"] = 60
        else:
            scores["position_score"] = 30  # 高位
    
    # 综合基本面评分
    weights = {"pe_score": 25, "pb_score": 20, "turnover_score": 15, "cap_score": 15, "position_score": 25}
    total = sum(scores[k] * weights[k] for k in weights) / sum(weights.values())
    
    return {
        "pe_score": scores["pe_score"],
        "pb_score": scores["pb_score"],
        "turnover_score": scores["turnover_score"],
        "cap_score": scores["cap_score"],
        "position_score": scores["position_score"],
        "fundamental_score": round(total, 1),
    }


def run(stocks_file: str = None):
    """运行研报分析"""
    root = Path(__file__).parent.parent.resolve()
    
    # 读取候选池
    import sys
    sys.path.insert(0, str(root / "agents"))
    from market_agent import fetch_quotes
    
    # 去重
    candidates = list(set(candidates))
    pool_dir = root / "五池管理"
    candidates = []
    
    for pool_name in ["快筛候选池.json", "重点观察池.json"]:
        pool_file = pool_dir / pool_name
        if pool_file.exists():
            data = safe_read_json(pool_file, default=None, required=False, log_error=False)
            if data is not None:
                for item in data.get("stocks", []):
                    code = item.get("股票代码", "") or item.get("代码", "")
                    if code and code != "000000":
                        # 转换为腾讯格式
                        market = "sh" if code.startswith("6") else "sz"
                        candidates.append(f"{market}{code}")
    
    if not candidates:
        return {"success": False, "error": "无候选股票", "stocks": []}
    
    # 获取行情数据
    quotes = fetch_quotes(candidates)
    if not quotes:
        return {"success": False, "error": "无法获取行情数据", "stocks": []}
    
    # 分析每只股票
    analyzed = []
    for q in quotes:
        code = q.get("代码", "")
        name = q.get("名称", "")
        
        # 基本面评分
        fundamental = analyze_fundamental(q)
        
        # 研报搜索
        reports = search_research_reports(code, name)
        
        analyzed.append({
            "代码": code,
            "名称": name,
            "现价": q.get("现价", 0),
            "涨跌幅": q.get("涨跌幅", 0),
            "基本面评分": fundamental,
            "研报搜索": reports.get("results", [])[:2],
        })
    
    # 按基本面评分排序
    analyzed.sort(key=lambda x: x["基本面评分"]["fundamental_score"], reverse=True)
    
    return {
        "success": True,
        "analyzed": analyzed[:6],
        "count": len(analyzed),
    }


if __name__ == "__main__":
    result = run()
    if result["success"]:
        plog("INFO", f"✅ 研报分析完成 | {result['count']}只股票")
        for s in result["analyzed"]:
            plog("INFO", f"  {s['代码']} {s['名称']}: 基本面{int(s['基本面评分']['fundamental_score'])}分")
    else:
        plog("INFO", f"❌ {result.get('error', 'error')}")
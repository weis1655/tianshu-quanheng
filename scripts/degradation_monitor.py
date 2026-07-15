#!/usr/bin/env python3
"""LLM退化监控脚本 — 追踪决策Agent输出退化频率"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
deg_file = ROOT / "data" / "llm_degradation_counter.json"

if deg_file.exists():
    data = json.loads(deg_file.read_text())
    count = data.get("count", 0)
    dates = data.get("dates", [])
    latest = data.get("latest", "未知")
    print(f"📊 LLM退化监控报告")
    print(f"  累计退化次数: {count}")
    print(f"  最后退化日期: {latest}")
    print(f"  退化日期列表: {', '.join(dates[-10:]) if dates else '无'}")
    if count > 5:
        print(f"  ⚠️ 退化频率较高({count}次)，建议检查模型或提示词")
    else:
        print(f"  ✅ 退化频率在正常范围")
else:
    print("ℹ️ 无退化记录（计数器文件不存在）")

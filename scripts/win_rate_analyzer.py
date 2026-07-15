#!/usr/bin/env python3
"""win_rate_analyzer 存根模块 — 避免cron报错

回头看报告中的准确率分析已由 scripts/回头看.py 实现。
此存根仅供无法import时报错静默处理。
"""
def calculate_win_rate(*args, **kwargs):
    """返回空结果，避免报错"""
    return {"win_rate": 0, "total": 0, "wins": 0, "losses": 0, "note": "功能已迁移至scripts/回头看.py"}

if __name__ == "__main__":
    print("ℹ️ win_rate_analyzer: 功能已迁移至 scripts/回头看.py")
    print("   请使用: python scripts/回头看.py")

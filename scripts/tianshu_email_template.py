#!/usr/bin/env python3
"""
天枢权衡统一邮件发送脚本

功能：读取今日报告文件 → 组合 Markdown → md_to_html 转 HTML → SMTP 发送
      不产生任何 markdown 原始语法暴露到邮件正文

用法：
    python3 tianshu_email_template.py --type 早盘
    python3 tianshu_email_template.py --type 盘中复盘
    python3 tianshu_email_template.py --type 临盘决策
    python3 tianshu_email_template.py --type 反馈闭环
"""

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

# 添加 send_email 路径
sys.path.insert(0, os.path.expanduser("~/.hermes/skills/email/scripts"))
from path_config import ensure_agent_paths; ensure_agent_paths()
from send_email import send_email, md_to_html


# 各阶段报告文件名
# 默认基础列表：所有 full_cycle 阶段共用
_BASE_REPORTS = [
    "宏观前置分析",
    "技术面分析",
    "快筛报告",
    "审查报告",
    "质疑审查报告",
    "四段闭环汇总",
    "决策报告",
]

REPORT_FILES = {
    "早盘": _BASE_REPORTS.copy(),
    "盘中复盘": _BASE_REPORTS.copy(),
    "临盘决策": _BASE_REPORTS.copy(),
    # 反馈闭环不走 full_cycle，只生成决策报告和四段闭环汇总
    "反馈闭环": ["四段闭环汇总", "决策报告"],
}

SUBJECT_PREFIX = {
    "早盘": "天枢权衡·早盘四段闭环",
    "盘中复盘": "天枢权衡·盘中复盘",
    "临盘决策": "天枢权衡·临盘决策",
    "反馈闭环": "天枢权衡·反馈闭环",
}


def main():
    parser = argparse.ArgumentParser(description="天枢权衡邮件发送")
    parser.add_argument("--type", required=True,
                        choices=["早盘", "盘中复盘", "临盘决策", "反馈闭环"],
                        help="报告类型")
    parser.add_argument("--to", default="user@example.com",
                        help="收件人邮箱")
    parser.add_argument("--data-dir",
                        default="~/hermes-data/tianshu-quanheng/data/历史记录",
                        help="报告文件目录")
    args = parser.parse_args()

    report_type = args.type
    today = datetime.now().strftime("%Y-%m-%d")
    data_dir = os.path.expanduser(args.data_dir)

    # 读取报告文件
    parts = []
    for report_name in REPORT_FILES.get(report_type, []):
        fname = os.path.join(data_dir, f"{today}_{report_name}.md")
        if not os.path.exists(fname):
            print(f"  - {report_name} (不存在)")
            continue
        try:
            content = Path(fname).read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError) as e:
            print(f"  ⚠ {report_name} (读取失败: {e})")
            continue
        if content:
            parts.append(content)
            print(f"  ✓ {report_name} ({len(content)} chars)")
        else:
            print(f"  - {report_name} (空)")

    if not parts:
        print(f"❌ 未读取到任何报告文件（{data_dir}），邮件未发送")
        return 1

    # 组合成完整报告 Markdown
    full_md = "\n\n---\n\n".join(parts)

    # 转换为 HTML（md_to_html 自动处理表格、列表等，不暴露 markdown 原始语法）
    subject = f"{SUBJECT_PREFIX[report_type]} {today}"
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    html_body = md_to_html(full_md, title=f"天枢权衡 · {report_type}",
                           generated_at=generated_at)

    # 发送邮件
    print(f"\n📧 发送邮件: {subject} → {args.to}")
    ok = send_email(subject, html_body, recipient=args.to, skip_lock=True)

    if ok:
        print(f"✅ 邮件发送成功: {subject}")
        return 0
    else:
        print(f"❌ 邮件发送失败: {subject}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
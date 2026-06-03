# 天枢权衡 Discord Bot

Discord 机器人，集成天枢权衡看板，提供：
- 📡 定时推送：S级操作池 + 重点观察池状态（每15分钟）
- 💬 斜杠命令：`/天枢状态` `/天枢持仓` `/天枢行情` `/天枢告警` `/天枢帮助`
- 🚨 告警推送：S级操作池实时告警

## 快速启动

```bash
# 1. 进入目录
cd /home/seven/hermes-data/tianshu-quanheng/discord-bot

# 2. 启动 Bot
./start.sh

# 或者手动启动
source .venv/bin/activate
python tianshu_bot.py
```

## 斜杠命令

| 命令 | 功能 |
|------|------|
| `/天枢状态` | 查看五池完整状态 |
| `/天枢持仓` | 查看持仓池明细和盈亏 |
| `/天枢行情` | 查看大盘行情 |
| `/天枢告警` | 查看未处理告警 |
| `/天枢帮助` | 查看所有命令说明 |

## 配置

编辑 `.env` 文件：

```env
DISCORD_BOT_TOKEN=你的BotToken
DISCORD_CHANNEL_ID=目标频道ID
PROXY_URL=http://192.168.197.109:7897
PUSH_INTERVAL_MINUTES=15
ALERT_THRESHOLD=-3.0
```

## 依赖

- discord.py >= 2.3.0
- python-dotenv >= 1.0.0
- requests >= 2.31.0

## 前提条件

1. **Dashboard 服务必须运行**：`hermes-data/tianshu-quanheng/dashboard/app.py`
2. **Bot Token**：在 Discord Developer Portal 创建应用并获取 Token
3. **代理配置**：国内环境需要 HTTP 代理

## 日志

日志文件：`tianshu_bot.log`

查看日志：
```bash
tail -f tianshu_bot.log
```

## 停止

按 `Ctrl+C` 停止 Bot。

## 项目结构

```
discord-bot/
├── .env              # 配置文件（勿提交git）
├── .env.example      # 配置模板
├── requirements.txt  # Python 依赖
├── start.sh          # 启动脚本
├── tianshu_bot.py    # 主程序
└── tianshu_bot.log   # 运行日志
```

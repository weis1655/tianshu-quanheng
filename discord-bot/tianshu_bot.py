#!/usr/bin/env python3
"""
天枢权衡 Discord Bot
功能：
  - 定时推送：S级操作池 + 重点观察池状态（每15分钟）
  - 斜杠命令：/天枢状态 /天枢持仓 /天枢行情 /天枢告警
  - 告警推送：S级操作池实时告警
"""

import os
import sys
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List, Any

import discord
from discord import app_commands
from dotenv import load_dotenv

# ==================== 配置 ====================
load_dotenv()

TOKEN = os.getenv('DISCORD_BOT_TOKEN')
CHANNEL_ID = int(os.getenv('DISCORD_CHANNEL_ID', '0'))
PROXY_URL = os.getenv('PROXY_URL', '')
PUSH_INTERVAL_MINUTES = int(os.getenv('PUSH_INTERVAL_MINUTES', '15'))
ALERT_THRESHOLD = float(os.getenv('ALERT_THRESHOLD', '-3.0'))
BOT_NAME = os.getenv('BOT_NAME', '天枢智能助手')
BOT_VERSION = os.getenv('BOT_VERSION', 'v1.0.0')

# 天枢项目路径
TIANSHU_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(TIANSHU_ROOT))
sys.path.insert(0, str(TIANSHU_ROOT / "agents"))

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('tianshu_bot.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# ==================== Dashboard API 客户端 ====================
import requests

DASHBOARD_URL = "http://localhost:8765"


def fetch_dashboard_data() -> Optional[Dict]:
    """从 dashboard API 获取看板数据"""
    try:
        resp = requests.get(f"{DASHBOARD_URL}/api/dashboard?silent=true", timeout=10)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        logger.error(f"获取 dashboard 数据失败: {e}")
    return None


def fetch_market_data() -> Optional[Dict]:
    """获取大盘行情"""
    try:
        resp = requests.get(f"{DASHBOARD_URL}/api/market", timeout=10)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        logger.error(f"获取行情失败: {e}")
    return None


def trigger_alerts() -> Optional[Dict]:
    """手动触发告警检查"""
    try:
        resp = requests.get(f"{DASHBOARD_URL}/api/alerts/trigger", timeout=10)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        logger.error(f"触发告警失败: {e}")
    return None


# ==================== Bot 类 ====================

class TianshuBot(discord.Client):
    """天枢 Discord 机器人"""
    
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        
        super().__init__(
            intents=intents,
            proxy=PROXY_URL,
            proxy_auth=None
        )
        self.tree = app_commands.CommandTree(self)
        self.channel: Optional[discord.TextChannel] = None
        self.push_task: Optional[asyncio.Task] = None
        logger.info(f"✅ 使用代理: {PROXY_URL}")
    
    async def setup(self):
        """设置命令树"""
        await self.tree.sync()
        logger.info("✅ 命令树同步完成")
    
    async def find_channel(self) -> Optional[discord.TextChannel]:
        """找到目标频道"""
        # 尝试直接通过 ID 获取
        try:
            channel = await self.fetch_channel(CHANNEL_ID)
            if isinstance(channel, discord.TextChannel):
                return channel
        except discord.NotFound:
            pass
        
        # 遍历所有服务器频道
        for guild in self.guilds:
            for channel in guild.text_channels:
                if channel.id == CHANNEL_ID:
                    return channel
        
        return None


class TianshuCommands:
    """天枢斜杠命令"""
    
    def __init__(self, bot: TianshuBot):
        self.bot = bot
    
    async def register_commands(self):
        """注册所有斜杠命令"""
        
        @self.bot.tree.command(
            name="天枢状态",
            description="查看天枢五池完整状态"
        )
        async def status_command(interaction: discord.Interaction):
            """天枢状态命令"""
            await interaction.response.send_message("⏳ 正在获取数据...", ephemeral=True)
            
            data = fetch_dashboard_data()
            if not data:
                await interaction.edit_original_response(
                    content="❌ 无法获取天枢数据，请确认 dashboard 服务已启动"
                )
                return
            
            embed = self._build_status_embed(data)
            await interaction.edit_original_response(embed=embed, content=None)
        
        @self.bot.tree.command(
            name="天枢持仓",
            description="查看持仓池明细和盈亏"
        )
        async def holding_command(interaction: discord.Interaction):
            """持仓命令"""
            await interaction.response.send_message("⏳ 正在获取数据...", ephemeral=True)
            
            data = fetch_dashboard_data()
            if not data:
                await interaction.edit_original_response(
                    content="❌ 无法获取天枢数据"
                )
                return
            
            embed = self._build_holding_embed(data.get("pools", {}).get("持仓池", {}))
            await interaction.edit_original_response(embed=embed, content=None)
        
        @self.bot.tree.command(
            name="天枢行情",
            description="查看大盘行情"
        )
        async def market_command(interaction: discord.Interaction):
            """行情命令"""
            await interaction.response.send_message("⏳ 正在获取行情...", ephemeral=True)
            
            market = fetch_market_data()
            if not market or not market.get("market"):
                await interaction.edit_original_response(
                    content="❌ 无法获取行情数据"
                )
                return
            
            embed = self._build_market_embed(market)
            await interaction.edit_original_response(embed=embed, content=None)
        
        @self.bot.tree.command(
            name="天枢告警",
            description="查看未处理告警"
        )
        async def alert_command(interaction: discord.Interaction):
            """告警命令"""
            await interaction.response.send_message("⏳ 正在检查告警...", ephemeral=True)
            
            result = trigger_alerts()
            if not result or not result.get("triggered"):
                await interaction.edit_original_response(
                    content="✅ 当前无新告警"
                )
                return
            
            alerts = result.get("triggered", [])
            embed = discord.Embed(
                title="🚨 天枢告警",
                description=f"共 {len(alerts)} 条告警",
                color=discord.Color.red()
            )
            for alert in alerts[:10]:
                if alert.get("type") == "holding_alert":
                    embed.add_field(
                        name=f"⚠️ 持仓警戒: {alert.get('stock')}",
                        value=f"跌幅: {alert.get('change', 0):.2f}%",
                        inline=False
                    )
                elif alert.get("type") == "screen_hit":
                    embed.add_field(
                        name=f"🎯 快筛命中: {alert.get('theme')}",
                        value=f"数量: x{alert.get('count')}",
                        inline=False
                    )
            
            await interaction.edit_original_response(embed=embed, content=None)
        
        @self.bot.tree.command(
            name="天枢帮助",
            description="查看所有可用命令"
        )
        async def help_command(interaction: discord.Interaction):
            """帮助命令"""
            embed = discord.Embed(
                title=f"📖 {BOT_NAME} - 帮助中心",
                description="""
**斜杠命令：**
• `/天枢状态` - 查看五池完整状态
• `/天枢持仓` - 查看持仓池明细和盈亏
• `/天枢行情` - 查看大盘行情
• `/天枢告警` - 查看未处理告警

**自动推送：**
• 每15分钟推送 S级操作池 + 重点观察池
• S级操作池告警实时推送

**数据来源：**
天枢权衡看板 (dashboard)
                """,
                color=discord.Color.blue()
            )
            embed.set_footer(text=f"{BOT_NAME} {BOT_VERSION}")
            await interaction.response.send_message(embed=embed, ephemeral=True)
        
        logger.info("✅ 所有天枢命令注册完成")
    
    # ========== Embed 构建器 ==========
    
    def _build_status_embed(self, data: Dict) -> discord.Embed:
        """构建状态 Embed"""
        pools = data.get("pools", {})
        timestamp = data.get("timestamp", "未知")
        
        embed = discord.Embed(
            title="🏛️ 天枢权衡 · 五池快照",
            description=f"📅 {timestamp}",
            color=discord.Color.blue()
        )
        
        # S级操作池
        s_pool = pools.get("S级操作池", {})
        stocks = s_pool.get("stocks", [])
        if stocks:
            stock_list = "\n".join([
                f"• `{s.get('code', '?')}` {s.get('name', '?')} | {s.get('current_price', 0):.2f} ({s.get('change_pct', 0):+.2f}%)"
                for s in stocks[:5]
            ])
            embed.add_field(
                name=f"🔥 S级操作池 ({len(stocks)})",
                value=stock_list,
                inline=False
            )
        else:
            embed.add_field(
                name="🔥 S级操作池 (0)",
                value="暂无标的",
                inline=False
            )
        
        # 重点观察池
        watch_pool = pools.get("重点观察池", {})
        stocks = watch_pool.get("stocks", [])
        if stocks:
            stock_list = "\n".join([
                f"• `{s.get('code', '?')}` {s.get('name', '?')} | {s.get('current_price', 0):.2f} ({s.get('change_pct', 0):+.2f}%)"
                for s in stocks[:5]
            ])
            embed.add_field(
                name=f"👁️ 重点观察池 ({len(stocks)})",
                value=stock_list,
                inline=False
            )
        else:
            embed.add_field(
                name="👁️ 重点观察池 (0)",
                value="暂无标的",
                inline=False
            )
        
        # 其他池统计
        other_pools = ["快筛候选池", "边缘池", "持仓池"]
        for name in other_pools:
            pool = pools.get(name, {})
            count = pool.get("count", 0)
            embed.add_field(
                name=f"📊 {name}",
                value=f"{count} 只",
                inline=True
            )
        
        embed.set_footer(text=f"自动推送间隔: {PUSH_INTERVAL_MINUTES}分钟")
        return embed
    
    def _build_holding_embed(self, pool_data: Dict) -> discord.Embed:
        """构建持仓 Embed"""
        stocks = pool_data.get("stocks", [])
        
        embed = discord.Embed(
            title="💼 持仓池明细",
            description=f"共 {len(stocks)} 只持仓",
            color=discord.Color.green()
        )
        
        if not stocks:
            embed.add_field(name="💡 提示", value="当前无持仓", inline=False)
            return embed
        
        for s in stocks[:10]:
            code = s.get("code", s.get("代码", "?"))
            name = s.get("name", s.get("名称", "?"))
            price = s.get("current_price", s.get("现价", 0))
            change = s.get("change_pct", s.get("涨跌幅", 0))
            pnl = s.get("pnl_pct", s.get("盈亏比例", 0))
            
            color = discord.Color.green() if change >= 0 else discord.Color.red()
            embed.add_field(
                name=f"`{code}` {name}",
                value=f"现价: {price:.2f} | 涨跌: {change:+.2f}% | 盈亏: {pnl:+.2f}%",
                inline=False
            )
        
        return embed
    
    def _build_market_embed(self, market_data: Dict) -> discord.Embed:
        """构建行情 Embed"""
        quotes = market_data.get("market", [])
        
        embed = discord.Embed(
            title="📈 大盘行情",
            description=f"📅 {market_data.get('timestamp', '未知')}",
            color=discord.Color.orange()
        )
        
        for q in quotes:
            name = q.get("name", "?")
            price = q.get("price", 0)
            change = q.get("change_pct", 0)
            color = discord.Color.green() if change >= 0 else discord.Color.red()
            
            embed.add_field(
                name=name,
                value=f"{price:.2f} ({change:+.2f}%)",
                inline=True
            )
        
        return embed


# ==================== 事件处理 ====================

async def setup_events(bot: TianshuBot):
    """设置事件处理器"""
    
    @bot.event
    async def on_ready():
        logger.info(f"""
╔══════════════════════════════════════╗
║  🤖 {BOT_NAME} 已上线！
║  版本：{BOT_VERSION}
║  用户：{bot.user}
║  ID：{bot.user.id}
╚══════════════════════════════════════╝
        """)
        await bot.setup()
        
        # 找到目标频道
        bot.channel = await bot.find_channel()
        if bot.channel:
            logger.info(f"✅ 已连接到频道: {bot.channel.name} (ID: {bot.channel.id})")
        else:
            logger.warning(f"⚠️ 未找到频道 ID: {CHANNEL_ID}")
        
        # 启动定时推送任务
        if bot.channel:
            bot.push_task = asyncio.create_task(push_loop(bot))
            logger.info(f"📡 定时推送已启动 (每{PUSH_INTERVAL_MINUTES}分钟)")
    
    @bot.event
    async def on_message(message: discord.Message):
        """处理消息事件"""
        if message.author.bot:
            return
        
        # 兼容旧式命令
        if message.content.startswith('/天枢'):
            await message.channel.send("💡 请使用斜杠命令：输入 `/` 后选择命令")


async def push_loop(bot: TianshuBot):
    """定时推送循环"""
    interval_seconds = PUSH_INTERVAL_MINUTES * 60
    
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            
            if not bot.channel:
                continue
            
            # 获取数据
            data = fetch_dashboard_data()
            if not data:
                logger.warning("⚠️ 获取 dashboard 数据失败，跳过推送")
                continue
            
            # 构建 Embed
            commands = TianshuCommands(bot)
            embed = commands._build_status_embed(data)
            
            # 发送消息
            try:
                await bot.channel.send(embed=embed)
                logger.info(f"📤 定时推送成功: {embed.title}")
            except discord.Forbidden:
                logger.error("❌ 无权限发送消息到目标频道")
                break
            except Exception as e:
                logger.error(f"❌ 推送失败: {e}")
        
        except asyncio.CancelledError:
            logger.info("🛑 推送任务已取消")
            break
        except Exception as e:
            logger.error(f"❌ 推送循环错误: {e}")
            await asyncio.sleep(60)  # 出错后等待1分钟再试


# ==================== 主函数 ====================

async def main():
    """主函数"""
    logger.info("🚀 启动天枢 Discord Bot...")
    
    if not TOKEN:
        logger.error("❌ DISCORD_BOT_TOKEN 未设置！")
        sys.exit(1)
    
    bot = TianshuBot()
    commands = TianshuCommands(bot)
    await setup_events(bot)
    
    await commands.register_commands()
    
    try:
        await bot.start(TOKEN)
    except discord.LoginFailure:
        logger.error("❌ Token 无效！请检查 DISCORD_BOT_TOKEN")
        sys.exit(1)
    except Exception as e:
        logger.error(f"❌ 启动失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    print(f"""
    ╔════════════════════════════════════════╗
    ║     天枢权衡 Discord Bot               ║
    ║     {BOT_NAME} {BOT_VERSION}              ║
    ║     推送间隔: {PUSH_INTERVAL_MINUTES}分钟                       ║
    ╚════════════════════════════════════════╝
    """)
    import asyncio
    asyncio.run(main())

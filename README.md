# 🏛️ 天枢权衡 — Tianshu Quanheng

> A multi-agent stock screening and decision analysis system powered by LLM.

天枢权衡 is an automated A-share stock analysis pipeline that processes financial news, screens candidates, performs deep reviews, applies skeptical gate checks, and generates structured investment decision reports — all through a collaborative multi-agent architecture.

**Disclaimer:** This is a personal investment research tool for educational and reference use. It does not constitute financial advice. All outputs are for reference only. Past performance does not guarantee future results.

---

## Architecture

```
News Feed → NewsAgent → ScreenAgent → ReviewAgent → SkepticGate → DecisionAgent → Feedback Loop
```

### Pipeline Stages

| Stage | Agent | Description |
|-------|-------|-------------|
| **① News** | `news_agent.py` | Scrapes and analyzes financial news, extracts sector catalysts |
| **② Screen** | `screen_agent.py` | Fast candidate screening based on news-driven factors |
| **③ Review** | `review_agent.py` | Deep 4-dimension review: catalyst, position, volume, risk |
| **④ Gate** | `skeptic_agent.py` | 5-dimension skeptical challenge: earnings, valuation, competition, positioning, volume-price |
| **⑤ Decision** | `decision_agent.py` | Generates actionable execution plans with stop-loss/take-profit |
| **⑥ Feedback** | `feedback_loop.py` | Closes the loop: records outcomes, adjusts weights |

### Pool System

Candidates flow through 5 pools:

```
Screening Pool →(score ≥75)→ Watch Pool →(buy)→ Holdings Pool
                    ↓(score <65)
                 Fringe Pool
                    
S-level Pool ──(priority read)→ Decision Layer (T+1 expiry)
```

## Key Features

- **Multi-Agent Pipeline** — News → Screen → Review → Skeptic → Decision, each agent has independent responsibilities
- **Skeptic Gate** — A dedicated adversarial agent challenges every candidate across 5 dimensions before approval
- **Automated Scoring** — 4-dimension review scoring (catalyst 25%, position 35%, volume 20%, risk 20%) with overheat detection
- **ML Scoring** — RandomForest model provides parallel ML-based scores alongside LLM scores for comparison (experimental, not used in actual decisions)
- **5-Pool Management** — Automatic pool transitions with capacity limits, time decay, and three-strike elimination rules
- **Automated Reports** — Generates daily morning reports, intraday reviews, and end-of-day feedback summaries
- **Email/Slack/Discord Delivery** — Results delivered via email or chat platforms

## Quick Start

### Prerequisites

- Python 3.11+
- An LLM API endpoint (supports OpenAI-compatible APIs, SenseNova, etc.)

### Setup

```bash
# Clone
git clone https://github.com/weis1655/tianshu-quanheng.git
cd tianshu-quanheng

# Install dependencies
pip install -r requirements.txt

# Configure
cp config.yaml.example config.yaml
# Edit config.yaml with your API credentials

# Run full pipeline
python main.py full

# Or individual stages
python main.py news       # News analysis only
python main.py screen     # Screening only
python main.py review     # Deep review only
python main.py decision   # Decision only
python main.py status     # Pool status
```

### Configuration

Configure via `config.yaml`:

```yaml
api:
  opencode_url: "https://your-api-endpoint/v1/chat/completions"
  opencode_key: "${OPENCODE_API_KEY}"      # via environment variable
  default_model: "your-model-name"
  llm:
    temperature: 0.3
    max_tokens: 1000
    timeout: 60
    max_retries: 3
```

All secrets should be set via environment variables (`.env` file):

```bash
OPENCODE_API_KEY=your_api_key_here
```

## Project Structure

```
tianshu-quanheng/
├── main.py                    # Entry point
├── config.yaml                # Configuration
├── agents/                    # Core agent modules
│   ├── news_agent.py          # News analysis
│   ├── screen_agent.py        # Fast screening
│   ├── review_agent.py        # Deep review
│   ├── skeptical_agent.py     # Skeptic gate
│   ├── decision_agent.py      # Decision generation
│   ├── pool_manager.py        # Pool management
│   ├── market_agent.py        # Market data fetching
│   ├── feedback_loop.py       # Feedback loop
│   ├── gate_controller.py     # Cross-pool gate
│   ├── quality_gate.py        # Quality gate
│   └── ...                    # Utilities and helpers
├── scripts/                   # Utility scripts
│   ├── calc_win_rate.py       # Win rate calculation
│   ├── ml_model_train.py      # ML model training
│   ├── ml_scorer.py           # ML inference
│   └── ...
├── dashboard/                 # Web dashboard
│   └── app.py                 # FastAPI dashboard
├── discord-bot/               # Discord bot integration
│   └── tianshu_bot.py
└── data/                      # Runtime data (gitignored)
    ├── ml_model/              # ML model artifacts
    └── ...
```

## Configuration Parameters

### Scoring Thresholds

| Score | Level | Flow | Description |
|-------|-------|------|-------------|
| ≥90 | S-level | Upgrade | Exceptional opportunity |
| 75-89 | A-level | Upgrade | Watch-worthy |
| 65-74 | B-level | Hold candidate | Cautious observation |
| 55-64 | C-level | Downgrade to fringe | Pause |
| <55 | D-level | Eliminate | Veto |

### Overheat Detection

| Level | Condition | Penalty |
|-------|-----------|---------|
| 🔥 CRITICAL | Daily gain >12% + (PE>80 or turnover>12%) | Force downgrade, -30 pts |
| ⚠️ WARNING-1 | Daily gain >8% + score >75 | -10 pts |
| ⚠️ WARNING-2 | Daily gain ≥10% | -5 pts |
| ⚠️ WARNING-3 | Daily gain >5% + volume ratio >3 | -5 pts |

## ML Scoring (Experimental)

RandomForest classifier trained on historical review data (125+ records, 9 features). Feature importance ranking:

1. **vol20** (20-day volatility) — 0.170
2. **day_range** (daily amplitude) — 0.147
3. **ret20** (20-day return) — 0.138
4. **ma10_div** (10-day MA deviation) — 0.125
5. **LLM score** — 0.052 (lowest)

> ML scores are displayed for reference and do not influence actual decisions.

## Disclaimer

**⚠️ IMPORTANT**

This project is developed for **personal educational and research purposes only**. It is NOT intended to be used as:

- Financial advice or investment recommendation
- A trading signal generator for real money
- A substitute for professional financial analysis

The system's accuracy is limited — LLM-based scoring shows ~48% win rate for ≥70 scores (near coin-flip). ML scores have ~65% cross-validation accuracy. **Do not use this system for actual trading without thorough independent validation.**

Stock data is sourced from public APIs (Sina Finance). No live trading execution is performed by this system.

## License

MIT
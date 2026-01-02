# Oura Daily Optimization Agent

## IMPORTANT: Do Not Run Destructive Commands in Production

**NEVER run `/clear`, `reset_baselines`, or any destructive commands against the production webhook or Modal volume.** The user's actual health data is stored there.

When testing:
- Use local pytest tests (they use temp directories)
- Do NOT call the production webhook with `/clear`
- Do NOT delete files from the Modal volume

## Project Overview

A personal health optimization agent that:
1. Pulls biometric data from Oura Ring API daily at 10 AM EST
2. Analyzes sleep, readiness, HRV against personal baselines
3. Generates actionable recommendations using Claude Opus 4.5
4. Sends morning brief via Telegram
5. Accepts intervention logging via Telegram replies (natural language)
6. Tracks interventions and correlates with outcomes over time

## Tech Stack

| Component | Technology | Purpose |
|-----------|------------|---------|
| AI Model | Claude Opus 4.5 (`claude-opus-4-5-20251101`) | Analysis & recommendations |
| Hosting | Modal (serverless) | Daily cron execution |
| Data Source | Oura Ring API | Biometric data |
| Notifications | Telegram Bot API | Morning briefs + intervention logging |
| Storage | Modal Volume (`/data`) | Encrypted persistent storage |

## Data Architecture

### Retention Windows

| Data Type | Retention | Purpose |
|-----------|-----------|---------|
| Raw Oura API responses | 28 days | Detailed data for trend analysis |
| Extracted metrics | 28 days | Summary metrics for historical context |
| Interventions | 28 days | Correlation with outcomes |
| Briefs | 28 days | Continuity and reference |
| Baselines | 60-day rolling | Rolling average of recent 60 days (includes recent data) |

### Directory Structure

```
oura_agent/                      # Git repository
├── modal_agent.py               # Main Modal function + agent logic
├── CLAUDE.md                    # This file - project context
├── profile.example.json         # User preferences template
├── requirements.txt             # Python dependencies
├── .env                         # Local credentials (gitignored)
└── .gitignore

/data/                           # Modal Volume (persistent, encrypted, NOT in git)
├── baselines.json               # 60-day rolling averages
├── raw/                         # Raw Oura API responses (28 days)
│   └── YYYY-MM-DD.json
├── metrics/                     # Extracted daily metrics (28 days)
│   └── YYYY-MM-DD.json
├── briefs/                      # Generated morning briefs (28 days)
│   └── YYYY-MM-DD.md
└── interventions/               # Logged interventions (28 days)
    └── YYYY-MM-DD.jsonl         # JSONL format (one entry per line)
```

## Credentials

| Secret Name | Variables | Source |
|-------------|-----------|--------|
| `anthropic` | `ANTHROPIC_API_KEY` | console.anthropic.com |
| `oura` | `OURA_ACCESS_TOKEN` | cloud.ouraring.com → Personal Access Tokens |
| `telegram` | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_WEBHOOK_SECRET` | @BotFather, getUpdates API, generate random string |

### Webhook Security

The `TELEGRAM_WEBHOOK_SECRET` is used to authenticate incoming webhook requests. When setting up the webhook with Telegram, include this secret:

```bash
curl -X POST "https://api.telegram.org/bot<BOT_TOKEN>/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "<YOUR_MODAL_WEBHOOK_URL>",
    "secret_token": "<YOUR_WEBHOOK_SECRET>"
  }'
```

Telegram will send this token in the `X-Telegram-Bot-Api-Secret-Token` header with every request. The webhook validates this header and rejects requests with invalid or missing tokens (when the secret is configured).

## Schedule

- **Daily**: 10 AM EST (15:00 UTC) - `0 15 * * *`

## AI Analysis Guidelines

Claude Opus 4.5 receives verbose context and makes dynamic, context-aware decisions rather than applying rigid thresholds.

### Context Sent to Claude

1. **Last night's detailed sleep** - 41 fields including HR/HRV trends, sleep architecture, readiness contributors
2. **28 days of historical metrics** - Daily summary for trend analysis
3. **28 days of interventions** - Cleaned/normalized text for correlation analysis
4. **60-day baselines** - Mean ± std for each metric
5. **Last 3 briefs** - For continuity

Note: Baselines are a rolling 60-day average that includes recent data. They represent "your typical values" rather than a pre-intervention baseline. Compare today's metrics to baselines to see if you're above/below your personal norm.

### Example Guardrails (Reference, Not Absolutes)

| Metric | Suggested Alert Level | Notes |
|--------|----------------------|-------|
| Readiness | <60 might indicate recovery day | Context matters |
| HRV | >1.5σ below baseline for 3+ days | Consider trend direction |
| Deep sleep | <45 min warrants investigation | Varies by individual |
| Temperature | >0.5°C deviation | Could indicate illness |
| Sleep efficiency | <80% is suboptimal | Correlate with next-day readiness |
| RHR | >2σ above baseline | Could be stress, illness, or overtraining |

## Intervention Tracking

### How It Works

1. **User logs intervention** via Telegram (natural language)
2. **Claude cleans/normalizes the input** immediately
3. **Both raw and cleaned versions are stored**
4. **Future agents see the cleaned version** for correlation analysis

### Intervention File Format

Uses JSONL (JSON Lines) format for atomic appends. Each line is a single entry:

```jsonl
{"time": "19:30", "raw": "took two neuro-mag capsules and one omega3", "cleaned": "Neuro-mag 2 capsules, omega-3 1 capsule"}
{"time": "21:15", "raw": "20 min sauna", "cleaned": "Sauna 20 min"}
```

- `raw`: Original user input (kept for audit, not displayed)
- `cleaned`: Claude-normalized version (used for display and analysis)

### Telegram Bot Commands

```
/log <text>  - Log an intervention (or just type naturally)
/status      - Show today's interventions
/brief       - Show the latest morning brief
/clear       - Clear today's interventions
/help        - Show available commands
```

Or just message naturally (no command needed):
```
took 2 magnesium capsules
20 min sauna session
glass of wine with dinner
```

## CLI Commands

```bash
# Deploy to Modal
modal deploy modal_agent.py

# Run manually
modal run modal_agent.py

# Backfill historical data (run once to bootstrap baselines)
modal run modal_agent.py::backfill_history --days 90

# Reset baselines
modal run modal_agent.py::reset_baselines

# View history
modal run modal_agent.py::view_history --days 7
```

## Oura API Reference

Base URL: `https://api.ouraring.com/v2/usercollection`
Auth: `Authorization: Bearer {OURA_ACCESS_TOKEN}`

| Endpoint | Data | Date Convention |
|----------|------|-----------------|
| `daily_sleep` | Sleep score, contributors | Day you woke up |
| `daily_readiness` | Readiness score, temp deviation | Day you woke up |
| `daily_activity` | Activity score, steps | Calendar day |
| `sleep` | Detailed sleep data (HR, HRV, stages) | Day sleep STARTED |

**Important**: The `sleep` endpoint uses the date sleep started, not ended. To get last night's sleep for a morning brief on Jan 1, fetch sleep data for Dec 31-Jan 1 and find the session that ended on Jan 1.

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
| Baselines | 60-day rolling | "Normal" reference |

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
    └── YYYY-MM-DD.json
```

## Credentials

| Secret Name | Variables | Source |
|-------------|-----------|--------|
| `anthropic` | `ANTHROPIC_API_KEY` | console.anthropic.com |
| `oura` | `OURA_ACCESS_TOKEN` | cloud.ouraring.com → Personal Access Tokens |
| `telegram` | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | @BotFather, getUpdates API |

## Schedule

- **Daily**: 10 AM EST (15:00 UTC) - `0 15 * * *`

## AI Analysis Guidelines

Claude Opus 4.5 receives verbose context and makes dynamic, context-aware decisions rather than applying rigid thresholds.

### Context Sent to Claude

1. **Last night's detailed sleep** - 41 fields including HR/HRV trends, sleep architecture, readiness contributors
2. **28 days of historical metrics** - Daily summary for trend analysis
3. **28 days of interventions** - For correlation analysis
4. **60-day baselines** - Mean ± std for each metric
5. **Last 3 briefs** - For continuity

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
2. **Raw text is stored immediately** with timestamp
3. **Claude parses during daily brief** and saves structured data back
4. **Future agents can use parsed data** for correlation analysis

### Intervention File Format

```json
{
  "date": "2026-01-01",
  "entries": [
    {
      "time": "19:30",
      "raw": "took two neuro-mag capsules and one omega3",
      "parsed": [
        {"type": "supplement", "name": "magnesium", "brand": "neuro-mag", "quantity": 2, "form": "capsule"},
        {"type": "supplement", "name": "omega-3", "quantity": 1, "form": "capsule"}
      ]
    },
    {
      "time": "21:15",
      "raw": "20 min sauna",
      "parsed": null
    }
  ]
}
```

- `raw`: Original user input (always preserved)
- `parsed`: null until Claude processes during daily brief, then array of structured items

### Parsed Item Fields

| Field | Required | Examples |
|-------|----------|----------|
| type | Yes | supplement, behavior, consumption, environment |
| name | Yes | magnesium, sauna, alcohol, room_temp |
| quantity | No | 2, 400, 1 |
| unit | No | mg, min, drinks, °F |
| brand | No | neuro-mag, thorne |
| form | No | capsule, powder, liquid |
| duration | No | 20 min |
| details | No | Any additional context |

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

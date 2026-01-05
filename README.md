# Oura Health Agent

A personal health optimization agent that analyzes your Oura Ring data and sends you actionable daily recommendations via Telegram.

## Features

- **Daily morning briefs** with sleep analysis and recommendations
- **Personal baselines** - compares your metrics against your 60-day rolling averages
- **Natural language logging** - "took magnesium", "20 min sauna", or send photos
- **Intelligent chat agent** - ask anything about your health data with full historical context
- **Intervention tracking** - correlates supplements/activities with sleep outcomes
- **Claude Opus 4.5** with extended thinking for intelligent, context-aware analysis

###  Agent Capabilities

The chat agent uses Claude's tool use to dynamically query your health data: Ask questions like:
- "How has my sleep changed since April?"
- "Compare my HRV this month vs 6 months ago"
- "What was my average readiness last summer?"
- "Show me trends over the past year"

## Tech Stack

| Component | Technology |
|-----------|------------|
| AI Model | Claude Opus 4.5 with extended thinking |
| Hosting | Modal (serverless) |
| Data Source | Oura Ring API |
| Notifications | Telegram Bot |
| Storage | Modal Volume (encrypted) |

## Project Structure

```
oura-agent/
├── modal_agent.py          # Modal entrypoint with decorators
├── oura_agent/             # Python package
│   ├── api/                # Oura API client
│   ├── extraction/         # Metrics extraction
│   ├── storage/            # Baselines, interventions, conversations
│   ├── telegram/           # Telegram client
│   └── claude/             # Claude AI handlers + agent with tools
├── prompts/                # System prompts
│   ├── morning_brief.md    # Morning brief generation
│   ├── chat.md             # Legacy chat handling
│   └── agent.md            # Agent with tools for chat interactions
└── tests/                  # Test suite (134 tests)
```

## Prerequisites

1. **Oura Ring** with active membership (API access required)
2. **Anthropic API key** from [console.anthropic.com](https://console.anthropic.com)
3. **Telegram Bot** (setup instructions below)
4. **Modal account** (free tier works) from [modal.com](https://modal.com)

## Quick Start

Ask your favorite coding agent to setup this repository for you. Or:

### 1. Clone the Repository

```bash
git clone https://github.com/babakd/oura-bot.git
cd oura-bot
```

### 2. Create Telegram Bot

1. Open Telegram and search for `@BotFather`
2. Send `/newbot` and follow the prompts
3. Save the bot token (looks like `123456789:ABCdefGHI...`)
4. Start a chat with your new bot and send any message
5. Get your chat ID:
   ```bash
   curl "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates"
   ```
   Look for `"chat":{"id":123456789}` in the response

### 3. Get Oura API Token

1. Go to [cloud.ouraring.com](https://cloud.ouraring.com)
2. Navigate to **Personal Access Tokens**
3. Create a new token with all scopes
4. Copy the token

### 4. Setup Modal

```bash
# Install Modal CLI
pip install modal

# Authenticate with Modal
modal setup

# Create secrets
modal secret create anthropic ANTHROPIC_API_KEY=sk-ant-...

modal secret create oura OURA_ACCESS_TOKEN=...

modal secret create telegram \
    TELEGRAM_BOT_TOKEN=123456789:ABC... \
    TELEGRAM_CHAT_ID=123456789 \
    TELEGRAM_WEBHOOK_SECRET=$(openssl rand -hex 32)
```

### 5. Deploy

```bash
# Deploy to Modal (starts the daily cron automatically)
modal deploy modal_agent.py

# Backfill historical data (recommended - saves all data for long-term analysis)
modal run modal_agent.py::backfill_history --days 280  # ~9 months

# Test it immediately
modal run modal_agent.py
```

### 6. Setup Telegram Webhook

After deploying, set up the webhook so your bot can receive messages:

```bash
curl -X POST "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://YOUR_MODAL_USERNAME--oura-agent-telegram-webhook.modal.run",
    "secret_token": "<YOUR_WEBHOOK_SECRET>"
  }'
```

## Usage

### Daily Briefs

The agent runs automatically at 10 AM EST every day. You'll receive a Telegram message with:
- Sleep score, HRV, deep sleep, readiness compared to your baselines
- Workout intensity recommendation (1-10)
- Cognitive load recommendation (High/Medium/Low)
- Multi-day trends and pattern insights
- Alerts for concerning deviations

### Logging Interventions

Message your bot naturally:

```
took 400mg magnesium
20 min sauna session
had 2 glasses of wine
45 min strength training
```

Or send a photo of supplements/food - Claude Vision will extract the details.

### Asking Questions

The agent can answer complex questions about your health data by querying the relevant date ranges:

```
How did I sleep last night?
What's my HRV trend over the past 2 weeks?
How did I sleep last month?
Compare this week to last week
What correlates with my good sleep nights?
Show me days where my deep sleep was above average
What did I log yesterday?
```

The agent will show a brief progress message ("📊 Analyzing the month...") while fetching data, then respond with formatted insights.

**Response format:**
- ✅ Above baseline / good
- ⚠️ Below baseline / needs attention
- 🔴 Significantly concerning
- Bold numbers for key metrics
- Summaries instead of raw data dumps

### Bot Commands

```
/status  - Show today's logged interventions
/brief   - Show the latest morning brief
/clear   - Clear today's interventions
/help    - Show available commands
```

### CLI Commands

```bash
# Run morning brief manually
modal run modal_agent.py

# Backfill historical data (all data saved for long-term queries)
modal run modal_agent.py::backfill_history --days 280

# Reset baselines to defaults
modal run modal_agent.py::reset_baselines

# View recent history
modal run modal_agent.py::view_history --days 7

# Check logs
modal app logs oura-agent
```

## Data Retention

| Data Type | Retention | Purpose |
|-----------|-----------|---------|
| Daily metrics | **Unlimited** | Long-term trend analysis |
| Morning briefs | **Unlimited** | Historical reference |
| Interventions | **Unlimited** | Correlation analysis |
| Conversations | 365 days | Chat context |
| Baselines | 60-day rolling | Personal averages |
| Raw Oura API responses | 28 days | Redundant with metrics |

**Note:** The morning brief uses the most recent 28 days of data for daily recommendations, but the chat agent can query your entire history for long-term analysis.

## Cost Estimate

- **Modal**: Free tier includes 30 compute-hours/month (agent uses ~3 hours/month)
- **Anthropic API**:
  - Morning briefs: ~$0.10-0.20/day
  - Chat queries: ~$0.02-0.10 per message (varies by complexity)
  - Extended thinking adds ~2-3x tokens but improves quality significantly
- **Total**: ~$5-15/month depending on chat usage

## Data Privacy

- All health data is stored on Modal's encrypted volumes
- Data never leaves Modal's infrastructure except to Telegram
- No third-party analytics or tracking
- Delete all data anytime: `modal volume delete oura-health-data`

## Development

```bash
# Install dev dependencies
pip install -r requirements.txt

# Run tests
pytest tests/ -v

# Deploy
modal deploy modal_agent.py
```

## Troubleshooting

### "No data returned from Oura"
Oura data syncs when you open the app. Make sure to open the Oura app before the morning brief runs.

### "Telegram message not received"
1. Verify your bot token: `curl https://api.telegram.org/bot<TOKEN>/getMe`
2. Ensure you've started a chat with your bot first
3. Check Modal logs: `modal app logs oura-agent`

### "Webhook not working"
1. Verify the webhook is set:
   ```bash
   curl "https://api.telegram.org/bot<TOKEN>/getWebhookInfo"
   ```
2. Check that `secret_token` matches `TELEGRAM_WEBHOOK_SECRET` in Modal secrets
3. Check Modal logs for 401 errors

## License

MIT

# Oura Daily Optimization Agent

A personal health optimization agent that analyzes your Oura Ring data and sends you actionable daily recommendations via Telegram.

## Features

- Daily morning briefs at 10 AM EST with sleep analysis and recommendations
- Compares your metrics against your personal 60-day rolling baselines
- Natural language intervention logging via Telegram ("took magnesium", "20 min sauna")
- Tracks correlations between interventions and sleep outcomes
- Uses Claude Opus 4.5 for intelligent, context-aware analysis

## Prerequisites

1. **Oura Ring** with active membership (API access required)
2. **Anthropic API key** from [console.anthropic.com](https://console.anthropic.com)
3. **Telegram Bot** (setup instructions below)
4. **Modal account** (free tier works) from [modal.com](https://modal.com)

## Quick Start

### 1. Clone the Repository

```bash
git clone https://github.com/yourusername/oura-agent.git
cd oura-agent
```

### 2. Create Your Profile

```bash
cp profile.example.json profile.json
# Edit profile.json with your preferences
```

### 3. Create Telegram Bot

1. Open Telegram and search for `@BotFather`
2. Send `/newbot` and follow the prompts
3. Save the bot token (looks like `123456789:ABCdefGHI...`)
4. Start a chat with your new bot and send any message
5. Get your chat ID:
   ```bash
   curl "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates"
   ```
   Look for `"chat":{"id":123456789}` in the response

### 4. Get Oura API Token

1. Go to [cloud.ouraring.com](https://cloud.ouraring.com)
2. Navigate to **Personal Access Tokens**
3. Create a new token with all scopes
4. Copy the token

### 5. Setup Modal

```bash
# Install Modal CLI
pip install modal

# Authenticate with Modal
modal setup

# Create secrets (replace with your actual values)
modal secret create anthropic ANTHROPIC_API_KEY=sk-ant-...

modal secret create oura OURA_ACCESS_TOKEN=...

modal secret create telegram \
    TELEGRAM_BOT_TOKEN=123456789:ABC... \
    TELEGRAM_CHAT_ID=123456789 \
    TELEGRAM_WEBHOOK_SECRET=$(openssl rand -hex 32)
```

> **Note**: The webhook secret is used to authenticate incoming requests from Telegram. Save it somewhere—you'll need it when setting up the webhook.

### 6. Deploy

```bash
# Deploy to Modal (starts the daily cron automatically)
modal deploy modal_agent.py

# Test it immediately
modal run modal_agent.py
```

### 7. Setup Telegram Webhook (for intervention logging)

After deploying, set up the webhook so your bot can receive messages. Include the webhook secret for security:

```bash
curl -X POST "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://YOUR_MODAL_USERNAME--oura-agent-telegram-webhook.modal.run",
    "secret_token": "<YOUR_WEBHOOK_SECRET>"
  }'
```

> **Important**: The `secret_token` must match the `TELEGRAM_WEBHOOK_SECRET` you created in step 5. This prevents unauthorized requests to your webhook.

## Usage

### Daily Briefs

The agent runs automatically at 10 AM EST every day. You'll receive a Telegram message with:
- Sleep score, HRV, deep sleep, readiness compared to your baselines
- Workout intensity recommendation (1-10)
- Cognitive load recommendation (High/Medium/Low)
- Multi-day trends and pattern insights
- Alerts for concerning deviations

### Logging Interventions

Just message your Telegram bot naturally:

```
took 400mg magnesium
20 min sauna session
had 2 glasses of wine
late dinner around 10pm
45 min workout
```

Or use commands:
```
/status  - Show today's logged interventions
/brief   - Show the latest morning brief
/help    - Show available commands
```

### CLI Commands

```bash
# Run manually (useful for testing)
modal run modal_agent.py

# Backfill historical data (recommended on first setup)
modal run modal_agent.py::backfill_history --days 90

# Reset baselines to defaults
modal run modal_agent.py::reset_baselines

# View recent history
modal run modal_agent.py::view_history --days 7

# Check logs
modal app logs oura-agent
```

## Configuration

### Profile Settings

Edit `profile.json` to customize:
- Your timezone and sleep targets
- Communication style preferences
- Tracked interventions list
- Primary health goals

### Schedule

The default schedule is 10 AM EST (15:00 UTC). To change it, edit the cron expression in `modal_agent.py`:

```python
schedule=modal.Cron("0 15 * * *"),  # 15:00 UTC = 10:00 AM EST
```

## Cost Estimate

- **Modal**: Free tier includes 30 compute-hours/month (agent uses ~3 hours/month)
- **Anthropic API**: ~$0.10-0.30/day depending on analysis depth
- **Total**: ~$3-10/month

## Data Privacy

- All health data is stored on Modal's encrypted volumes
- Data never leaves Modal's infrastructure except to Telegram (for notifications)
- No third-party analytics or tracking
- Delete all data anytime: `modal volume delete oura-health-data`

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
2. Check that your `secret_token` in the webhook matches `TELEGRAM_WEBHOOK_SECRET` in Modal secrets
3. Check Modal logs for 401 errors: `modal app logs oura-agent`

## License

MIT

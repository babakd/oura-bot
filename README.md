# Oura Health Agent

A personal health optimization agent that analyzes your Oura Ring data and sends you actionable daily recommendations via Telegram.

> **Scope:** This repository is a private, single-user personal tool. It is not
> designed for multi-user processing, distribution, commercialization, or
> medical diagnosis or treatment. Revisit the security, consent, and Oura API
> architecture before expanding that scope.

## Features

- **One daily action card** — a compact, evidence-grounded decision or an explicit
  “follow your normal plan” when the data does not justify a change
- **Deterministic health signals** — computes baseline comparisons, trends,
  provenance, freshness, and data-quality state before Claude selects wording
- **Personal baselines** — compares metrics against correction-safe 60-day rolling averages
- **Natural language logging** — “took magnesium”, “20 min sauna”, or send photos
- **Recoverable intervention clearing** — `/clear` requires confirmation and `/undo`
  can restore the most recent clear
- **Recommendation feedback** — Telegram buttons capture accuracy, usefulness,
  adherence, skips, and rejected recommendation domains for future context
- **Intelligent chat agent** — ask questions about your health data with historical context
- **Configurable Anthropic Claude model** with Opus 4.8 as the privacy-conscious default
- **Streaming chat** — responses stream into a single Telegram message that edits as Claude thinks

### Agent Capabilities

The chat agent uses Claude's tool use to dynamically query your health data. Ask questions like:

- "How has my sleep changed since April?"
- "Compare my HRV this month vs 6 months ago"
- "What was my average readiness last summer?"
- "Show me trends over the past year"

## Tech Stack

| Component | Technology |
|-----------|------------|
| AI Model | Anthropic Claude via `ANTHROPIC_MODEL` (Opus 4.8 by default) |
| Hosting | Modal (serverless) |
| Data Source | Oura Ring API |
| Notifications | Telegram Bot |
| Health-data storage | Modal Volume (`oura-health-data`) |
| Coordination | Modal Dict (`oura-agent-coordination`) for locks, delivery state, and Telegram update claims |

## Project Structure

```
oura-agent/
├── modal_agent.py                    # Scheduled job, webhook, and Modal resources
├── oura_agent/
│   ├── api/oura.py                   # Typed Oura client and endpoint date handling
│   ├── extraction/metrics.py         # Oura response normalization
│   ├── insights.py                   # Deterministic daily insight packet and renderer
│   ├── claude/
│   │   ├── brief_card.py             # Structured daily-card selection + safe fallback
│   │   ├── models.py                 # Model selection and Fable-to-Opus fallback
│   │   └── agent.py                  # Tool-using conversational agent
│   ├── storage/
│   │   ├── baselines.py              # Atomic, correction-safe rolling baselines
│   │   ├── interventions.py          # Immutable events + recoverable clear/undo
│   │   ├── recommendations.py        # Cards, delivery, and feedback ledger
│   │   ├── profile.py                # Shared user context
│   │   └── runs.py                   # Run and Telegram idempotency records
│   └── telegram/client.py            # Splitting, retries, edits, and callbacks
├── prompts/
│   ├── daily_card.md                 # Current scheduled-card selection contract
│   ├── morning_brief.md              # Tool-based brief helper prompt
│   └── agent.md                      # Chat and intervention prompt
├── scripts/
│   ├── setup.py                      # Interactive setup wizard
│   └── doctor.py                     # Non-destructive configuration checks
└── tests/                            # Unit, integration, reliability, and E2E tests
```

## Prerequisites

1. **Oura Ring** with active membership and an existing Oura API credential
2. **Anthropic API key** from [console.anthropic.com](https://console.anthropic.com)
3. **Telegram Bot** (setup instructions below)
4. **Modal account** (free tier works) from [modal.com](https://modal.com)

Oura has deprecated personal access tokens and recommends OAuth2 plus webhooks.
This repository still accepts an existing
`OURA_ACCESS_TOKEN` for backward compatibility, but its OAuth migration is not
yet implemented. Keep a working existing token until that migration is
complete; new users without one do not yet have a supported authentication
path through the setup wizard.

The default model is `claude-opus-4-8`. Fable 5 is opt-in only: it requires
accepting mandatory 30-day provider retention and can refuse health or biology
requests under its eligibility policy. Verify both your workspace and workload
eligibility before setting `ANTHROPIC_MODEL=claude-fable-5`. The shared model
helper retries Fable access errors and refusals on Opus 4.8.

## Quick Start

### 1. Clone and Install

```bash
git clone https://github.com/babakd/oura-bot.git
cd oura-bot
python3 -m pip install -r requirements.txt
```

### 2. Run Setup Wizard

```bash
python3 scripts/setup.py
```

The wizard will guide you through:
- Getting your Anthropic API key
- Validating an existing Oura access token
- Creating your Telegram bot
- Auto-detecting your Telegram chat ID
- Generating a secure webhook secret
- Creating Modal secrets
- Deploying to Modal
- Registering the Telegram webhook


<details>
<summary><b>Manual Setup (Alternative)</b></summary>

If you prefer manual setup instead of the wizard:

#### Create Telegram Bot

1. Open Telegram and search for `@BotFather`
2. Send `/newbot` and follow the prompts
3. Save the bot token (looks like `123456789:ABCdefGHI...`)
4. Start a chat with your new bot and send any message
5. Get your chat ID:
   ```bash
   curl "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates"
   ```
   Look for `"chat":{"id":123456789}` in the response

#### Configure Oura API Access

The application currently reads `OURA_ACCESS_TOKEN`. Existing installations
can continue using a working token. Oura no longer offers new personal access
tokens; OAuth2 support is tracked as required migration work and is not yet
implemented here.

#### Setup Modal

```bash
# Install Modal CLI
python3 -m pip install modal

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

The deployed app uses Opus 4.8 when `ANTHROPIC_MODEL` is absent. To opt into
another model, add `ANTHROPIC_MODEL=<model-id>` to the `anthropic` Modal secret
as well as any local `.env`; review the Fable warning above before selecting it.

#### Deploy

```bash
# Deploy to Modal (starts the daily cron automatically)
modal deploy modal_agent.py

# Optional live run: writes health artifacts and sends a Telegram card
modal run modal_agent.py
```

#### Setup Telegram Webhook

After deploying, set up the webhook so your bot can receive messages:

```bash
curl -X POST "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://YOUR_MODAL_USERNAME--oura-agent-telegram-webhook.modal.run",
    "secret_token": "<YOUR_WEBHOOK_SECRET>"
  }'
```

</details>

## Usage

### Daily Action Card

The default deployment runs at **10:00 AM in `America/New_York`**. Modal applies
that named timezone to the cron, so the schedule follows daylight-saving time.
The Telegram message is intentionally compact and contains:

- one observation chosen from validated Oura signals;
- one optional decision, or an explicit recommendation to keep the normal plan;
- up to two deterministic evidence lines with personal-baseline context;
- confidence plus data-freshness or partial-data status; and
- inline buttons for accuracy, usefulness, adherence, skipping, or “not for me.”

If required Oura data is unavailable, the agent sends a low-confidence
data-quality card instead of inventing a recovery conclusion.

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
/status        - Show today's logged interventions
/brief         - Show the latest daily action card
/regen-brief   - Generate and send a fresh card (useful after a delayed Oura sync)
/profile       - Show personal context shared by daily cards and chat
/clear         - Ask for confirmation before hiding today's interventions
/clear confirm - Confirm the recoverable clear
/undo          - Restore the most recent intervention clear
/help          - Show available commands
```

### Live CLI Commands

These commands target the configured Modal app. A manual run sends a live
Telegram card, and a backfill writes health data and updates baselines. Use
local pytest tests for validation.

```bash
# Generate and send a live daily action card
modal run modal_agent.py

# Backfill historical data into the live volume
modal run modal_agent.py::backfill_history --days 280

# Read recent stored history
modal run modal_agent.py::view_history --days 7

# Check logs
modal app logs oura-agent
```

Destructive baseline-reset, intervention-delete, and volume-delete operations
are intentionally omitted. Do not use them as routine troubleshooting against
production data; prepare and verify a recoverable backup and a maintenance plan
before any destructive operation.

## Data Retention

| Data Type | Retention | Purpose |
|-----------|-----------|---------|
| Daily metrics | **Unlimited** | Long-term trend analysis |
| Daily action cards | **Unlimited** | Historical decisions and evidence |
| Interventions | **Unlimited** | Correlation analysis |
| Recommendation, feedback, and run events | **Unlimited** | Learning context and operational audit |
| Conversations | 365 days | Chat context |
| Baselines | 60-day rolling | Personal averages |
| Raw Oura API responses | 28 days | Redundant with metrics |

**Note:** The daily action card uses the most recent 28 days of data for its
decision packet, while the chat agent can query the retained metric history.

## Cost

Cost depends on current Modal and Anthropic pricing, selected model, chat usage,
backfills, and retry volume. Use the providers' billing dashboards and current
pricing pages rather than relying on a fixed estimate in this repository.

## Data Privacy

This application processes sensitive health and behavioral data. Its actual
data flow is:

1. Oura biometric data is fetched from the Oura API into Modal.
2. Raw responses, extracted metrics, baselines, interventions, rendered
   daily-card artifacts, profile data, recommendation/feedback events, run
   records, and conversation history are stored on the `oura-health-data`
   Modal volume according to the retention table above.
3. Relevant metrics, history, interventions, profile details, conversation
   context, and submitted photos are sent to Anthropic when generating cards,
   answering questions, or interpreting an image. Anthropic's account and model
   retention terms therefore apply. When the active model is configured as
   `claude-fable-5`, its mandatory 30-day provider retention applies; that
   Fable-specific retention statement applies only while Fable is enabled.
4. The `oura-agent-coordination` Modal Dict stores small coordination records
   such as opaque card/update IDs, timestamps, lock ownership, and delivery
   state. Health metrics and prompt context remain in the Volume, not the Dict.
5. Generated cards, chat replies, operational notices, and user-submitted
   Telegram messages or photos pass through Telegram and are subject to
   Telegram's storage and retention behavior.
6. Modal runtime logs and local operational output contain status and error
   information and may include metric values. Treat those logs as health data.
7. Local setup stores credentials in `.env`; optional local backfills store
   health data under `data/`. Both paths are gitignored, but they remain the
   operator's responsibility to protect and back up.

There is no third-party analytics integration in this repository. Revoking an
Oura, Anthropic, or Telegram credential stops future access but does not erase
copies already retained by another provider. Deleting the Modal volume is
irreversible and does not delete messages from Telegram or data retained under
another provider's policy; destructive volume commands are intentionally not
documented here.

## Development

```bash
# Install dev dependencies
python3 -m pip install -r requirements.txt

# Run tests
python3 -m pytest tests/ -v

# Deploy
modal deploy modal_agent.py
```

## Troubleshooting

Run the non-destructive doctor first:

```bash
python3 scripts/doctor.py
```

The doctor checks local credentials, Telegram webhook registration, and Modal
HTTP reachability. It does not send Telegram messages, call `/clear`, or touch
the Modal volume. Run it with the same Python interpreter where the project
requirements were installed. If it reports that `requests` is missing, follow
the interpreter-specific install command it prints.

### "No data returned from Oura"
Oura data syncs when you open the app. Make sure to open the Oura app before the
daily action card runs.

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
3. Check Modal reachability:
   ```bash
   python3 scripts/doctor.py
   ```
4. If Modal reports `workspace ... disabled`, `Resource exhausted`, or `billing cycle spend limit reached`, raise the Modal workspace spend limit or wait for the next billing cycle. Telegram cannot reach the webhook until Modal HTTP is re-enabled.
5. Check Modal logs for 401 errors

## License

MIT

# Oura Agent v2 Plan

## Architecture Overview

```
                                 EXTERNAL SERVICES
    ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
    │    Oura API     │     │   Claude API    │     │  Telegram API   │
    │  (biometrics)   │     │  (Opus 4.5)     │     │    (bot)        │
    └────────┬────────┘     └────────┬────────┘     └────────┬────────┘
             │                       │                       │
    ┌────────┴───────────────────────┴───────────────────────┴────────┐
    │                      MODAL SERVERLESS                            │
    │  ┌──────────────────────────────────────────────────────────┐   │
    │  │                   modal_agent.py                          │   │
    │  │   ┌─────────────┐  ┌─────────────┐  ┌─────────────┐      │   │
    │  │   │ morning_    │  │ telegram_   │  │ backfill_   │      │   │
    │  │   │ brief()     │  │ webhook()   │  │ history()   │      │   │
    │  │   │ [CRON]      │  │ [HTTP]      │  │ [CLI]       │      │   │
    │  │   └─────────────┘  └─────────────┘  └─────────────┘      │   │
    │  └───────────────────────────────────────────────────────────┘   │
    │  ┌───────────────────────────────────────────────────────────┐  │
    │  │              Modal Volume: /data (encrypted)               │  │
    │  │  raw/ metrics/ briefs/ interventions/ conversations/       │  │
    │  │  baselines.json                                            │  │
    │  └───────────────────────────────────────────────────────────┘  │
    └──────────────────────────────────────────────────────────────────┘
```

---

## Issues to Address

### 1. Dead Code: Evening Brief (APPROVED TO REMOVE)

~200 lines of fully implemented but disabled code.

**Delete:**
- `evening_brief()` function
- `generate_evening_brief_with_claude()` function
- `EVENING_SYSTEM_PROMPT` constant

---

### 2. No Retry Logic for External APIs

If Oura or Telegram APIs fail, the entire morning brief fails.

**Fix:** Add retry with exponential backoff for:
- `fetch_oura_data()`
- `send_telegram()`

---

### 3. Single-File Monolith (2700+ lines)

All logic in one file. Mixed concerns.

**Proposed module structure:**
```
oura_agent/
├── modal_agent.py              # Entry points only (~200 lines)
│   - morning_brief(), telegram_webhook(), backfill_history()
│   - Modal decorators and app configuration
│
├── api/
│   ├── __init__.py
│   └── oura.py                 # Oura API fetching (~200 lines)
│       - fetch_oura_data() with retry
│       - get_oura_daily_data(), get_oura_sleep_data()
│       - get_oura_activity_data(), get_oura_heartrate()
│
├── extraction/
│   ├── __init__.py
│   ├── sleep.py                # Sleep metrics extraction (~150 lines)
│   │   - extract_sleep_metrics(), extract_detailed_sleep()
│   └── activity.py             # Activity metrics extraction (~100 lines)
│       - extract_activity_metrics(), extract_detailed_workouts()
│
├── analysis/
│   ├── __init__.py
│   ├── brief_generator.py      # Brief generation (~200 lines)
│   │   - generate_brief_with_claude()
│   │   - Uses prompts from prompts/ directory
│   └── chat_handler.py         # Chat & intervention handling (~300 lines)
│       - handle_message(), clean_intervention_with_claude()
│       - format_intervention_response(), analyze_photo_with_claude()
│
├── storage/
│   ├── __init__.py
│   ├── baselines.py            # Baseline management (~150 lines)
│   │   - load_baselines(), update_baselines(), get_default_baselines()
│   ├── metrics.py              # Metrics I/O (~100 lines)
│   │   - save_daily_metrics(), load_historical_metrics()
│   ├── interventions.py        # Intervention storage (~150 lines)
│   │   - save_intervention_raw(), load_interventions()
│   │   - get_today_interventions(), load_historical_interventions()
│   └── conversations.py        # Chat history (~100 lines)
│       - save_conversation_message(), load_conversation_history()
│       - prune_conversation_history()
│
├── telegram/
│   ├── __init__.py
│   └── bot.py                  # Telegram integration (~150 lines)
│       - send_telegram() with chunking
│       - download_telegram_photo()
│
└── prompts/                    # Already extracted
    ├── morning_brief.md
    └── chat.md
```

**Migration approach (incremental):**
1. Create new module files with functions copied from modal_agent.py
2. Update imports in modal_agent.py to use new modules
3. Test each module independently
4. Delete duplicated code from modal_agent.py
5. Run full test suite after each module extraction

**Key considerations:**
- Modal app and volume must remain in modal_agent.py (decorators)
- volume.reload() and volume.commit() calls stay in entry point functions
- Import anthropic inside functions to avoid Modal image issues
- Maintain backward compatibility with existing tests

---

### 4. Extract System Prompts to Files

Prompts embedded as 200+ line strings in code.

**Move to:**
```
prompts/
├── morning_brief.md
└── chat.md
```

---

### 5. Documentation Mismatch

- Code comment says baselines are "60 days BEFORE raw window" but implementation uses rolling 60-day including recent data
- CLAUDE.md mentions Haiku but all calls use Opus (correct, but docs should match)

**Fix:** Update comments and CLAUDE.md to match actual behavior.

---

### 6. No Structured Logging

Print statements only. Hard to debug in Modal logs.

**Fix:** Add Python logging with structured output.

---

### 7. Photo Analysis Assumes JPEG

Hardcoded `media_type: "image/jpeg"` for all photos.

**Fix:** Detect actual image type from Telegram file info.

---

## Priority Order

1. **Remove evening brief** - Quick cleanup, ~200 lines
2. **Add retry logic** - Resilience improvement
3. **Fix documentation** - Accuracy
4. **Extract prompts to files** - Better prompt management
5. **Add structured logging** - Better debugging
6. **Split into modules** - Long-term maintainability
7. **Fix photo MIME type** - Edge case handling

---

## Files to Modify

- `/Users/babakd/oura_agent/modal_agent.py` - Main changes
- `/Users/babakd/oura_agent/CLAUDE.md` - Documentation fixes

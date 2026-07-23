# Oura Agent Product Plan — July 23, 2026

## Executive summary

The product has found a real daily job:

> Tell me what changed in my Oura data without making me open Oura.

It has not yet delivered the larger promise:

> Understand my life, help me make a better decision, and become more useful from the outcome.

The next version should therefore be **reader first, coach second, and
experimentation optional**. It should preserve the passive daily value that
already works, replace the long rigid report with one decision-first card, add
an explicit and editable personal model, and close the loop between a
recommendation, the user's response, and the eventual outcome.

The core product loop should be:

```text
observe → prioritize → recommend → capture response → observe outcome
        → update the personal model
```

This is primarily a product and data-architecture change, not a prompt-writing
or model-capability problem.

## Evidence from actual use

The production history reviewed on July 22, 2026 showed:

- 441 days of stored metrics.
- 193 brief files.
- Intervention logging on only 21 days, with the last intervention on
  February 9.
- 68 user chat messages, of which 64 occurred in January.
- The five most recent briefs were 500–683 words.
- Three of those five briefs exceeded Telegram's 4,096-character message
  limit.
- The recent briefs repeatedly prescribed the same bedtime correction and
  requested another log entry despite the user consistently declining through
  behavior.

This is strong retention for the passive reader and very weak retention for
the active coaching, chat, and intervention features.

The current implementation also has a technically healthy base. The full local
test suite passed with 190 tests on July 22. The gap is that most tests validate
plumbing, storage, and prompt formatting rather than longitudinal intelligence,
trust, usefulness, or behavior change.

## Product diagnosis

### 1. No closed learning loop

Recommendations are emitted as prose and then discarded. The product does not
store:

- what it recommended;
- why it recommended it;
- the expected outcome and review horizon;
- whether the recommendation was relevant;
- whether the user accepted or followed it;
- whether it was useful;
- what subjective and biometric outcomes followed.

Six months of use therefore cannot make tomorrow's recommendation materially
better than January's.

### 2. Biometric personalization is not life personalization

Comparing a value with a 60-day mean is useful, but it does not tell the system
what is appropriate for the user's actual day.

The product needs to understand:

- current goals and their priority;
- normal and target sleep schedules;
- work and calendar demands;
- planned training;
- preferred activities and available equipment;
- injuries, limitations, and medical boundaries;
- travel and temporary life context;
- recurring routines that should not require daily logging;
- advice the user accepts, ignores, or dislikes;
- desired tone, length, and notification behavior.

Chat currently receives only a short recent transcript. The in-progress profile
work is a useful start, but it is thin, is supplied only to the morning brief,
and is not an editable learning system shared by all product surfaces.

### 3. Report length is being mistaken for intelligence

The current brief often provides thoughtful analysis, but it is too long,
repetitive, and certain. Recent examples included:

- repeated conclusions presented as fresh findings;
- causal claims based on observational biometric changes;
- prescriptive workouts without knowing the user's plan or constraints;
- an illness/prodrome narrative based on small temperature changes;
- absolute language about one behavior being the only thing that mattered;
- a malformed numerical sequence in the generated output.

An ordinary day must be allowed to produce:

> Nothing unusual. Follow your normal plan.

The content planner should decide whether there is a meaningful insight before
the renderer decides how to format it.

### 4. Intervention tracking cannot deliver credible learning

The current record stores little more than time and free text. It lacks a
canonical intervention, category, dose, unit, duration, recurrence, adherence,
confidence, correction history, and links to an active hypothesis.

The current correlation method uses substring matching and a one-day-lag
exposed-versus-all-other-days comparison. It does not adequately account for
concurrent interventions, incomplete logging, travel, illness, dosage,
training, weekday, trend, or selection bias.

Routine behaviors should be configured once. The user should log only
deviations or respond to one-tap adherence prompts during a deliberate
experiment.

### 5. Telegram is the wrong primary interface

Telegram is valuable for:

- passive notifications;
- one-tap feedback;
- quick context capture;
- simple questions;
- deep links into a richer view.

It is poor for:

- long analytical reports;
- charts and comparisons;
- inspecting and correcting memory;
- editing intervention history;
- following experiment progress;
- navigating recommendation history;
- privacy and schedule settings.

The primary reader should become a small responsive web app or Telegram Mini
App.

### 6. Delivery is schedule-driven rather than readiness-driven

The current fixed UTC cron drifts by an hour across daylight-saving changes and
can run before Oura sleep data is fresh.

Delivery should be triggered by a fresh-data event when possible, bounded by the
user's preferred wake and quiet-hour window. When data is stale, the product
should show the last sync time and offer a clear retry action rather than
guessing whether the ring was removed.

## Product principles

1. **Reader first.** Preserve the daily glance that already creates value.
2. **Earn the right to coach.** Do not prescribe until the necessary personal
   and situational context is available.
3. **One important thing.** Rank candidate insights by goal relevance,
   actionability, confidence, severity, and novelty.
4. **No action is a valid result.** Do not manufacture advice on normal days.
5. **Ask only high-information questions.** Ask for context only when an answer
   could change the recommendation.
6. **Make memory visible and editable.** The user must be able to inspect,
   correct, expire, and delete remembered information.
7. **Separate observation, association, hypothesis, and causation.**
8. **Show data freshness and uncertainty.**
9. **Learn from rejection.** Repeatedly ignored advice should become less
   likely, not louder.
10. **Use slow metrics slowly.** Resilience, cardiovascular fitness, and
    similar measures belong in weekly or monthly views, not daily noise.

## Target experience

### Today

The default daily card should fit comfortably in one Telegram message and
contain:

- one-sentence state;
- the most important change;
- one optional decision;
- the two strongest supporting signals;
- confidence and freshness;
- progressive-disclosure actions.

Example:

> **Normal capacity, one caveat**
>
> You moved bedtime 2h30 earlier. Sleep and readiness stayed strong; HRV fell
> but remains within your normal range and improved overnight.
>
> **Today:** follow your planned day. Choose moderate training only if energy
> and soreness agree. Repeat the earlier bedtime once before drawing a
> conclusion.
>
> `Accurate` · `Too much` · `Show trend` · `Plan today`

### Trends

Build an interactive reader with:

- 7-, 28-, 90-, and 365-day views;
- overlays for workouts, travel, illness, alcohol, supplements, and user notes;
- missing-data and freshness indicators;
- comparisons against robust personal ranges;
- annotations for meaningful change points;
- "ask about this period";
- export and deletion controls.

### Personal model

Create explicit, user-controlled layers:

1. **Profile:** goals, schedule, preferences, training, equipment, constraints.
2. **Recent state:** travel, illness, deadline, training block, temporary
   schedule.
3. **Preference memory:** length, tone, suppressed topics, rejected advice.
4. **Recommendation ledger:** recommendations, responses, adherence, outcomes.
5. **Hypothesis ledger:** possible relationships, evidence, sample size,
   confidence, and last update.
6. **Conversation summary:** durable facts and unresolved threads, not a raw
   transcript dump.

Every memory should record its source, confidence, last update, and optional
expiry.

### Experiments

Support one active experiment at a time:

- agree on one goal and one hypothesis;
- define the exposure, outcome, expected lag, and duration;
- capture dose, timing, and adherence structurally;
- identify obvious confounders;
- prompt only for missing information;
- require a minimum number of comparable observations;
- report effect size, uncertainty, and data sufficiency;
- say "associated with" unless the design supports stronger language;
- end with a concrete keep, stop, extend, or redesign decision.

## Intelligence architecture

### Deterministic insight packet

Before calling the language model, compute a stable daily packet containing:

- chronological 7-, 14-, and 28-day trends;
- robust baselines and per-metric sample counts;
- personal percentiles and direction-aware status;
- anomaly and change-point strength;
- sleep debt and bedtime/wake regularity;
- recent training load and recovery context;
- missing-data and sync-quality flags;
- novel observations versus recent briefs;
- current personal context;
- active hypotheses;
- prior recommendations and the user's response.

The language model should select and explain from this packet. It should not be
responsible for deciding whether basic statistical analysis happens at all.

### Recommendation and feedback ledger

Store each recommendation as structured data:

```json
{
  "action": "Repeat bedtime before 00:30",
  "domain": "sleep_timing",
  "reason": ["bedtime phase advance", "strong sleep duration"],
  "confidence": "medium",
  "expected_outcome": "improved regularity without HRV deterioration",
  "review_after": "2 nights",
  "status": "accepted",
  "adherence": null,
  "useful": null,
  "outcome": null
}
```

Telegram actions should include `Relevant`, `Not for me`, `Doing it`,
`Skipped`, and `Useful`. The next brief must consult this ledger.

### Safety and calibration

- Do not infer illness or advise isolation from a minor isolated temperature
  change.
- Do not recommend supplements or dosing without explicit safety context and
  appropriate boundaries.
- Do not prescribe a workout without knowing planned activity, injuries, and
  subjective readiness.
- Validate every numerical statement against the deterministic packet.
- Validate output length and Telegram markup before delivery.
- Provide a clear distinction between wellness guidance and medical advice.

## Model strategy

### Decision

If Anthropic remains the model provider, use **Claude Fable 5** with API model
ID:

```text
claude-fable-5
```

Anthropic describes Fable 5 as its most capable generally available model for
demanding reasoning and long-horizon agentic work:

- https://platform.claude.com/docs/en/about-claude/models/introducing-claude-fable-5-and-claude-mythos-5
- https://www.anthropic.com/claude/fable

The model upgrade is not a substitute for memory, feedback, deterministic
analysis, or better UX. The migration can start immediately alongside the
trust work below and should be validated with the same product evaluations.

### Required Fable migration work

1. Set the primary model to `claude-fable-5`.
2. Use `claude-opus-4-8` as the explicit fallback where appropriate.
3. Handle `stop_reason: "refusal"` as a successful HTTP response with a
   product-safe retry or explanation, rather than treating it as empty output.
4. Account for always-on adaptive thinking and use effort controls.
5. Verify code execution, tool loops, prompt caching, and streaming behavior
   against the current SDK.
6. Test multi-turn preservation of thinking blocks.
7. Budget for Fable's higher token cost and long-context behavior.
8. Disclose and review Fable's mandatory 30-day provider retention. Fable is
   not available under zero-data-retention terms.
9. Continue using the direct Oura API under the documented private,
   single-user scope. Re-review the architecture before any distribution,
   commercialization, or processing of another person's data.
10. Add evaluation cases for refusals, fallbacks, empty output, latency, and
    cost.

## P0 decisions: scope, trust, platform, and security

These establish the operating boundary for further development.

### Scope and Oura architecture

Owner decision on July 23, 2026:

- this is a private, single-user personal tool;
- continue using the direct Oura API;
- do not add an Oura MCP migration to the current roadmap;
- do not distribute, commercialize, or process another person's Oura data
  without first revisiting the architecture and applicable terms.

For reference if that scope ever changes:

- https://cloud.ouraring.com/legal/api-agreement

### Authentication and sync

Current Oura documentation says personal access tokens were deprecated in
December 2025 and recommends OAuth2 plus webhooks:

- https://cloud.ouraring.com/v2/docs

Required work:

- migrate setup to the supported OAuth flow;
- refresh and revoke tokens correctly;
- use webhooks or another explicitly supported fresh-data mechanism;
- show sync status and last successful fetch;
- generate only after required data is complete or a retry window expires.

### Privacy

The current README says data never leaves Modal except for Telegram, while the
generation path sends health data to Anthropic. Replace that statement with a
truthful data-flow and consent explanation covering:

- Oura;
- Modal storage;
- Anthropic processing and retention;
- Telegram delivery;
- local operational logs;
- export, deletion, and revocation.

### Repository security

- Ensure `.env_backup` and similar credential backups can never be committed.
- Ignore coverage and local runtime artifacts.
- Rotate any secret that was ever staged, pushed, or otherwise exposed.
- Add automated secret scanning before commit and in CI.

## Prioritized roadmap

### Phase 0 — Trust and reliability

- Document the private, single-user scope and direct Oura API decision.
- Migrate the primary model to Claude Fable 5 with Opus 4.8 fallback, refusal
  handling, retention disclosure, and regression evaluations.
- Correct privacy language and health-safety boundaries.
- Secure local credential artifacts.
- Migrate authentication and data freshness handling.
- Fix timezone and daylight-saving behavior.
- Add exact command parsing, confirmation, soft deletion, and undo.
- Guarantee that chat errors replace the `Thinking...` placeholder.
- Validate and safely split long Telegram output.
- Build factuality, date, numerical, medical-calibration, and longitudinal
  evaluation cases.

### Phase 1 — Decision-first daily product

- Replace the report template with a ranked insight selector.
- Cap the normal daily card at approximately 600–900 characters.
- Allow a no-action day.
- Add progressive-disclosure buttons.
- Add a small targeted energy, soreness, stress, and planned-activity check-in.
- Store recommendation acceptance, adherence, and usefulness.
- Produce a weekly review of what changed, what was useful, and what remains
  unknown.

### Phase 2 — Explicit personalization

- Build the shared personal model and memory editor.
- Pass the same profile and relevant memory to every product surface.
- Support conversational updates such as:
  - "Never recommend morning training."
  - "I have a knee injury until September."
  - "Keep briefs under 100 words."
  - "This week I am traveling."
- Add confidence, source, expiry, edit, and delete controls.
- Use the recommendation ledger to stop repeating rejected advice.

### Phase 3 — Rich reader

- Build `Today`, `Trends`, `Experiments`, and `Personal Model` views.
- Add annotated visualizations and period comparisons.
- Keep Telegram as the notification and quick-action surface.
- Add slower health measures to weekly and monthly reviews only.
- Add selective calendar, training, travel, weather, or subjective context only
  when an active goal benefits.

### Phase 4 — Closed-loop experiments

- Replace free-text-only intervention storage with structured event records.
- Support correction, deletion, recurrence, and adherence.
- Run one deliberate experiment at a time.
- Use matched or otherwise comparable observations.
- Report sample size, effect size, uncertainty, missingness, and confounders.
- End each experiment with a user decision and update the hypothesis ledger.

### Phase 5 — Model evaluation and cost hardening

After the personal learning loop is operating:

- replay the longitudinal evaluation suite against Fable updates and future
  frontier models;
- compare candidates against the deployed Fable baseline on factuality, usefulness,
  repetition, calibration, latency, and cost;
- deploy only if it improves the product metrics below.

## Success measures

### North star

**Useful decisions per week:** recommendations or insights the user marks as
having changed or confirmed a worthwhile action.

### Supporting metrics

- percentage of daily cards rated accurate or useful;
- recommendation relevance and acceptance;
- adherence follow-up completion;
- repeated-advice rate;
- correction rate for facts and logs;
- stale-data brief rate;
- time to first useful insight;
- weekly review usefulness;
- experiment completion;
- percentage of insights that add value beyond restating an Oura score;
- median daily-card length and detail-expansion rate;
- AI cost and latency per useful decision.

## Non-goals

Do not prioritize:

- switching models without fixing the product loop;
- adding more sections or metrics to the daily brief;
- a universal food or supplement logger;
- generic medical or supplement recommendations;
- another unstructured chatbot surface;
- hidden memory the user cannot inspect;
- correlations presented as causation;
- collecting context without a clear decision it improves.

## Immediate implementation order

1. Document the personal-use boundary, data flow, and repository security.
2. Upgrade to Claude Fable 5 with retention consent, refusal handling, an
   Opus 4.8 fallback, and regression evaluations.
3. Fix authentication, data freshness, dates, timezone, and delivery
   reliability.
4. Ship the concise decision-first Telegram card with feedback actions.
5. Add the recommendation ledger and shared personal model.
6. Build the reader UI and weekly review.
7. Add structured one-at-a-time experiments.

---
name: th-trader-timemachine
description: >-
  Thai Time Machine for SET and mai stocks. Replays th-trader/plan.json 10,000
  times before you risk a baht, net of fees. Intraday plans replay the rest of
  the session from the stock's own 5-minute bars of the last 60 days (60 slots
  a day, lunch break included, no-trade slots flat, clamped to the day's floor
  and ceiling). Swing plans replay the next N trading days from 5 years of
  daily bars. It also re-runs the same rule on every past session or setup
  day, prints the odds of target first, stop first or neither, the spread of
  end prices, average net R and one verdict line, and draws an ASCII fan
  chart. Use when the user says "run the time machine", "replay this trade",
  "what are the odds", "backtest this setup", "is this a coin flip",
  "จำลองไม้นี้", "โอกาสถึงเป้า", "ย้อนดูไม้นี้", "ไม้นี้คุ้มไหมในเชิงสถิติ", or after
  th-trader-exit writes a plan.
argument-hint: "[run | chart] [--seed N] [--until HH:MM]"
---

# th-trader-timemachine

The Time Machine replays a planned trade before the user risks money. It is a
port of `trader-timemachine` (day-trader-skill, Jake Schincariol, MIT) with SET
rules from `th_market.py`.

`${CLAUDE_SKILL_DIR}` is this skill's folder. Run with `python`, not `python3`.

```bash
python "${CLAUDE_SKILL_DIR}/timemachine.py" run --write        # th-trader/plan.json -> th-trader/timemachine.json
python "${CLAUDE_SKILL_DIR}/timemachine.py" run --seed 7 --replays 20000
python "${CLAUDE_SKILL_DIR}/timemachine.py" run --until 15:00  # intraday: stop each replay at 15:00
python "${CLAUDE_SKILL_DIR}/timemachine.py" chart              # fan chart of the last saved run
```

## How it works

**Intraday plans:**
1. **Data.** 60 days of 5-minute bars from Yahoo.
2. **Usable days.** A day counts when at least 45 of its 60 slots traded and its 10:00 bar is real. A slot with no trade is a flat bar at the previous close. With fewer than 20 usable days it refuses and suggests `--mode swing`.
3. **The pool.** For each slot, every past day's bar is kept as ratios to the bar before. The 14:00 slot is measured against 12:25, so the lunch-break move is in the pool.
4. **One replay.** It walks from the bar after the entry to `flat_by`, drawing each bar from the same time of day (±2 slots, inside the same session). Every price is clamped to the day's floor and ceiling.

**Swing plans:**
- The replay walks `days` trading days. Each day is a random past daily bar, clamped to ±30% of the day before.

**Exits (both modes):**
- A bar that **opens** through the stop exits at that open. That can be worse than −1R, and it matters on SET's afternoon and next-day opens.
- Otherwise the stop is checked before the target, so a bar that touches both counts as the stop.

**R:** R is net of fees: (exit − entry − round-trip fee) ÷ risk a share with fees.

**History:**
- Intraday: the plan's opening-range rule is re-run on every usable past session.
- Swing: every past day on which the stock matched the plan's setup tag is re-run.
- Hand-entered swing plans have no history.

**Verdict:** among the replays that hit a side, the target's share is compared with the break-even 1 ÷ (1 + net R):
- under 5 points apart: "Close to break-even" (or "coin flip" at 1R)
- 5 to 15 points: "Leans toward the target" or "Leans toward the stop"
- 15 or more: "Strongly toward" one side

**Seed:** 19 by default. Same plan, same data and same seed give the same numbers.

## Steps

1. `timemachine.py run --write`. Report in Thai:
   - the odds
   - the price spread at the end
   - average net R
   - the history line
   - the verdict line and any line under it, exactly as printed
   - the seed and the fees line
2. If the verdict is close to break-even or leans toward the stop, say so plainly.
3. Hand on: th-trader-budget sizes the plan.

## What it never does

It never places or sizes an order and never says whether to take the trade. A
replay is statistics from past bars, not a prediction; a real fill can be
worse. Education and tooling, not investment advice.

---
name: th-trader-budget
description: >-
  Thai Budget Manager for SET and mai stocks. Sizes the plan in
  th-trader/plan.json to the budget and the max loss per trade in
  th-trader/config.json, fees counted, in whole board lots (100, or 50): the
  largest size that fits max loss, the position cap and cash after the
  buffer, and says which limit decided it. A stop too far for the max loss is
  a plain refusal, not a smaller order; so is net R under min_reward_risk.
  Writes one broker-neutral order ticket to th-trader/orders.json for
  th-trader-risk and prints it for the user to type into their broker's app.
  Nothing is placed. Use when the user says "how many shares", "size this
  trade", "I have 100,000 baht", "I only want to risk 500 a trade", "ซื้อกี่หุ้น",
  "คำนวณขนาดไม้", "มีเงินเท่านี้ซื้อได้เท่าไหร่", "ตั้งกฎเงิน", or after th-trader-exit.
argument-hint: "[--write] [--price X] [--lot 50]"
---

# th-trader-budget

The Budget Manager turns a plan into a number of shares. It is a port of
`trader-budget` (day-trader-skill, Jake Schincariol, MIT) with SET board lots,
baht and fees from `th_market.py`. The original's Alpaca order shape and
weights mode are not ported.

`${CLAUDE_SKILL_DIR}` is this skill's folder. Run with `python`, not `python3`.

```bash
python "${CLAUDE_SKILL_DIR}/size.py"                # th-trader/plan.json + th-trader/config.json
python "${CLAUDE_SKILL_DIR}/size.py" --write        # also th-trader/orders.json
python "${CLAUDE_SKILL_DIR}/size.py" --price 42.50  # the price ran above the plan's entry
python "${CLAUDE_SKILL_DIR}/size.py" --lot 50       # a stock at or above THB 500 for 6 months
```

## Set the rules once

The rules live in `th-trader/config.json`. Every key is optional; the defaults
are below. Help the user write it with their own numbers.

```json
{"budget_thb": 100000, "max_loss_per_trade_thb": 500, "max_position_pct": 50,
 "cash_buffer_pct": 10, "min_reward_risk": 1.5, "commission_pct": 0.15}
```

The fee defaults were checked on 2026-11-02:
- market fees 0.007% (trading 0.005 + clearing 0.001 + SEC 0.001)
- VAT 7%, extended to 2027-09-30
- commission 0.15%, a typical online cash-balance rate. Ask the user for their broker's rate.

## How many shares

The size is the largest multiple of the lot that fits all three limits:

| Limit | Shares |
|---|---|
| max loss | max_loss_per_trade_thb ÷ (stop distance + round-trip fee a share) |
| position cap | budget_thb × max_position_pct ÷ entry |
| cash | budget_thb × (1 − cash_buffer_pct) ÷ (entry + buy fee a share) |

- Zero lots is a refusal that names the loss of one lot in baht.
- Net R under `min_reward_risk` is refused. The default exit target of 2R gross is often under 1.5 net once fees are counted; the message says so. The user can re-plan with a farther target or set their own `min_reward_risk`.

## Steps

1. `size.py --write`. Report in Thai:
   - the three limits and which one decided the size
   - the shares and their cost
   - the max loss with fees
   - the ticket, exactly as printed
   - the fees line
2. Hand on: th-trader-risk checks the ticket.

## What it never does

It never places, approves or schedules an order, and never raises a limit on its
own. The user types the ticket into their broker's app. Education and tooling,
not investment advice.

---
name: th-trader-alerts
description: >-
  Thai price and clock alerts for SET and mai stocks, in Claude and on
  Telegram. Says whether SET is open, in the lunch break or closed (with the
  holiday name); quotes the last price and previous close from Yahoo's delayed
  .BK data and writes th-trader/prices.json for th-trader-budget and
  th-trader-risk; and rings once when a stop, target, price level, % move,
  time (flat by 16:20) or the opening bell hits, with how far behind the data
  is. Rules come from th-trader/plan.json or the user. Never places an order.
  Use when the user says "แจ้งเตือนเมื่อ", "เตือนถ้าชน stop", "ตลาดเปิดไหม",
  "ราคาตอนนี้", "ตั้งเตือนราคา", "alert me when", "tell me if it hits my
  stop", "is the market open", "price now", or while a Thai trade is open.
argument-hint: "[clock | quote SYMBOL... | add ... | check | list | reset ID]"
---

# th-trader-alerts

Price and clock alerts for a Thai trade. It watches what the user already
decided (a stop, a target, a time to be flat) and says when it hits, here and
on Telegram. It is a port of `trader-alerts` (day-trader-skill, Jake
Schincariol, MIT) with the SET clock from `th_market.py`.

`${CLAUDE_SKILL_DIR}` is this skill's folder. Run with `python`, not `python3`.

```bash
python "${CLAUDE_SKILL_DIR}/alerts.py" clock                        # open / lunch / closed, next open or close
python "${CLAUDE_SKILL_DIR}/alerts.py" quote PTT CPALL --write      # -> th-trader/prices.json
python "${CLAUDE_SKILL_DIR}/alerts.py" add --from-plan              # stop, target, flat-by from th-trader/plan.json
python "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol PTT --below 41.75 [--rearm]
python "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol PTT --above 43.50
python "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol PTT --pct 3 --direction down
python "${CLAUDE_SKILL_DIR}/alerts.py" add --at 16:20 --label "flat by"
python "${CLAUDE_SKILL_DIR}/alerts.py" add --opening-bell
python "${CLAUDE_SKILL_DIR}/alerts.py" check                        # ring what hit; exit 0 rang, 1 nothing
python "${CLAUDE_SKILL_DIR}/alerts.py" list
python "${CLAUDE_SKILL_DIR}/alerts.py" reset PTT-below-1
```

Other flags: `--date YYYY-MM-DD` (a rule for that date only), `--no-telegram`,
`--now "2026-11-02 11:05"` (Bangkok), `--offline DIR` (saved Yahoo charts,
`SYMBOL.json`).

## How rules ring

Rules live in `th-trader/alerts.json` with their ring state.

| kind | rings |
| --- | --- |
| `above` / `below` | once, then quiet until `reset`; with `--rearm`, again after the price crosses back |
| `pct_move` | at most once a day, vs the previous close |
| `time` | once per trading day at or after HH:MM (never on a weekend or holiday) |
| `opening_bell` | once per trading day while SET is open (10:00) |

`add --from-plan` makes three rules for the plan's date: `below` = stop,
`above` = target, and for an intraday plan `time` = flat_by. Adding again
replaces them.

A ring reads like "PTT 41.70 hit the stop 41.75 at 11:05 (data ~15 min
behind)". Yahoo's .BK quotes run 15 to 30 minutes behind, so a ring says the
stop was crossed in delayed data: the stop order in the broker's app is what
actually gets the user out. In the lunch break and after the close the note
reads "last price before the lunch break" (or "before the close"), or "data ~N
min behind at the lunch break" when Yahoo stopped short of it; a quote from an
earlier day reads "last price from YYYY-MM-DD HH:MM".

## Steps

1. When a trade is open, run `alerts.py add --from-plan` once.
2. Run `alerts.py check` every few minutes with `/loop 5m alerts.py check`
   (the full path as above) until the user is flat. Tell the user each ALERT
   line, plainly.
3. Before sizing with th-trader-budget, `alerts.py quote SYMBOL --write` gives
   it a fresh price; th-trader-risk takes the previous close from the same file.
4. `alerts.py clock` answers "is the market open".

## Telegram

The alerts also go to the user's own Telegram bot. The user sets two
environment variables **in their own terminal**, never in the chat:

```powershell
setx TH_TRADER_TELEGRAM_TOKEN "123456:ABC..."     # from @BotFather
setx TH_TRADER_TELEGRAM_CHAT_ID "987654321"       # their chat id with the bot
```

`setx` applies to terminals opened afterwards: restart Claude Code after
setting them. Without them each ring says "Telegram not set up" and only
prints. A failed send is one line with the HTTP status; the rule still counts
as rung. The token never appears in output or files. Never ask the user to
paste the token into the chat, and never echo the variable.

## Rules

- An alert is information, not an order. It never places, changes or cancels
  anything.
- Quotes are free and delayed. Say so when a minute matters.
- Data and statistics, not a recommendation to buy or sell.

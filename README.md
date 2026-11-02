# ThaiTraderSkills

Claude Code skills for day trading Thai stocks (SET and mai), ported from the
US `trader-*` skills of day-trader-skill (Jake Schincariol, MIT). The original
skills stay installed and untouched; these install as `th-trader-*`.

| Skill | What it does |
|---|---|
| `th-trader-scanner` | Daily setups after the close and opening-range breakouts during the morning session, from Yahoo `.BK` data |
| `th-trader-exit` | The stop and the target before the entry, on SET's tick grid, fees counted (intraday or swing) |
| `th-trader-timemachine` | 10,000 replays of the plan from the stock's own 5-minute or daily bars, plus the same rule on past days |
| `th-trader-budget` | How many shares, in board lots, fit the budget and the max loss; an order ticket to type into the broker app |
| `th-trader-risk` | PASS or BLOCK against your rules, and a log of the trades you made |

The usual chain: `scan.py orb` → `exit.py plan --symbol X --write` → `timemachine.py run` →
`size.py --write` → `check.py` → you place the order yourself → `check.py log buy ...`.

Every Thai market rule (Bangkok time, SET holidays and sessions, tick sizes,
price limits, board lot, fees) lives in `shared/th_market.py` and `shared/data/`.

## Your rules: `th-trader/config.json`

One file in the folder you work in, shared by every skill. Every key is optional:

```json
{"commission_pct": 0.15, "market_fees_pct": 0.007, "vat_pct": 7,
 "budget_thb": 100000, "max_loss_per_trade_thb": 500, "max_position_pct": 50,
 "cash_buffer_pct": 10, "max_daily_loss_thb": 1500, "max_trades_per_day": 3,
 "min_reward_risk": 1.5, "flat_by": "16:20", "no_entries_after": "15:30", "blocked": []}
```

- **Fees (checked 2026-11-02).** The market fees are 0.007% per side: SET trading 0.005%, TCH clearing 0.001% and SEC 0.001%, plus VAT 7% (extended to 2027-09-30). Commission is negotiated per broker; 0.15% is a common online cash-balance rate. **Set `commission_pct` to your broker's rate.**
- **`min_reward_risk` is net of fees.** A 2R target (before fees) on a tight stop is often below 1.5 net, so the budget and risk skills refuse it.

Output is data and statistics, not advice to buy or sell. Nothing here places
an order.

## Install

```powershell
powershell -ExecutionPolicy Bypass -File scripts\Install-Skills.ps1
```

It copies each skill, plus `shared/*.py` and `shared/data/`, into
`~/.claude/skills/<name>/`. It replaces only folders it installed itself.

## Develop

```powershell
python -m pip install --user pytest
python -m pytest tests
```

Specs and plans are in `docs/superpowers/`.

## Data to keep up to date

- `shared/data/set-holidays.json`: add next year's SET holidays before the year starts.
- `shared/data/tick-tables.json`: the tick table from 2026-11-16 has no values
  yet. Enter its bands from SET's official notice and set its status to
  `verified`. Until then prices are rounded with the 2009 table, with a warning.

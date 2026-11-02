# ThaiTraderSkills

Claude Code skills for day trading Thai stocks (SET and mai), ported from the
US `trader-*` skills of day-trader-skill (Jake Schincariol, MIT). The original
skills stay installed and untouched; these install as `th-trader-*`.

| Skill | What it does |
|---|---|
| `th-trader-scanner` | Daily setups after the close and opening-range breakouts during the morning session, from Yahoo `.BK` data |

Every Thai market rule (Bangkok time, SET holidays and sessions, tick sizes,
price limits, board lot) lives in `shared/th_market.py` and `shared/data/`.

Output is data and statistics, not advice to buy or sell. Nothing here places
an order.

## Install

```powershell
powershell -ExecutionPolicy Bypass -File scripts\Install-Skills.ps1
```

It copies each skill, plus `th_market.py` and `data/`, into
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

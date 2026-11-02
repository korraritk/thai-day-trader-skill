"""Time Machine for th-trader-timemachine. Standard library only.

Replays a planned SET/mai trade before you risk a baht: 10,000 simulated runs of the rest of the
session (intraday, 5-minute bars) or of the next N trading days (swing, daily bars), plus the same
rule on every past session or setup day in the data. Fees are counted: R is net of the round trip.

Ported from day-trader-skill's trader-timemachine (MIT, Jake Schincariol): the pool of bar ratios,
the replay loop, the percentile bands, the verdict. Changed for SET: 60 five-minute slots a day
(two sessions, the lunch break inside the 12:25 -> 14:00 step), a slot with no trade is a flat bar,
prices are clamped to the day's floor and ceiling, a bar that opens through the stop exits at its
open (worse than -1R), and R is net of fees.

    python timemachine.py run                      # th-trader/plan.json, 10,000 replays, seed 19
    python timemachine.py run --write              # also save th-trader/timemachine.json
    python timemachine.py run --seed 7 --replays 20000 --json
    python timemachine.py chart                    # ASCII fan chart of the last saved run
"""
import argparse
import datetime
import json
import os
import random
import statistics
import sys
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    import th_market as tm
    import setups
except ImportError:
    sys.path.insert(0, os.path.join(HERE, "..", "..", "shared"))
    import th_market as tm
    import setups

SLOTS = 60  # 5-minute bars a full SET day: 30 morning + 30 afternoon
MORNING = range(0, 30)
AFTERNOON = range(30, 60)
MIN_SHARE = 0.75  # a day counts when this share of its slots has a real bar
MIN_SESSIONS = 20
SAMPLE_PATHS = 160
BAND_PATHS = 2000
BAND_Q = (0.05, 0.25, 0.5, 0.75, 0.95)
HIST_BINS = 24
COIN_FLIP = 0.05  # verdict thresholds, as in the original
STRONG = 0.15
MANY_NEITHER = 0.40
STEP = datetime.timedelta(minutes=5)


class TMError(Exception):
    pass


# ---------------------------------------------------------------- intraday sessions

def full_day(series, d, min_share=MIN_SHARE):
    """The day's 60 bars with no-trade slots filled flat at the previous close, or None when
    fewer than min_share of the slots traded or the 10:00 bar is missing."""
    real = {b["start"]: b for b in tm.intraday_bars(series, d, datetime.datetime.combine(d, datetime.time(16, 30)))}
    grid = tm.slots(d, 5)
    if len(grid) != SLOTS or grid[0] not in real or len(real) < min_share * SLOTS:
        return None
    out, prev = [], None
    for s in grid:
        b = real.get(s)
        if b is None or b["h"] is None or b["l"] is None:
            c = prev if b is None else b["c"]
            b = {"start": s, "o": c, "h": c, "l": c, "c": c, "v": 0}
        elif b["o"] is None:
            b = dict(b, o=prev if prev is not None else b["c"])
        out.append(b)
        prev = b["c"]
    return out


def usable_sessions(series, before, min_share=MIN_SHARE):
    """{date: 60 bars} for every trading day before `before` that passes full_day."""
    days = sorted({tm.ts_to_bkk(t).date() for t in series["t"]})
    out = {}
    for d in days:
        if d >= before or not tm.is_trading_day(d):
            continue
        bars = full_day(series, d, min_share)
        if bars:
            out[d] = bars
    return out


def require_sessions(sessions, symbol):
    if len(sessions) < MIN_SESSIONS:
        raise TMError("%s has %d usable 5-minute sessions in the last 60 days (needs %d): too thinly traded "
                      "for an intraday replay; try --mode swing." % (symbol, len(sessions), MIN_SESSIONS))


def build_pool(sessions):
    """pool[i] = every past session's bar i as (o, h, l, c) / bar i-1's close, in date order.
    pool[0] is empty: the day's first bar has no bar before it."""
    pool = [[] for _ in range(SLOTS)]
    for bars in sessions:
        for i in range(1, SLOTS):
            pc = bars[i - 1]["c"]
            b = bars[i]
            pool[i].append((b["o"] / pc, b["h"] / pc, b["l"] / pc, b["c"] / pc))
    return pool


def entry_slot(series, plan):
    """Index of the bar that closed at the plan's entry time on the plan's date."""
    day = datetime.date.fromisoformat(plan["date"])
    t = datetime.datetime.strptime(plan.get("entry_time") or "10:20", "%H:%M").time()
    end = datetime.datetime.combine(day, t)
    start = end - STEP
    grid = tm.slots(day, 5)
    if start not in grid:
        raise TMError("%s is not the end of a 5-minute bar in a SET session" % t.strftime("%H:%M"))
    have = {b["start"] for b in tm.intraday_bars(series, day, end)}
    if start not in have:
        raise TMError("The entry bar at %s on %s is not in Yahoo's data yet (it runs 15-30 minutes behind). "
                      "Run again in a few minutes." % (t.strftime("%H:%M"), day.isoformat()))
    return grid.index(start)


def end_slot(day, until):
    """The last slot whose bar ends at or before `until`."""
    grid = tm.slots(day, 5)
    ends = [i for i, s in enumerate(grid) if s + STEP <= datetime.datetime.combine(day, until)]
    if not ends:
        raise TMError("--until %s is before the first bar ends" % until.strftime("%H:%M"))
    return ends[-1]


# ---------------------------------------------------------------- the replay

def net_r(exit_price, entry, risk_per_share, fee_rate):
    return (exit_price - entry - (entry + exit_price) * fee_rate) / risk_per_share


def walk(bar, p, stop, target, floor, ceiling):
    """One simulated bar from price p -> (open, high, low, close, exit or None, outcome or None)."""
    o, h, l, c = (min(max(p * x, floor), ceiling) for x in bar)
    if o <= stop:
        return o, h, l, c, o, "stop"  # opened through the stop: out at the open
    if l <= stop:
        return o, h, l, c, stop, "stop"  # both in one bar counts as the stop (conservative)
    if o >= target or h >= target:
        return o, h, l, c, target, "target"
    return o, h, l, c, None, None


def _slot_picker(rng, window):
    def pick(i):
        rng_ = MORNING if i in MORNING else AFTERNOON
        lo, hi = max(rng_.start, 1), rng_.stop - 1
        return i if window == 0 else min(hi, max(lo, i + rng.randint(-window, window)))
    return pick


def summarize(outcomes, closes, Rs, entry, r_target, replays, seed, extra):
    cnt = {x: outcomes.count(x) for x in ("target", "stop", "close")}
    green = sum(1 for o, p in zip(outcomes, closes) if o == "target" or (o == "close" and p > entry))
    srt = sorted(closes)
    lo_, hi_ = pct(srt, 0.01), pct(srt, 0.99)
    w = (hi_ - lo_) / HIST_BINS
    bins = [0] * HIST_BINS
    for c in srt:
        b = int((c - lo_) / w) if w > 0 else 0
        if 0 <= b < HIST_BINS:
            bins[b] += 1
    out = {"replays": replays, "seed": seed,
           "p_target": round(cnt["target"] / replays, 4), "p_stop": round(cnt["stop"] / replays, 4),
           "p_neither": round(cnt["close"] / replays, 4), "p_green": round(green / replays, 4),
           "median_close": round(pct(srt, 0.5), 2), "p5_close": round(pct(srt, 0.05), 2),
           "p95_close": round(pct(srt, 0.95), 2), "avg_R": round(statistics.mean(Rs), 3),
           "r_at_target": round(r_target, 4), "hist": {"lo": round(lo_, 2), "hi": round(hi_, 2), "bins": bins}}
    out.update(extra)
    return out


def bands_of(paths):
    bands = []
    for j in range(len(paths[0])):
        col = sorted(pp[j] for pp in paths)
        bands.append([round(pct(col, q), 2) for q in BAND_Q])
    return bands


def simulate(pool, entry, stop, target, start_slot, end_slot, floor, ceiling, risk_per_share, fee_rate,
             replays=10000, seed=19, window=2):
    """Replays of slots start_slot+1 .. end_slot from the entry price."""
    r_target = net_r(target, entry, risk_per_share, fee_rate)
    rng = random.Random(seed)
    pick = _slot_picker(rng, window)
    choice = rng.choice
    outcomes, closes, Rs, paths = [], [], [], []
    for n in range(replays):
        p, outcome, exit_at, path = entry, None, None, [entry]
        for i in range(start_slot + 1, end_slot + 1):
            o, h, l, c, x, out = walk(choice(pool[pick(i)]), p, stop, target, floor, ceiling)
            if outcome is None and out:
                outcome, exit_at = out, x
            p = c
            if n < SAMPLE_PATHS:
                path.append(round(p, 2))
        outcomes.append(outcome or "close")
        closes.append(p)
        Rs.append(net_r(exit_at if outcome else p, entry, risk_per_share, fee_rate))
        if n < SAMPLE_PATHS:
            paths.append(path)
    rng = random.Random(seed)
    pick = _slot_picker(rng, window)
    allp = []
    for _ in range(BAND_PATHS):
        p, path = entry, [entry]
        for i in range(start_slot + 1, end_slot + 1):
            p = min(max(p * rng.choice(pool[pick(i)])[3], floor), ceiling)
            path.append(p)
        allp.append(path)
    return summarize(outcomes, closes, Rs, entry, r_target, replays, seed,
                     {"bars_left": end_slot - start_slot, "sample_paths": paths, "bands": bands_of(allp),
                      "band_paths": BAND_PATHS, "window": window})


# ---------------------------------------------------------------- history: the same rule on past sessions

def history_stop(entry, oh, ol, rule, plan_risk_pct, d):
    if rule == "or-mid":
        raw = (oh + ol) / 2
    elif rule == "or-low":
        raw = ol
    else:  # price:, pct:, atr: keep the plan's stop distance as a share of the entry
        raw = entry * (1 - plan_risk_pct)
    return float(tm.round_down_tick(Decimal(str(round(raw, 6))), d))


def history_intraday(sessions, or_minutes, stop_rule, plan_risk_pct, target_r, end_at, fee_rate):
    """The plan's opening-range breakout on every past session: entry = the first close above the
    range high; same stop rule, target R and exit rules (no clamp: these are real bars)."""
    n = or_minutes // 5
    rows = []
    for d, bars in sorted(sessions.items()):
        oh = max(b["h"] for b in bars[:n])
        ol = min(b["l"] for b in bars[:n])
        k = next((i for i in range(n, end_at + 1) if bars[i]["c"] > oh), None)
        if k is None:
            rows.append({"date": d.isoformat(), "outcome": "no breakout"})
            continue
        entry = bars[k]["c"]
        stop = history_stop(entry, oh, ol, stop_rule, plan_risk_pct, d)
        if stop >= entry:
            rows.append({"date": d.isoformat(), "outcome": "no valid stop"})
            continue
        target = float(tm.round_tick(Decimal(str(round(entry + target_r * (entry - stop), 6))), d))
        risk = (entry - stop) + (entry + stop) * fee_rate
        outcome, x, p = None, None, entry
        for b in bars[k + 1:end_at + 1]:
            p = b["c"]
            if b["o"] <= stop:
                outcome, x = "stop", b["o"]
            elif b["l"] <= stop:
                outcome, x = "stop", stop
            elif b["o"] >= target or b["h"] >= target:
                outcome, x = "target", target
            if outcome:
                break
        rows.append({"date": d.isoformat(), "entry": round(entry, 2), "stop": stop, "target": target,
                     "at": (bars[k]["start"] + STEP).strftime("%H:%M"), "outcome": outcome or "close",
                     "r": round(net_r(x if outcome else p, entry, risk, fee_rate), 3)})
    return rows


# ---------------------------------------------------------------- verdict (ported from trader-timemachine;
# r_at_target is net R here, so the break-even counts fees)

def _until(mc):
    return mc.get("until") or "16:20"


def pct(a, q):
    return a[min(len(a) - 1, int(q * len(a)))]


def lean(mc):
    """(gap, share, break_even): the target's share of the replays that hit one side,
    minus the share that breaks even at the target's R. None when nothing hit a side."""
    pt, ps = mc["p_target"], mc["p_stop"]
    if pt + ps <= 0:
        return None
    r = mc.get("r_at_target") or 1.0
    share, even = pt / (pt + ps), 1.0 / (1.0 + r)
    return share - even, share, even


def verdict(mc):
    """One line from the numbers, see the docstring for the thresholds."""
    pt, ps = mc["p_target"], mc["p_stop"]
    lv = lean(mc)
    if lv is None:
        return "No replay reached the target or the stop by %s: the price at %s decides every one." % (
            _until(mc), _until(mc))
    d, share, even = lv
    r = mc.get("r_at_target") or 1.0
    one_r = abs(r - 1.0) < 0.005
    if abs(d) < COIN_FLIP:
        head = "Close to a coin flip" if one_r else "Close to break-even"
    elif abs(d) < STRONG:
        head = "Leans toward the target" if d > 0 else "Leans toward the stop"
    else:
        head = "Strongly toward the target" if d > 0 else "Strongly toward the stop"
    odds = "target first %.1f %% vs stop first %.1f %%" % (100 * pt, 100 * ps)
    if one_r:
        return "%s: %s." % (head, odds)
    return ("%s: at %gR the target has to come first in %.0f %% of the replays that hit one side; "
            "it did in %.0f %% (%s)." % (head, round(r, 2), 100 * even, 100 * share, odds))


def verdict_notes(mc):
    """The lines printed right below the verdict, so nothing reads as one-sided."""
    notes = []
    pn = mc.get("p_neither", 0)
    if pn > MANY_NEITHER and lean(mc) is not None:
        notes.append("%.1f %% of the replays hit neither by %s; the price at %s decides those."
                     % (100 * pn, _until(mc), _until(mc)))
    lv = lean(mc)
    avg = mc.get("avg_R")
    if lv and abs(lv[0]) >= COIN_FLIP and avg is not None and avg != 0 and (avg > 0) != (lv[0] > 0):
        notes.append("Average R is %+.3f all the same: the replays that hit neither pull it the other way." % avg)
    return notes

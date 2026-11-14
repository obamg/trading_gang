# Does the Confluence Board pick direction over 4 hours? Pre-registration

Written 2026-11-14, before the signals were filtered by the board rule or their outcomes looked
at for this question. The result is appended at the bottom, once.

## Why this question

The Confluence Board (`oracle/board.py`) lists coins where at least three Oracle modules agree
on a direction, and since 2026-11-14 it records and follows up each new entry. That record has
one entry so far. Waiting for it would take months, but Oracle has stored about 515,000 signals
since 2026-09-24, each with its per-module breakdown and the price move 4 and 24 hours later.
The board rule can be applied to those signals directly.

Related, already judged: `2026-11-13-oracle-alerted-signals.md` (alerted signals, 1 hour) failed.
This uses the same stored signals with a different selection (module agreement, not the alert
gate) and a different horizon (4 hours, the one the board's page reports). It is not a re-run of
that test, but it is the sixth test in three days, and is read that way.

## Rules (the board's own, unchanged)

- Signals: `oracle_signals` with `timeframe = '5m'`, `signal_at` from 2026-09-24 to 2026-11-13
  inclusive, with `pnl_4h_pct` present.
- Agreement: a module agrees when its `direction` is bullish (or bearish) and its `intensity` is
  above 0.3. A signal qualifies when at least 3 modules agree on one direction and more modules
  agree on it than on the other (`MIN_MODULES = 3`, `INTENSITY_FLOOR = 0.3`).
- Universe: the board uses the top 50 USDT perps by Binance 24h volume, which was not stored. The
  stand-in, fixed in advance: on each UTC day, the 50 Bybit USDT perps with the highest turnover
  on the previous day (Bybit daily bars, already cached for the weekly study; no look-ahead).
- Entries: the first qualifying signal for a symbol, then nothing more for that symbol for
  120 minutes (the board's alert cooldown). A signal in the other direction inside the cooldown
  is also skipped, as the live board would do.
- Result per entry: `+pnl_4h_pct` for bullish, `-pnl_4h_pct` for bearish. Cost: 0.15% per entry.

## Unit and pass test (judged once)

Entries on the same day move together, so the unit is the UTC day: each day's entries are
averaged first. With m = mean of daily averages minus 0.15% and se = sd / sqrt(days):

- **Pass:** at least 30 days and 100 entries, m > 1.28 * se, and both halves (split by date)
  have m above 0.
- **Ruled out:** m + 1.28 * se < 0 (even the optimistic bound does not pay costs).
- **Inconclusive:** anything else.

## Smallest detectable edge (stated before looking)

About 50 days. Guessing a daily sd near 1.0% for 4-hour moves (the 1-hour test saw 0.50%), the
standard error is about 0.14%. A true edge of about +0.45% per entry before costs would pass
80% of the time; +0.30% would pass about a third of the time. So this test can find a strong
edge or rule out one that pays costs comfortably, and a thin edge will read as inconclusive.

Reported for context, unable to change the verdict: 24 hours; long and short entries
separately; entries with |score| of 40 or more; the same rule on every symbol, not only the
top 50; the result before costs.

## What each verdict would mean

Pass: the board earns a stricter forward test on its own recorded entries, rules written first.
Ruled out: the board stays a "where to look" screen and its page stops presenting entries as
calls. Inconclusive: the same, with the forward record left running. No re-run with other
thresholds, cooldowns, universes or horizons.

## Known weaknesses

- Stored signals fire on module events every few minutes; the live board re-scores the top 50
  every 3 minutes. A qualifying stored signal is a close stand-in for "the coin is on the board",
  not an exact copy of the board's entry times.
- Outcome prices come from cached 5-minute candles; when the exact candle is missing, the stored
  4-hour price may be from up to 4 hours later (`measure_outcomes` fallback).
- Bybit turnover stands in for Binance volume.

---

## Result

Run once on 2026-11-14 against production (read-only export). **Inconclusive by the test above.
Read with the detail below, there is no sign of an edge: the average is carried by a handful of
extreme entries, and the typical entry and the typical day both lose after costs.**

Signals 2026-09-24 to 2026-11-13 with a 4-hour outcome: 510,461. Board-rule entries in the
top-50 universe: 1,554 over 45 days (749 long, 805 short). Unit = UTC day.

| | Entries | Days | Per day, after 0.15% cost | 90% bounds | Days above 0 |
| --- | --- | --- | --- | --- | --- |
| Board rule, 4h (the hypothesis) | 1,554 | 45 | +0.102% | −0.300% to +0.505% | 18 of 45 |
| 24h (context) | 1,554 | 45 | +0.290% | −0.096% to +0.677% | 24 of 45 |
| Longs, 4h | 749 | 44 | +0.341% | −0.540% to +1.222% | 20 of 44 |
| Shorts, 4h | 805 | 44 | −0.187% | −0.530% to +0.156% | 22 of 44 |
| Every symbol, not only top 50, 4h | 6,836 | 45 | −0.106% | −0.188% to −0.024% | 15 of 45 |

Against the test: at least 30 days and 100 entries, yes; mean above the bar (+0.402% at sd
2.11%), no; both halves above 0, yes (+0.060%, +0.143%). Not ruled out either, because the
upper bound (+0.505%) still covers an edge that would pay costs.

Why "inconclusive" here means "nothing found":

- The daily sd was 2.11%, double the 1.0% assumed, so the test could only have detected an edge
  of about +0.82% per entry before costs. It had little power, as the note warned.
- The median day is −0.14% and the mean without its two best days is −0.24%. The median entry
  is −0.005% before costs and 49.5% of entries pointed the right way: a coin flip.
- The five best entries are +394%, +67%, +54%, +52% and +47% in four hours. The +394% long
  alone moves the 4-hour mean noticeably. Moves that size may be real pumps or bad outcome
  prices (the 4-hour price can come from a fallback candle); this was not checked, and either
  way they are not a repeatable edge.
- Every entry had |score| below 40 (median 21, maximum 37): with three modules agreeing the
  Oracle score is still weak, so the board is not a "strongest signals" list.
- Applied to every symbol, where there are more entries and less noise, the same rule loses
  after costs with an upper bound below zero.

What follows, by the terms above: the board stays a "where to look" screen, its page should stop
presenting entries as calls, and its forward record keeps running. No re-run with other
thresholds, cooldowns, universes or horizons.

Data export (read-only, production):

```sql
select s.symbol, extract(epoch from s.signal_at)::bigint ts, s.score,
  (select count(*) from jsonb_each(s.signals_breakdown) e
     where e.value->>'direction'='bullish' and (e.value->>'intensity')::numeric > 0.3) bull,
  (select count(*) from jsonb_each(s.signals_breakdown) e
     where e.value->>'direction'='bearish' and (e.value->>'intensity')::numeric > 0.3) bear,
  o.pnl_4h_pct, o.pnl_24h_pct
from oracle_signals s join oracle_outcomes o on o.signal_id = s.id
where s.timeframe = '5m' and s.signal_at >= '2026-09-24' and s.signal_at < '2026-11-14'
  and o.pnl_4h_pct is not null
order by s.signal_at;
```

Script: `docs/research/scripts/board_confluence.py`.

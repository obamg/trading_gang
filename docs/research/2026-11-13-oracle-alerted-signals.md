# Do Oracle's alerted signals predict the next hour? Pre-registration

Written 2026-11-13, before the production outcome data was queried for this question.
The result is appended at the bottom, once.

## Why this question

Two range-breakout studies lost money (`2026-11-12-inplay-orb.md`). Oracle is different in kind:
it combines the other modules' signals into a score from −100 to +100, and since 2026-09-24 it
flags the rarest scores as `alerted` and pushes them to Telegram. It also records what price did
afterwards. If the alerted signals do not predict direction, nothing built on them can work. If
they do, that is the first evidence of an edge in this repo and it earns a forward test.

## Hypothesis

Following an alerted Oracle signal in the direction of its score makes money over the next hour,
after trading costs.

## Measure

- Data: `oracle_signals` joined to `oracle_outcomes`, `signal_at >= 2026-09-24` (the day
  `alerted` started being recorded), `score <> 0`, `pnl_1h_pct` present.
- Directional return of one signal = `sign(score) * pnl_1h_pct` (percent). `pnl_1h_pct` is the
  raw price change one hour after the signal.
- Cost: 0.15% per trade (two taker fees plus slippage), the same friction the DayTrade
  playbook uses.
- Signals on the same day are not independent (the market moves together), so the unit is the
  day: each UTC day's signals are averaged into one number first.

## Pass test (judged once)

For alerted signals, on daily averages, all of:

1. at least 30 days that had an alerted signal;
2. average daily directional return minus 0.15% is above `1.28 * sd / sqrt(days)`;
3. both halves of the period, split by date, have an average above 0.15%.

The 4-hour return and the non-alerted signals are reported for context. They cannot pass the
test on their own.

## What a pass would and would not mean

This is every outcome that exists, so there is no untouched period to confirm on. A pass means
"worth a pre-registered forward test on new signals", not "trade it". A fail means Oracle's
alert gate is not picking direction better than cost, and a strategy built on it is dropped.

---

## Result

Run once on 2026-11-13 against production (read-only query). **Fail. A strategy built on
Oracle's alerts is dropped.**

Period 2026-09-24 to 2026-11-13. Unit = UTC day.

| | Days | Signals | Avg 1h directional | After 0.15% cost | Days positive | Avg 4h directional |
| --- | --- | --- | --- | --- | --- | --- |
| Alerted (the hypothesis) | 42 | 504 | −0.040% | −0.190% | 18 of 42 | −0.081% |
| Non-alerted (context) | 45 | 456,865 | −0.004% | −0.154% | 16 of 45 | −0.024% |

Against the pass test:

1. At least 30 alerted days: yes (42).
2. Daily average minus cost above the bar of +0.100% (sd 0.50): no (−0.190%).
3. Both halves above 0.15%: no (−0.024% over 18 days, then −0.053% over 24 days).

How to read it: over the next hour, alerted signals did not point the right way even before
costs, and they did slightly worse than the non-alerted ones. The 4-hour figure is negative
too. This says nothing about Oracle as an attention tool (what to look at); it says the alert
is not a direction call that can be traded mechanically at this horizon.

Not done, by the terms above: no re-run on other horizons, score thresholds or symbol subsets.

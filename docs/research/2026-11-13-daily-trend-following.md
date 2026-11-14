# Daily trend following (Turtle rules): pre-registration

Written 2026-11-13, before any daily bars or funding history were fetched.
The result is appended at the bottom, once. This is the third pre-registered test in two days
(after `2026-11-12-inplay-orb.md` and the not-yet-run Oracle question), so a pass here is weaker
evidence than a first test would be, and is treated that way below.

## Why this idea

Intraday range breakouts lost twice. The strategy with the longest public track record among
professional traders is not intraday at all: it is trend following on daily bars, as taught to
the Turtles by Richard Dennis and run by trend-following funds since. It trades rarely, holds
for days to weeks, and is managed once a day. Costs matter far less at that horizon, which was
the main thing killing the intraday setup.

## Rules (the published Turtle "System 2", not tuned)

- Universe, fixed in advance: BTC, ETH, SOL, BNB, XRP, DOGE, ADA, AVAX, LINK, LTC, DOT, BCH
  USDT perpetuals on Bybit, daily bars, each from its first available bar.
- N = average true range of the previous 20 days.
- Enter long when a day closes above the highest high of the previous 55 days; enter short when
  it closes below the lowest low of the previous 55 days. Fill at the next day's open.
- Exit a long when a day closes below the lowest low of the previous 20 days (mirror for
  shorts). Fill at the next day's open.
- Protective stop 2N from the entry price. If a day trades through it, exit at the stop, or at
  that day's open if it gapped beyond.
- One position per coin, no adding to winners.
- Result per trade in R, where 1R = 2N at entry. Costs: 0.05% taker fee and 0.05% slippage on
  each side, plus the actual funding paid or received while the position was open (Bybit's
  funding history).

## Pass test (judged once)

Trades in the same month are not independent (coins trend together), so the unit is the entry
month: each month's trades are averaged into one number. All of:

1. at least 30 months that had an entry, and at least 60 trades;
2. the average of the monthly numbers is above `1.28 * sd / sqrt(months)`;
3. both halves of the history, split by date, average above 0;
4. the most recent 12 months average above 0.

Long and short trades are reported separately for context. Neither side can pass on its own.

## What a pass would and would not mean

There is no untouched period to confirm on: this uses every daily bar that exists. A pass means
"build it as a second paper playbook and forward-test it", not "trade it". A fail means it is
dropped: no changing 55/20, the stop, the universe or the sides and re-running.

## Known weaknesses

- Twelve coins that are large today: survivorship bias, and it flatters the long side.
- The coins are highly correlated, so the real number of independent bets is small.
- Daily bars cannot show the order of events inside a day; stops are assumed to fill at the
  stop price unless the day opened beyond it.

---

## Result

Run once on 2026-11-13. **Fail, by the test above. Dropped, though it is the only one of the
three ideas that made money overall.**

Bybit daily bars from each coin's first day (BTC from 2020-03, the others from 2020-10 to
2021-10), real funding history, 354 closed trades, 8 still open and excluded.

| | Trades | Average | Median | Winners | Total | Avg hold | Funding cost |
| --- | --- | --- | --- | --- | --- | --- | --- |
| All | 354 | +0.302R | −1.00R | 29% | +106.7R | 33 days | −0.113R |
| Long | 193 | +0.543R | −1.02R | 22% | +104.8R | 30 days | −0.200R |
| Short | 161 | +0.012R | −0.65R | 39% | +2.0R | 38 days | −0.009R |

Against the pass test (unit = entry month, 68 months):

1. At least 30 months and 60 trades: yes.
2. Monthly average above the bar: no. +0.499R against a bar of +0.785R (sd 5.06).
3. Both halves above 0: yes (+0.808R, then +0.245R).
4. Last 12 months above 0: no (−0.143R over 10 months with entries).

By year: 2020 +7.66R (5 trades), 2021 −0.44R, 2022 +0.08R, 2023 +0.59R, 2024 +0.70R,
2025 −0.24R, 2026 +0.18R.

How to read it:

- This is the usual trend-following shape: most trades lose about 1R, and a few very large
  winners pay for them. All of the profit is on the long side; shorts broke even.
- The total is positive, but the result leans on a handful of huge months, which is exactly
  why the monthly spread is so wide and the average does not clear the luck margin.
- The second half is weaker than the first and the last year lost, so the evidence is getting
  worse over time, not better.
- Twelve coins that are large today flatter the long side (survivorship), so the true long
  result is lower than shown.

Not done, by the terms above: no re-run as long-only, with other lookbacks, or on another
universe. "Long-only would have passed" is a hypothesis this data suggested, which is the one
kind of hypothesis this data cannot test. Scratch script and bars are not in the repo.

---

## Addendum, 2026-11-13: read this result as inconclusive, not as "no edge"

An independent audit of the pass test found it underpowered. Nothing above is changed; this
corrects how the verdict should be read.

- With a monthly sd of 5.06 over 68 months, the standard error of the monthly mean is 0.61R.
  A true edge of +0.5R per month would have cleared the bar about one time in three, and all
  four conditions together about 15 to 20% of the time. Reaching 80% power needed a true
  monthly mean near +1.3R.
- The 90% interval on the monthly mean is roughly −0.29R to +1.28R: consistent with zero and
  with a large edge alike. The test could not tell them apart, so "dropped" overstated it.
- The decision stands for practical purposes: no playbook is built on this result, and the
  same data cannot be re-tested. What changes is the label, from "failed" to "not shown".
- A better-specified test (not run): weekly portfolio return in % of equity at fixed risk per
  trade, compared with buy-and-hold of the same basket (the long-side profit may be market
  beta), with the smallest detectable edge computed before looking.

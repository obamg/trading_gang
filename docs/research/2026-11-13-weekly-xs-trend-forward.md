# Weekly cross-sectional trend: forward test (pre-registration)

Written 2026-11-13, before the first forward week (starting 2026-11-16) exists. The result is
appended at the bottom, once, after week 104 settles.

## Why

The backtest in `2026-11-13-weekly-cross-sectional-trend.md` passed (+0.82% per week net over
231 weeks) but it was the fifth test in two days, and about half its profit came from ten
pumped coin-weeks. A backtest pass earns a paper portfolio and a forward record, nothing more.

## Rules

Exactly the backtest rules, unchanged: Monday 00:00 UTC rebalance; the 30 most-traded Bybit
USDT perps over 30 days (90 daily bars, a bar on each of the last 30 days, no stablecoin
bases); rank on 7, 14 and 28-day returns; long the top 10, short the bottom 10, equal weight,
0.5 of capital a side; Monday open to next Monday open; 0.10% per unit of turnover; actual
funding. They are code in `tradecore/app/modules/xstrend/rules.py`; every week row stores a
hash of that file, and only rows with the current hash count. Changing the rules restarts the
test.

Paper only. Nothing is sent to an exchange.

## Data

Weeks starting 2026-11-16 and later. None of them exist yet. Every input is a closed public
daily bar or a settled funding rate, so a week the scheduler missed is rebuilt later from the
same data and gives the same basket. Catching up is therefore allowed and is not a re-run.

## Unit and smallest detectable edge

Unit = one week. The backtest's weekly sd was 3.95%.

| Weeks | Standard error | Pass bar | True edge that passes 80% of the time |
| --- | --- | --- | --- |
| 52 | 0.55% | +0.70% | +1.16% |
| 104 | 0.39% | +0.50% | +0.82% |

One year cannot confirm an edge the size of the backtest's: a true +0.82% would pass only
about 59% of the time, and the more cautious +0.47% (the backtest without its five best weeks)
about 34%. So the test runs 104 weeks. Even then it has 80% power only against the full
backtest figure; if the true edge is +0.47% it passes about 47% of the time, and the likeliest
honest outcome is "inconclusive". That is known going in.

## Verdict (judged once, when 104 weeks have settled)

With m = mean weekly net return and se = sd / sqrt(104):

- **Pass:** m > 1.28 * se, and both halves (weeks 1 to 52, 53 to 104) average above 0.
- **Ruled out:** m + 1.28 * se < 0.10% per week.
- **Inconclusive:** anything else.

A skipped week (fewer than 30 eligible coins) does not count toward the 104. The running
numbers on the page before week 104 are descriptive. No early pass, no early stop, no change
of rules on the way.

Reported alongside, unable to change the verdict: the result before costs, costs and funding
separately, the equal-weight universe as benchmark, worst drawdown, and the share of profit
from the five best weeks.

## What a pass would and would not mean

A pass means the edge survived on data nobody had seen, at paper fills. It still would not
show the portfolio can be traded: paper fills ignore position limits, borrow-free shorting of
coins in a squeeze, order-book depth on a Monday open, and whether a delisting contract
accepts new positions. Those need a separate, small, real-money check, which is a decision for
a person and outside this repo (TradeCore routes no orders).

## Known weaknesses

- Fills are the Monday daily-bar open, with a flat 0.05% slippage per side.
- Intra-week moves are not modelled: a short that triples mid-week would be liquidated in
  reality and is carried to Monday here.
- The backtest found that trading one day late roughly removed the first-half result, so the
  edge may depend on exact timing that a person cannot reproduce by hand.

---

## Result

Not yet. Appended once, after week 104.

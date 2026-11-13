# Weekly cross-sectional trend on Bybit perps: pre-registration

Written 2026-11-13, before any data for this test was fetched. The result is appended at the
bottom, once. This is the fifth pre-registered test in two days, so a pass is weaker evidence
than a first test would be.

## Why this idea

Four directional tests found nothing at the day-trading horizon, and a literature survey found
no published evidence of a one-direction intraday edge on liquid coins after retail taker
costs. The one candidate with a peer-reviewed source and data unseen by it is cross-sectional
trend: coins that have recently outperformed other coins keep outperforming them (Fieberg,
Liedtke, Poddig, Walker & Zaremba, "A Trend Factor for the Cross Section of Cryptocurrency
Returns", JFQA 2025; sample April 2015 to May 2022). Their signal is a machine-learned blend
of 28 indicators over 3,000+ coins. This tests a deliberately crude version on liquid perps
only, on data after their sample ends. It is weekly and market-neutral, not day trading.

## Hypothesis

Each week, a portfolio long the recent relative winners and short the recent relative losers
among the most liquid Bybit USDT perpetuals makes money after fees, slippage and funding.

## Rules (fixed; nothing here is tuned)

- Rebalance every Monday 00:00 UTC, using only daily bars that closed before it.
- Universe at each rebalance: Bybit USDT linear perpetuals with at least 90 daily bars and a
  bar on each of the last 30 days, excluding stablecoin bases (USDC, BUSD, DAI, TUSD, FDUSD,
  USDE, PYUSD). Keep the 30 with the highest turnover over the last 30 days. Delisted
  contracts are included if the exchange API still serves them (see weaknesses).
- Signal: each coin's return over the last 7, 14 and 28 days. Rank the 30 coins on each
  (highest return = highest rank), average the three ranks. Ties broken by symbol name.
- Portfolio: long the top 10, short the bottom 10, equal weight, 0.5 of capital on each side
  (gross exposure 1, net 0). Hold to the next Monday open. Fewer than 30 eligible coins: no
  portfolio that week.
- Prices: Monday open to next Monday open. A coin with no bar at the exit uses its last close.
- Costs: 0.10% of every unit of notional traded (0.05% taker fee plus 0.05% slippage per
  side), on the actual turnover between the drifted old weights and the new ones. Plus the
  actual funding paid or received by each position (Bybit funding history).
- Weekly return = price P&L minus trading costs minus net funding, as a fraction of gross
  capital.

## Data

- Test window: weeks starting 2022-06-06 through the last complete week before 2026-11-13,
  about 230 weeks. All of it is after the source paper's sample.
- Bars from 2022-01-01 are used only to warm up the 90-day and 30-day filters.

## Smallest detectable edge (stated before looking)

Assuming a weekly sd near 3% for a portfolio like this, 230 weeks give a standard error of
about 0.20% and a pass bar of about 0.25% per week. A true edge of roughly 0.42% per week
(about 22% a year on gross capital, annual Sharpe near 1.0) would pass about 80% of the time.
A true Sharpe of 0.5 would pass only about half the time. So this test can confirm a strong
edge or rule out a strong one; a modest real edge will probably read as inconclusive. The
actual sd will be reported and these figures recomputed.

## Verdict rules (judged once)

Unit = one week. With m = mean weekly net return and se = sd / sqrt(weeks):

- **Pass:** at least 150 weeks, m > 1.28 * se, and both halves of the window average above 0.
- **Ruled out:** m + 1.28 * se < 0.10% per week (even the optimistic bound is under about 5%
  a year).
- **Inconclusive:** anything else.

Reported for context, unable to change the verdict: the last 52 weeks; the result before
costs; costs and funding separately; the long leg and the short leg each against the
equal-weight universe; the correlation of weekly returns with that universe; worst drawdown.

## What each verdict would mean

A pass means "build it as a paper portfolio and forward-test it", not "trade it". Ruled out or
inconclusive means it is not built. No re-run with other lookbacks, universe sizes, tercile
cuts or rebalance days.

## Known weaknesses

- If the API does not serve delisted contracts, the universe is survivors only. Losers that
  were delisted would have been shorts, so this biases the result downward for shorts and the
  direction of the net bias is unclear. The result states which case applied.
- The paper's signal is far richer than three return horizons; a fail of this crude version
  does not falsify the paper.
- Fills are assumed at the Monday open plus a flat 0.05%. Thin coins at rank 20 to 30 may cost
  more. Short availability and position limits are ignored.
- Daily bars only; intra-week liquidation risk on the short side is not modelled.

---

## Result

Run on 2026-11-13. **Pass, by the rules above. Not proof of an edge: read the caveats.**

Bybit daily bars and funding for 1,136 USDT perpetuals, 316 of them delisted (so not
survivors-only; 42 delisted contracts were actually held). 231 weeks, 2022-06-06 to 2026-11-02.

| | Per week |
| --- | --- |
| Net return | **+0.818%** (sd 3.95%, standard error 0.260%) |
| Pass bar / 90% bounds | +0.333% / +0.485% to +1.151% |
| Before costs | +0.720% |
| Trading costs | −0.103% |
| Funding | +0.201% received |
| First half / second half | +0.484% / +1.149% |
| Last 52 weeks | +1.221% |
| Long leg / short leg vs equal-weight universe | +0.771% / +0.669% |
| Correlation with the universe | +0.12 |
| Winning weeks / worst drawdown | 58% / −28.4% |

By year (weekly mean): 2022 +0.18%, 2023 +0.65%, 2024 +0.42%, 2025 +1.45%, 2026 +1.18%.
Smallest detectable edge at the observed sd: +0.55% per week (the prior guess was +0.42%).

Against the verdict rules: 231 weeks (needs 150), mean above the bar, both halves positive.

### Process notes

- The first run crashed on a reporting line before printing anything (Python 3.9 has no
  `statistics.correlation`); that line was replaced and the study run.
- An independent audit rebuilt the backtest from the cached data and matched it week by week.
  It found one bug: tied rank sums were broken by floating-point noise instead of by symbol
  name, in 12 of 231 weeks. Fixed with integer rank sums; the figures above are after the fix
  (before it: +0.806%). The verdict did not depend on it.
- The audit found no look-ahead, no missing funding, no price rescales or zero-volume bars in
  any holding window, and correct funding sign and turnover.

### Why this is weaker than the headline

- **It is carried by a few pumps.** The 10 best coin-weeks, all longs in coins that then
  exploded, are 55% of the total. The best 5 weeks are about 81 of 189 points; without them
  the mean is about +0.47% a week. The median week is +0.49%.
- **One of those may not be tradeable.** The best week (+26%) is a long in ALPACA during its
  delisting squeeze (+519%, plus funding). If the exchange blocked new longs or capped size
  after the delisting notice, about 0.12% a week of the result disappears.
- **Funding income is lumpy.** The +0.20% a week comes from a handful of pumped coins with
  deeply negative funding; the median week is +0.04%. Without any funding the net is +0.605%
  a week (t 2.35).
- **Execution timing matters.** With the signal one day old at entry, the net falls to +0.527%
  a week and the first half to +0.126%. (Implementation check; not part of the verdict.)
- **Costs are not the weak point.** At 0.20% and 0.30% per unit traded the net is +0.715% and
  +0.612%.
- **Not modelled:** squeezes against a short inside the week (one short rose 88% in a week),
  position limits, and whether shorts were available. A −28% drawdown on gross capital
  happened in the sample.
- **Fifth test in two days**, and the rules, though fixed in advance, were written by someone
  who had just read a paper saying this works. The t-statistic of 3.1 survives a correction
  for five tests; it does not survive the pump-dependence question, which only new data
  answers.

### What follows

By the terms above: this earns a paper portfolio and a forward test on weeks after
2026-11-13, with its own rules written first. It does not earn money. Nothing here is tuned
and nothing will be: the same rules, as is, forward.

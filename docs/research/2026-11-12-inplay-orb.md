# In-play ORB: pre-registration

Written 2026-11-12, before any data for the test window was fetched or looked at.
Results are appended at the bottom, once. The rules above the line are not edited afterwards.

## Why this idea

The DayTrade playbook (`tradecore/app/modules/daytrade/playbook.py`) lost 0.215R per trade on a
90-day replay. It takes the first two coins that break out each session. The research it borrows
from (Zarattini, Barbon & Aziz, "A Profitable Day Trading Strategy for the U.S. Equity Market")
does something different: it only trades names that are "in play", meaning unusually high
volume in the opening range compared with their own recent history. That selection step is the
part the playbook left out.

## Hypothesis

Trading only the one coin with the most unusual opening-range volume each session has a positive
expectancy after fees, where taking the first breakouts does not.

## Rules (the only change is the selection step)

- Universe, fixed in advance: BTC, ETH, SOL, BNB, XRP, DOGE, ADA, AVAX, LINK, LTC, DOT, BCH
  (USDT perpetuals on Bybit, 5-minute bars). All were listed well before the test window.
- Sessions, range, entry, filters, stop, target, time stop, fees and slippage: exactly the
  current `Playbook()` defaults, unchanged.
- Selection, decided at the moment the opening range completes (no later information):
  - relative volume = this session's opening-range volume / the average opening-range volume of
    the same session over the previous 14 days, per coin;
  - only the coin with the highest relative volume is eligible, and only if it is above 1.0;
  - if that coin produces no valid trigger, there is no trade that session.
- So at most one trade per session, two per day.

## Data

- Test window: 2025-11-25 00:00 UTC to 2026-08-10 00:00 UTC (about 8.5 months). None of it has
  been seen: the earlier replay used 2026-08-14 onward.
- Bars from 2025-11-10 are fetched only to warm up the 14-day volume baseline and the trend EMA.
- The already-seen period (2026-08-14 onward) is reported for information only. It cannot pass
  or fail the test.

## Pass test (judged once)

On the test window, all of:

1. at least 60 closed trades;
2. average net R above `1.28 * sd / sqrt(n)` (one-sided 90% bound), computed over all trades;
3. both halves of the window, split by date, have an average net R above 0.

Anything else is a fail. A fail means the idea is dropped, not adjusted: no changing the
threshold, the lookback, the universe or the sessions and re-running.

## Known weaknesses, stated up front

- The universe is twelve large coins chosen today. The original research ranked thousands of
  stocks. Twelve correlated majors may not contain a real "in play" name on most days.
- Choosing coins that are large today is a mild survivorship bias. The strategy trades both
  directions, which limits but does not remove it.
- Fills are simulated from 5-minute bars (next-bar open plus 0.05% slippage, pessimistic on
  stops). Funding is not charged.

---

## Result

Run once on 2026-11-13. **Fail. The idea is dropped.**

Test window (2025-11-25 to 2026-08-10, 12 coins, Bybit 5m bars, no gaps):

| | Trades | Average | Winners | Total |
| --- | --- | --- | --- | --- |
| In-play top-1 (the hypothesis) | 271 | −0.087R | 38% | −23.5R |
| Every coin, no selection (context) | 3,435 | −0.174R | 35% | −598.2R |

Against the pass test:

1. At least 60 trades: yes (271).
2. Average above the bar of +0.069R: no (−0.087R).
3. Both halves positive: no (first half −0.052R, second half −0.120R).

Both sessions lost: daily open −0.045R (124 trades), US open −0.122R (147 trades). 172 of 704
sessions had no in-play coin, and 111 of 382 picks produced no trade.

What it does show: the selection step roughly halved the loss per trade compared with trading
every breakout, so it was pointing the right way. It did not turn a losing setup into a winning
one. On the already-seen 90 days it averaged +0.037R over 92 trades, which is inside the noise
(bar +0.131R) and would have been misleading if that period had been the test.

Not done, by the terms above: no second run with a different threshold, lookback, universe or
session. The study script and bars were scratch files and are not in the repo; the rules above
are enough to rebuild them.

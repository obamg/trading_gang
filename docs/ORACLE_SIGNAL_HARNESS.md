# Oracle signal-accuracy harness

A task-local harness for one repeated question: **does adding or reweighting an
input make Oracle's alerts more accurate, out of sample?** Every candidate —
existing or new — goes through the same loop and the same eval. Research feeds
candidates in; the eval decides. Nothing gets a weight because it sounds good.

Why this exists: three bot strategies (0 for 3), a BTC bake-off (0 of 27), a
wallet scorer with no feedback loop, and a copy-trading pre-mortem showing
ex-post rank is market beta — all failed the same way, by adding inputs without
a pre-committed test. Since 2026-09-24 Oracle writes a signal and an outcome row
per trigger (~7k/day) with an `alerted` flag. It is the first dataset in this
repo that can say no.

## Objective

- **Ship:** a ranked, evidence-backed list of changes to Oracle's inputs and
  weights, each with a measured out-of-sample lift, and the alert gate tuned
  from data rather than defaults.
- **Do not ship:** any trading or order routing; any new input that has not
  passed the eval; any weight change justified by intuition.

## Inputs

- Repo: `tradecore/app/modules/oracle/engine.py` — `DEFAULT_WEIGHTS`,
  `compute_live_score`, `should_alert`. Tables `oracle_signals`,
  `oracle_outcomes`.
- Data: prod Postgres, read-only, via the documented psql pattern.
- External candidates, verified 2026-09-24 via Context7, ranked by
  **backtestable history per euro** — a parameter you cannot test against the
  past can only be forward-tested, and forward tests here have cost months
  each:

| Source | Adds | Free tier | History | Priority |
| --- | --- | --- | --- | --- |
| DefiLlama | chain/protocol TVL, stablecoin supply, DEX volume, perps OI, fees | full, no key | full daily | **1** |
| Glassnode | 800+ on-chain, exchange balances, realized cap | Basic (T1) @ 24h | to 2011 | **2** |
| Bybit/Binance native | funding, OI, long/short, liquidations | free | deep | already in — the only input carrying signal on day one |
| Coinglass | cross-exchange OI/funding/liq OHLC | none surfaced | all plans; Hobbyist ≥4h | 3 — only for sub-4h cross-exchange |
| CMC | search-trending, gainers/losers, community | Basic 15k credits | thin | already in; a **contrarian** covariate |
| Messari | curated metrics; unlocks are enterprise | metered | 1m–1y | 4 |
| LunarCrush | Galaxy Score, AltRank, social dominance | paid | exists | **hold** — most ex-post-contaminated class |
| Santiment | on-chain (chainpulse) | free = >30d old only | good | backtest-only |

- Credentials policy: free tiers first. A paid key needs a measured lift from
  the free tier of the same source before purchase.

## Loop

1. **Discover current state** — run Eval A. Until `n_alerted ≥ 200` with
   `was_correct_4h` populated, the loop is *waiting*, not blocked.
2. **Measure what you already have before adding anything.** Eval B ranks the
   eight existing inputs by lift. Expect radarx, flowpulse, liquidmap and
   gemradar near zero on majors — they returned `neutral/null` on BTC/ETH/SOL/
   XRP at first light. An input that never contributes is the cheapest
   accuracy gain there is: fix or drop it before buying data.
3. **Add one candidate at a time** as a *recorded but unweighted* column
   (weight 0) for ≥ 7 days, so its value lands on every signal without moving
   the score. Then run Eval C on it.
4. **Promote or discard** by the gate. Promotion = a weight. Discard = the
   column stays for later re-test; the weight stays 0.
5. **Stop** on a failed gate (record it; do not retune), on an input only
   obtainable by scraping, or on any change that would route an order.

## Eval

**A — the gate that judges the gate.** Alerted tail vs. everything else.

```sql
SELECT s.alerted, count(*) AS n,
       round(100.0 * avg(o.was_correct_1h::int), 1) AS hit_1h,
       round(100.0 * avg(o.was_correct_4h::int), 1) AS hit_4h,
       round(avg(o.pnl_4h_pct)::numeric, 3) AS pnl_4h
FROM oracle_signals s JOIN oracle_outcomes o ON o.signal_id = s.id
WHERE o.was_correct_4h IS NOT NULL GROUP BY 1;
```

Pass: alerted `hit_4h` exceeds non-alerted by ≥ 5 points at `n_alerted ≥ 200`,
and alerted `pnl_4h` is positive **after subtracting the non-alerted mean** —
the benchmark is the middle, never zero; a rally flatters everything.
Fail: disable the gate by setting `ORACLE_ALERT_MIN_CONFLUENCE` above any
observed confluence. Do not lower the bar to make it pass.

**B — per-input lift.** For each key in `signals_breakdown`, how often did that
input's contribution point the way the 4h move actually went?

```sql
SELECT k AS input, count(*) AS n,
       round(100.0 * avg((sign((v->>'contribution')::numeric) = sign(o.pnl_4h_pct))::int), 1) AS agree_4h
FROM oracle_signals s
JOIN oracle_outcomes o ON o.signal_id = s.id,
     jsonb_each(s.signals_breakdown::jsonb) AS b(k, v)
WHERE o.pnl_4h_pct IS NOT NULL AND (v->>'contribution')::numeric <> 0
GROUP BY k ORDER BY agree_4h DESC;
```

An input at ~50% is a coin; its weight should be 0. Weights are then set in
proportion to `max(agree_4h − 50, 0)`, re-derived monthly, never by hand.

**C — candidate lift.** Eval B on the new column over its unweighted week.
Promote only if its `agree_4h` beats the best existing input's confidence
interval on the **second half** of the window — the first half is for looking,
the second half is the test.

- Failure owner: whoever proposed the input. On ambiguity the answer is no.

## Handoff

- **Status (2026-09-25):** harness written. Eval A/B not yet runnable —
  290+ signals exist but `was_correct_4h` needs ~4h of wall-clock per row and
  `n_alerted` is 4. First meaningful Eval A ≈ 2026-10-08.
- **Evidence:** PR #29 (Oracle's first signal, ever), PR #30 (rarity gate +
  `alerted` flag), the `oracle_alert_suppressed` log stream.
- **Next action:** run Eval B once 500 rows carry `pnl_4h_pct`; expect four
  dead inputs. Fix those — already integrated, already free — before adding
  DefiLlama. Then DefiLlama TVL-delta as the first weight-0 column.
- **Blocked on a human:** nothing. Blocked on time: everything above.

## Extraction candidate

`walletwatch/discovery`'s scorer has the identical no-feedback-loop defect. If
Eval B/C runs for a second module, promote this into a shared `signal-eval`
skill: one SQL-backed script, one pass/fail contract, one weight-derivation rule.

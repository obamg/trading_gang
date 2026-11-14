# Research notes

One file per question, written before the data is looked at, with the result appended once.

Every note states, above the line and in advance:

1. The hypothesis and the exact rules, including costs.
2. The data, and which part has never been seen.
3. The unit of independence (trade, day, week, month) and why.
4. The pass test, judged once. A fail means dropped, not retuned.
5. **The smallest edge the test can detect.** From a prior estimate of the spread and the
   expected n, state the true effect that would pass about 80% of the time. If no realistic
   edge reaches that, the test is not worth running as designed.

Every result reports:

- the estimate with its upper and lower bound, so "ruled out" (the upper bound is below what
  would pay costs) is told apart from "inconclusive" (the interval still contains a useful edge);
- the result before costs next to the result after costs;
- a benchmark: buy-and-hold for anything trend-shaped, random entries with the same stop and
  target for entry rules.

Study scripts are committed next to the note, or the note says where they are and why not.

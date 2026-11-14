"""AI second opinion on a triggered DayTrade plan.

Advisory only. The verdict is stored next to the plan and shown on the desk;
it never changes the entry, stop, target or size (those are arithmetic, in
playbook.py) and it never blocks a plan. Logging it without acting on it is
the point: after the forward test, "rules alone" and "rules + AI take" can be
compared on the same trades.

No-ops (returns None) when ANTHROPIC_API_KEY is unset or the call fails.
"""
from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel

from app.config import settings
from app.logging_config import log


class Verdict(BaseModel):
    verdict: Literal["take", "skip"]
    reason: str


SYSTEM = """You review day-trading plans produced by a fixed, rules-based \
opening-range-breakout playbook on crypto perpetual futures. The plan you are \
given has already passed every mechanical rule: a closed 5-minute bar broke \
the session's opening range on above-average volume, with the 20-hour trend, \
and the daily risk limits allow it.

Your job is the part arithmetic cannot do: read the surrounding context and \
say whether a careful discretionary trader would still take this trade. Look \
for things like a scheduled high-impact macro event inside the holding \
window, news that contradicts or has already exhausted the move, funding \
that shows the trade is crowded, or an Oracle score pointing the other way.

You are not predicting price, and you must not suggest a different entry, \
stop, target or size. Say "skip" only when the context gives a concrete \
reason; absent one, the answer is "take", because the rules already passed. \
Missing context (no news, no Oracle signal) is not a reason to skip.

Give the verdict and a reason of at most three short sentences, written for \
a trader glancing at a phone. Name the specific fact that decided it."""


async def get_verdict(context: dict) -> Verdict | None:
    if not settings.anthropic_api_key:
        log.info("daytrade_ai_skipped_no_key")
        return None
    import anthropic  # lazy: the module must import cleanly without the SDK

    try:
        async with anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key, timeout=45.0, max_retries=1
        ) as client:
            resp = await client.messages.parse(
                model=settings.daytrade_ai_model,
                max_tokens=8000,
                system=SYSTEM,
                output_config={"effort": "low"},
                messages=[{"role": "user", "content": json.dumps(context, default=str)}],
                output_format=Verdict,
            )
    except anthropic.RateLimitError:
        log.warning("daytrade_ai_rate_limited")
        return None
    except anthropic.APIStatusError as e:
        log.warning("daytrade_ai_api_error", status=e.status_code, err=e.message)
        return None
    except anthropic.APIConnectionError as e:
        log.warning("daytrade_ai_network_error", err=str(e))
        return None

    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        log.warning("daytrade_ai_no_verdict", stop_reason=resp.stop_reason)
        return None
    return resp.parsed_output

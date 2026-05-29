"""Claude-powered morning brief writer. Takes the deterministic fleet summary +
anomaly list and produces a one-page markdown brief in Senior PE voice.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import date

from anthropic import Anthropic
from dotenv import load_dotenv

from .anomaly_detector import Anomaly


SYSTEM_PROMPT = """You are a Senior Production Engineer writing the daily morning brief for an asset team's 6:30am standup. You're given:

- Today's date
- Fleet-wide summary stats (total BOPD, water cut, average runtime)
- A list of anomalies detected overnight by deterministic Python rules, ranked by severity

Write a one-page markdown brief in this exact structure:

1. **# Daily Production Brief — {date}** (top heading)
2. **## Bottom Line** — 2-3 sentence executive summary. Lead with the worst news.
3. **## Field Status** — 3-bullet recap of fleet KPIs (BOPD, water cut, runtime)
4. **## Top Priorities** — Numbered list of HIGH-severity anomalies, each with: well, what happened (1 sentence citing the evidence), action owner & deadline. If no HIGH items, say "No HIGH-priority anomalies — fleet is stable."
5. **## Watch List** — MEDIUM-severity anomalies as a compact table (Well, Category, Headline, Action)
6. **## Closing** — One sentence either reassuring (if stable) or escalating (if multiple HIGH items)

Style:
- Write the way a Staff Production Engineer talks to an Ops Manager — terse, specific, no hedging, no fluff
- Use the evidence numbers verbatim from the anomaly data — never round or generalize
- Action items must have an owner role (lease operator, field foreman, on-call engineer) and a deadline
- Never invent anomalies not in the input. Never reference wells not in the input.
- **First character of your response must be `#` — no preamble.**
"""


def write_brief(
    summary: dict,
    anomalies: list[Anomaly],
    brief_date: str | None = None,
    model: str = "claude-sonnet-4-6",
    client: Anthropic | None = None,
) -> str:
    if client is None:
        load_dotenv()
        client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    brief_date = brief_date or date.today().isoformat()
    anomaly_dicts = [{**asdict(a)} for a in anomalies]

    user_prompt = (
        f"Date: {brief_date}\n\n"
        f"Fleet summary:\n{json.dumps(summary, indent=2)}\n\n"
        f"Anomalies detected overnight ({len(anomalies)} total):\n"
        f"{json.dumps(anomaly_dicts, indent=2)}\n\n"
        "Write the morning brief."
    )

    response = client.messages.create(
        model=model,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    text = "".join(b.text for b in response.content if b.type == "text")

    # Belt-and-suspenders: strip any preamble before the first markdown header.
    first_header = text.find("\n#")
    if first_header > 0 and not text.lstrip().startswith("#"):
        text = text[first_header:].lstrip()
    return text

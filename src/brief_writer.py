"""Claude-powered morning brief writer. Takes the deterministic fleet summary +
anomaly list and produces a one-page markdown brief in Senior PE voice.

Detection stays deterministic — the LLM only narrates. If no API key is present
(public demo, CI without a secret), ``render_brief_markdown`` produces a fully
deterministic brief from the same data, so the pipeline never just crashes.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import date

from dotenv import load_dotenv

from .anomaly_detector import Anomaly


class MissingAPIKey(RuntimeError):
    """Raised when an LLM brief is requested without ANTHROPIC_API_KEY set."""


SYSTEM_PROMPT = """You are a Senior Production Engineer writing the daily morning brief for an asset team's 6:30am standup. You're given:

- Today's date
- Fleet-wide summary stats (total BOPD, water cut, average runtime)
- A list of anomalies detected overnight by deterministic Python rules, ranked by severity then by DEFERRED $/day (money first)

Write a one-page markdown brief in this exact structure:

1. **# Daily Production Brief — {date}** (top heading)
2. **## Bottom Line** — 2-3 sentence executive summary. Lead with the worst news AND the total deferred $/day at risk.
3. **## Field Status** — 3-bullet recap of fleet KPIs (BOPD, water cut, runtime)
4. **## Top Priorities** — Numbered list of HIGH-severity anomalies, each with: well, what happened (1 sentence citing the evidence incl. deferred bbl/$ where present), action owner & deadline. If no HIGH items, say "No HIGH-priority anomalies — fleet is stable."
5. **## Watch List** — MEDIUM-severity anomalies as a compact table (Well, Category, Headline, Action)
6. **## Data Quality / Acknowledged** — note any comms-loss / metering-dropout flags and any acknowledged (known/planned) items that were suppressed from priorities.
7. **## Closing** — One sentence either reassuring (if stable) or escalating (if multiple HIGH items)

Style:
- Write the way a Staff Production Engineer talks to an Ops Manager — terse, specific, no hedging, no fluff
- Use the evidence numbers verbatim from the anomaly data — never round or generalize
- Action items must have an owner role (lease operator, field foreman, on-call engineer) and a deadline
- Never invent anomalies not in the input. Never reference wells not in the input.
- **First character of your response must be `#` — no preamble.**
"""


def render_brief_markdown(summary: dict, anomalies: list[Anomaly],
                          brief_date: str | None = None) -> str:
    """Deterministic morning brief (no LLM) — used as the no-API-key fallback and
    as the committed sample. Same data the LLM narrates, just templated."""
    brief_date = brief_date or date.today().isoformat()
    active = [a for a in anomalies if not a.acknowledged]
    acked = [a for a in anomalies if a.acknowledged]
    highs = [a for a in active if a.severity == "HIGH"]
    meds = [a for a in active if a.severity == "MEDIUM"]
    dq = [a for a in active if a.category in ("comms_loss", "meter_dropout")]
    total_deferred_bopd = sum(a.deferred_bopd for a in active)
    total_deferred_usd = sum(a.deferred_usd_per_day for a in active)

    L = [f"# Daily Production Brief — {brief_date}", ""]
    L.append("## Bottom Line")
    if highs:
        L.append(f"{len(highs)} HIGH-priority well(s) overnight; "
                 f"~{total_deferred_bopd:.0f} BOPD (${total_deferred_usd:,.0f}/day) deferred and at risk. "
                 f"{len(meds)} on the watch list.")
    else:
        L.append("No HIGH-priority anomalies — fleet is stable. "
                 f"{len(meds)} item(s) on the watch list.")
    L += ["", "## Field Status",
          f"- Total oil: **{summary.get('total_bopd', 0):.0f} BOPD** across {summary.get('well_count', 0)} wells",
          f"- Water cut: **{summary.get('water_cut_pct', 0):.0f}%**",
          f"- Avg runtime: **{summary.get('avg_runtime_pct', 0):.1f}%**", ""]

    L.append("## Top Priorities")
    if highs:
        for i, a in enumerate(highs, 1):
            defer = (f" — deferring ~{a.deferred_bopd:.0f} BOPD (${a.deferred_usd_per_day:,.0f}/day)"
                     if a.deferred_bopd > 0 else "")
            L.append(f"{i}. **{a.well_id}** — {a.headline}{defer}. _Action:_ {a.recommended_action}")
    else:
        L.append("No HIGH-priority anomalies — fleet is stable.")
    L.append("")

    L.append("## Watch List")
    if meds:
        L += ["| Well | Category | Headline | Action |", "|---|---|---|---|"]
        for a in meds:
            L.append(f"| {a.well_id} | {a.category} | {a.headline} | {a.recommended_action} |")
    else:
        L.append("Nothing on the watch list.")
    L.append("")

    if dq or acked:
        L.append("## Data Quality / Acknowledged")
        for a in dq:
            L.append(f"- ⚠️ **{a.well_id}** — {a.headline} (verify before dispatching).")
        for a in acked:
            L.append(f"- 🔕 **{a.well_id}** ({a.category}) suppressed — acknowledged / known event.")
        L.append("")

    L.append("## Closing")
    L.append("Multiple HIGH items — escalate at standup." if len(highs) > 1
             else ("One HIGH item to close out today." if highs else "Fleet stable; routine monitoring."))
    return "\n".join(L)


def write_brief(
    summary: dict,
    anomalies: list[Anomaly],
    brief_date: str | None = None,
    model: str | None = None,
    client=None,
) -> str:
    load_dotenv()
    if client is None:
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise MissingAPIKey(
                "ANTHROPIC_API_KEY is not set. Use render_brief_markdown() for a "
                "deterministic brief, or set the key to get the LLM-narrated version.")
        from anthropic import Anthropic
        client = Anthropic(api_key=key)
    # Honor the MODEL env var that .env.example documents; fall back to the default.
    model = model or os.environ.get("MODEL", "claude-sonnet-4-6")

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

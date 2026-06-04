"""Main entry point: run once per morning. Cron, GitHub Actions, and Streamlit
all call into this same function. Outputs to briefs/YYYY-MM-DD.md."""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown

from .anomaly_detector import load_acknowledgements, scan_fleet
from .brief_writer import MissingAPIKey, render_brief_markdown, write_brief
from .data_loader import fleet_summary, load_fleet


DEFAULT_DATA_DIR = "data/synthetic/fleet"
BRIEFS_DIR = Path("briefs")
ACK_PATH = "acknowledged.yml"


def run(data_dir: str = DEFAULT_DATA_DIR, brief_date: str | None = None, verbose: bool = False) -> Path:
    """Generate today's brief and persist to disk. Returns the brief's path."""
    console = Console()
    brief_date = brief_date or date.today().isoformat()

    if verbose:
        console.print(f"[bold cyan]Loading fleet from {data_dir}...[/]")
    fleet = load_fleet(data_dir)
    if not fleet:
        raise RuntimeError(f"No wells found in {data_dir}. Run data/synthetic/generate_fleet.py first.")

    summary = fleet_summary(fleet)
    if verbose:
        console.print(f"[bold]Fleet:[/] {summary['well_count']} wells · "
                      f"{summary['total_bopd']:.0f} BOPD · {summary['water_cut_pct']:.0f}% WC")

    if verbose:
        console.print("[bold cyan]Scanning for anomalies...[/]")
    acknowledged = load_acknowledgements(ACK_PATH)
    anomalies = scan_fleet(fleet, acknowledged=acknowledged)
    if verbose:
        sev_counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
        for a in anomalies:
            sev_counts[a.severity] += 1
        deferred = sum(a.deferred_usd_per_day for a in anomalies if not a.acknowledged)
        console.print(f"[bold]Anomalies:[/] {sev_counts['HIGH']} HIGH · {sev_counts['MEDIUM']} MEDIUM · "
                      f"{sev_counts['LOW']} LOW · ${deferred:,.0f}/day deferred")

    # Detection is deterministic; the LLM only narrates. With no API key we still
    # emit a real (templated) brief instead of crashing.
    try:
        if verbose:
            console.print("[bold cyan]Writing brief (LLM)...[/]")
        brief_md = write_brief(summary, anomalies, brief_date=brief_date)
    except MissingAPIKey:
        if verbose:
            console.print("[yellow]No ANTHROPIC_API_KEY — writing deterministic brief.[/]")
        brief_md = render_brief_markdown(summary, anomalies, brief_date=brief_date)

    BRIEFS_DIR.mkdir(exist_ok=True)
    out_path = BRIEFS_DIR / f"{brief_date}.md"
    out_path.write_text(brief_md)
    if verbose:
        console.print(f"\n[bold green]Wrote {out_path}[/]\n")
        console.print(Markdown(brief_md))
    return out_path


def main():
    parser = argparse.ArgumentParser(description="Run the daily production digest.")
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    parser.add_argument("--date", default=None, help="Override brief date (YYYY-MM-DD)")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()
    run(data_dir=args.data_dir, brief_date=args.date, verbose=args.verbose)


if __name__ == "__main__":
    main()

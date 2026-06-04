"""Streamlit history viewer for the Daily Production Digest."""
from __future__ import annotations

import subprocess
import sys
from datetime import date
from pathlib import Path

# Ensure repo root is on sys.path so `src.*` imports work on Streamlit Cloud.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# --- Self-heal stale bytecode / module cache (Streamlit Cloud) --------------
# Streamlit reuses the container across redeploys; a cached .pyc or already-imported
# OLD module can lack symbols added in a newer commit, surfacing as a startup
# ImportError for a name that exists in the source. Purge src/ bytecode + evict
# cached src modules so every submodule reloads from CURRENT source (no-op when clean).
import shutil as _shutil
for _pycache in (REPO_ROOT / "src").rglob("__pycache__"):
    _shutil.rmtree(_pycache, ignore_errors=True)
for _name in [m for m in sys.modules if m == "src" or m.startswith("src.")]:
    del sys.modules[_name]

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src import __version__
from src.anomaly_detector import load_acknowledgements, scan_fleet
from src.data_loader import fleet_summary, load_fleet


st.set_page_config(page_title="Daily Production Digest", page_icon="📅", layout="wide")

st.title(f"Daily Production Digest `v{__version__}`")
st.caption("Scheduled AI agent that writes a morning brief for asset teams. Built by an ex-OXY / ex-Shell Staff PE.")

with st.expander(f"🆕 What's new in v{__version__}"):
    st.markdown(
        "- **Ranked by deferred barrels & dollars**, not z-score — the brief leads with "
        "where the money is leaking, not the alphabetically-first well.\n"
        "- **Sensor-dropout vs. comms-loss vs. real-trip** detection — a flat-lined "
        "transmitter is flagged as a data-quality event, not a phantom 100% rate drop.\n"
        "- **Acknowledge / suppress known events** (`acknowledged.yml`) so a planned "
        "workover doesn't re-fire HIGH every morning (alarm-fatigue control).\n"
        "- **Water-cut context** on rate drops — distinguishes watering-out from a pump issue.\n"
        "- **Works with no API key** — a deterministic brief is rendered when the LLM "
        "narrator is unavailable (detection was always deterministic).\n"
        "- **Honest backtest** — near-threshold decoy wells + a real lead-time/latency metric "
        "(`python -m src.backtest`)."
    )

DATA_DIR = REPO_ROOT / "data" / "synthetic" / "fleet"
BRIEFS_DIR = REPO_ROOT / "briefs"
BRIEFS_DIR.mkdir(exist_ok=True)


def _bootstrap_fleet() -> None:
    """Generate synthetic fleet data on first run if it isn't already there."""
    if not any(DATA_DIR.glob("well_*.csv")):
        with st.status("First-time setup: generating synthetic fleet…", expanded=False):
            subprocess.run(
                [sys.executable, str(REPO_ROOT / "data" / "synthetic" / "generate_fleet.py")],
                check=True,
            )


_bootstrap_fleet()

with st.sidebar:
    st.header("Generate brief")
    if st.button("Run morning brief now", type="primary"):
        with st.spinner("Scanning fleet + writing brief…"):
            from src.brief_writer import MissingAPIKey, render_brief_markdown, write_brief
            fleet = load_fleet(DATA_DIR)
            summary = fleet_summary(fleet)
            acknowledged = load_acknowledgements(REPO_ROOT / "acknowledged.yml")
            anomalies = scan_fleet(fleet, acknowledged=acknowledged)
            try:
                brief_md = write_brief(summary, anomalies)
            except MissingAPIKey:
                brief_md = render_brief_markdown(summary, anomalies)
                st.info("No ANTHROPIC_API_KEY — generated a deterministic brief "
                        "(detection is deterministic; the LLM only adds prose).")
            today = date.today().isoformat()
            (BRIEFS_DIR / f"{today}.md").write_text(brief_md)
        st.success("Brief generated. Reload page.")

    st.divider()
    st.subheader("Brief history")
    briefs = sorted(BRIEFS_DIR.glob("*.md"), reverse=True)
    if briefs:
        selected = st.selectbox("Pick a date", briefs, format_func=lambda p: p.stem)
    else:
        st.info("No briefs yet. Click \"Run morning brief now\".")
        selected = None

# Main panel
col1, col2 = st.columns([2, 1])

with col1:
    st.subheader("Brief")
    if selected:
        st.markdown(selected.read_text())
    elif (BRIEFS_DIR / "sample.md").exists():
        st.info("Showing committed sample brief. Click \"Run morning brief now\" to generate a fresh one.")
        st.markdown((BRIEFS_DIR / "sample.md").read_text())
    else:
        st.info("No briefs yet. Click \"Run morning brief now\".")

with col2:
    st.subheader("Fleet snapshot")
    fleet = load_fleet(DATA_DIR)
    summary = fleet_summary(fleet)
    st.metric("Wells", summary["well_count"])
    st.metric("Total BOPD", f"{summary['total_bopd']:.0f}")
    st.metric("Water cut", f"{summary['water_cut_pct']:.0f}%")
    st.metric("Avg runtime", f"{summary['avg_runtime_pct']:.1f}%")

    acknowledged = load_acknowledgements(REPO_ROOT / "acknowledged.yml")
    anomalies = scan_fleet(fleet, acknowledged=acknowledged)
    active = [a for a in anomalies if not a.acknowledged]
    if anomalies:
        st.subheader("Anomalies (deterministic)")
        total_deferred = sum(a.deferred_usd_per_day for a in active)
        st.metric("Deferred production at risk", f"${total_deferred:,.0f}/day")
        sev_counts = pd.Series([a.severity for a in active]).value_counts()
        cols = st.columns(max(len(sev_counts), 1))
        for c, (sev, count) in zip(cols, sev_counts.items()):
            c.metric(sev, int(count))

        with st.expander("Drill in (ranked by deferred $)"):
            df = pd.DataFrame([
                {"Well": a.well_id, "Sev": a.severity, "Category": a.category,
                 "Deferred $/day": f"${a.deferred_usd_per_day:,.0f}" if a.deferred_usd_per_day else "—",
                 "Headline": a.headline, "Ack": "🔕" if a.acknowledged else ""}
                for a in anomalies
            ])
            st.dataframe(df, use_container_width=True, hide_index=True)

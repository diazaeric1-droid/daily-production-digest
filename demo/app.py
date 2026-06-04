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
from src.anomaly_detector import scan_fleet
from src.data_loader import fleet_summary, load_fleet


st.set_page_config(page_title="Daily Production Digest", page_icon="📅", layout="wide")

st.title(f"Daily Production Digest `v{__version__}`")
st.caption("Scheduled AI agent that writes a morning brief for asset teams. Built by an ex-OXY / ex-Shell Staff PE.")

with st.expander("🆕 What's new in v0.2.0"):
    st.markdown(
        "- **Robust anomaly detection** — rolling median + MAD robust z-scores; "
        "each flag reports *N sigma off this well's own baseline*.\n"
        "- **Decline-aware rate-drop flagging** — expected Arps rate (log-linear "
        "decline fit), not a flat 7-day mean, so healthy decliners stop over-flagging.\n"
        "- **Least-squares trend slopes** — recovers an amps-creep well the old "
        "2-point estimator missed.\n"
        "- **Pluggable historian adapter protocol** — `FleetSource` + a second "
        "(SQLite / time-range) adapter alongside the CSV loader.\n"
        "- **Backtest harness** — precision / recall / lead-time vs. seeded anomalies "
        "(`python -m src.backtest`).\n"
        "- Empty/short-frame guards; honors the `MODEL` env var."
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
            from src.brief_writer import write_brief
            fleet = load_fleet(DATA_DIR)
            summary = fleet_summary(fleet)
            anomalies = scan_fleet(fleet)
            brief_md = write_brief(summary, anomalies)
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

    anomalies = scan_fleet(fleet)
    if anomalies:
        st.subheader("Anomalies (deterministic)")
        sev_counts = pd.Series([a.severity for a in anomalies]).value_counts()
        for sev, count in sev_counts.items():
            st.metric(f"{sev}", int(count))

        with st.expander("Drill in"):
            df = pd.DataFrame([
                {"Well": a.well_id, "Sev": a.severity, "Category": a.category, "Headline": a.headline}
                for a in anomalies
            ])
            st.dataframe(df, use_container_width=True, hide_index=True)

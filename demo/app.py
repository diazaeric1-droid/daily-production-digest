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

import theme
from src import __version__
from src.anomaly_detector import load_acknowledgements, scan_fleet
from src.data_loader import fleet_summary, load_fleet
from src.ledger import build_ledger


theme.setup_page("Daily Production Digest", icon="📅")
theme.suite_nav("pe-digest")

theme.header(
    "Daily Production Digest",
    subtitle="Scheduled AI agent that writes a morning brief for asset teams. Built by an ex-OXY / ex-Shell Staff PE.",
    chips=[(f"v{__version__}", "ver"), ("scheduled agent", "info")],
)

with st.expander(f"🆕 What's new in v{__version__}"):
    st.markdown(
        "- **Unified dark + navy suite theme** + a **cross-app sidebar suite navigator** — "
        "the digest now looks and links like one product across the PE suite.\n"
        "- **First visualizations** (previously text-only) — a **fleet oil-rate trend** and a "
        "**top deferred-$ offender bar** surface the leak at a glance.\n"
        "- **Rolling lost-production ledger** — cumulative deferred **$/bbl by cause** over a "
        "trailing window (MTD-style), with a **deep-link to Deferment IQ**.\n"
        "- **Performance** — cached fleet load/scan, and the app **auto-selects the new brief** "
        "after generation.\n"
        "- **Shared fleet registry** — Permian field/formation identity is consistent across the suite.\n"
        "- **Swept deprecated `use_container_width`** (→ `width=\"stretch\"`); requires `streamlit>=1.50`."
    )

DATA_DIR = REPO_ROOT / "data" / "synthetic" / "fleet"
BRIEFS_DIR = REPO_ROOT / "briefs"
BRIEFS_DIR.mkdir(exist_ok=True)


@st.cache_data(show_spinner=False)
def _load_fleet_cached(data_dir: str) -> dict:
    """Cache the expensive per-well CSV load. Takes a string path (hashable);
    returns the picklable dict[str, DataFrame]."""
    return load_fleet(data_dir)


@st.cache_data(show_spinner=False)
def _scan_fleet_cached(data_dir: str, ack_path: str) -> list:
    """Cache the deterministic fleet scan. Re-derives the fleet from the cached
    loader so the same inputs reuse the same result across reruns."""
    fleet = _load_fleet_cached(data_dir)
    acknowledged = load_acknowledgements(ack_path)
    return scan_fleet(fleet, acknowledged=acknowledged)


@st.cache_data(show_spinner=False)
def _build_ledger_cached(data_dir: str, ack_path: str, window_days: int = 30):
    """Cache the day-by-day ledger replay (re-runs the scan over a trailing
    window). Re-derives the fleet from the cached loader so reruns reuse it."""
    fleet = _load_fleet_cached(data_dir)
    acknowledged = load_acknowledgements(ack_path)
    return build_ledger(fleet, window_days=window_days, acknowledged=acknowledged)


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
    byok_key = st.text_input(
        "🔑 Anthropic API key (optional)", type="password",
        help="Bring your own key — used only for this session, never stored. Powers the "
             "Senior-PE narrated brief. Without it, a deterministic brief is rendered instead.")
    if st.button("Run morning brief now", type="primary"):
        with st.spinner("Scanning fleet + writing brief…"):
            from src.brief_writer import MissingAPIKey, render_brief_markdown, write_brief
            fleet = _load_fleet_cached(str(DATA_DIR))
            summary = fleet_summary(fleet)
            anomalies = _scan_fleet_cached(str(DATA_DIR), str(REPO_ROOT / "acknowledged.yml"))
            client = None
            if byok_key:
                from anthropic import Anthropic
                client = Anthropic(api_key=byok_key)
            try:
                brief_md = write_brief(summary, anomalies, client=client)
            except MissingAPIKey:
                brief_md = render_brief_markdown(summary, anomalies)
                st.info("No API key entered — generated a deterministic brief. Add your Anthropic "
                        "key in the sidebar for the Senior-PE narrated version.")
            today = date.today().isoformat()
            (BRIEFS_DIR / f"{today}.md").write_text(brief_md)
        st.success("Brief generated.")
        st.rerun()

    st.divider()
    st.subheader("Brief history")
    briefs = sorted(BRIEFS_DIR.glob("*.md"), reverse=True)
    if briefs:
        selected = st.selectbox("Pick a date", briefs, format_func=lambda p: p.stem)
    else:
        st.info("No briefs yet. Click \"Run morning brief now\".")
        selected = None

# Main panel — shared fleet load (cached) + deterministic scan (cached)
fleet = _load_fleet_cached(str(DATA_DIR))
summary = fleet_summary(fleet)
anomalies = _scan_fleet_cached(str(DATA_DIR), str(REPO_ROOT / "acknowledged.yml"))
active = [a for a in anomalies if not a.acknowledged]

# Fleet rate trend — total fleet oil (BOPD) per day over the trailing ~30 days.
trend_window = 30
daily_totals: dict[pd.Timestamp, float] = {}
for df in fleet.values():
    if not len(df):
        continue
    recent = df.iloc[-trend_window:]
    for _, row in recent.iterrows():
        bopd = row["bopd"]
        if pd.notna(bopd):
            daily_totals[row["date"]] = daily_totals.get(row["date"], 0.0) + float(bopd)
if daily_totals:
    trend = pd.Series(daily_totals).sort_index()
    fig_trend = go.Figure()
    fig_trend.add_trace(go.Scatter(
        x=trend.index, y=trend.values, mode="lines+markers",
        name="Fleet oil", line=dict(color=theme.BLUE, width=2),
        marker=dict(size=5),
    ))
    fig_trend.update_layout(title="Fleet oil rate trend (total BOPD)",
                            yaxis_title="BOPD")
    st.plotly_chart(theme.style_fig(fig_trend, height=300, legend=False),
                    width="stretch")

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
    st.metric("Wells", summary["well_count"])
    st.metric("Total BOPD", f"{summary['total_bopd']:.0f}")
    st.metric("Water cut", f"{summary['water_cut_pct']:.0f}%")
    st.metric("Avg runtime", f"{summary['avg_runtime_pct']:.1f}%")

    if anomalies:
        st.subheader("Anomalies (deterministic)")
        total_deferred = sum(a.deferred_usd_per_day for a in active)
        st.metric("Deferred production at risk", f"${total_deferred:,.0f}/day")
        sev_counts = pd.Series([a.severity for a in active]).value_counts()
        cols = st.columns(max(len(sev_counts), 1))
        for c, (sev, count) in zip(cols, sev_counts.items()):
            c.metric(sev, int(count))

        # Deferred-$ ranking bar — top offender wells by deferred $/day.
        offenders = [a for a in active if a.deferred_usd_per_day > 0][:10]
        if offenders:
            offenders = list(reversed(offenders))  # largest at top of horizontal bar
            fig_def = go.Figure()
            fig_def.add_trace(go.Bar(
                x=[a.deferred_usd_per_day for a in offenders],
                y=[a.well_id for a in offenders],
                orientation="h", marker_color=theme.RED,
            ))
            fig_def.update_layout(title="Top deferred-$ offenders ($/day)",
                                  xaxis_title="Deferred $/day")
            st.plotly_chart(theme.style_fig(fig_def, height=300, legend=False),
                            width="stretch")

        with st.expander("Drill in (ranked by deferred $)"):
            df = pd.DataFrame([
                {"Well": a.well_id, "Sev": a.severity, "Category": a.category,
                 "Deferred $/day": f"${a.deferred_usd_per_day:,.0f}" if a.deferred_usd_per_day else "—",
                 "Headline": a.headline, "Ack": "🔕" if a.acknowledged else ""}
                for a in anomalies
            ])
            st.dataframe(df, width="stretch", hide_index=True)

# --- Rolling lost-production ledger -----------------------------------------
# Monitor → Quantify: the daily scan is point-in-time; this aggregates the
# deferred barrels/$ it would have flagged each morning across a trailing window
# into a cumulative period ledger, split by cause. Same deterministic scan, same
# deferred-$ economics — just accrued over time (see src/ledger.py for the
# honest day-by-day replay approach and its limits).
st.divider()
st.subheader("📉 Lost-production ledger")
ledger, led_summary = _build_ledger_cached(
    str(DATA_DIR), str(REPO_ROOT / "acknowledged.yml"), 30)

win_start = led_summary.get("window_start")
win_end = led_summary.get("window_end")
win_label = (f"{win_start.date()} → {win_end.date()}"
             if win_start is not None and win_end is not None else "trailing window")
st.caption(
    f"Cumulative deferred production accrued over the {win_label} window "
    f"({led_summary['days_scanned']} day(s) with a scannable baseline) — the "
    "same deterministic scan + deferred-$ economics as the morning brief, summed by cause.")

lc1, lc2, lc3 = st.columns(3)
lc1.metric("Period deferred $", f"${led_summary['period_deferred_usd']:,.0f}")
lc2.metric("Recoverable $ (est.)", f"${led_summary['recoverable_usd']:,.0f}",
           help="~65% of period deferred — excludes the typically planned/reservoir-driven "
                "share. Full base-management split lives in Deferment IQ.")
lc3.metric("Top cause", str(led_summary["top_cause"] or "—"),
           delta=f"${led_summary['top_cause_usd']:,.0f}" if led_summary["top_cause"] else None,
           delta_color="off")

if not ledger.empty:
    # (a) Cumulative deferred-$ trend over the window.
    daily = ledger.groupby("date", as_index=False)["deferred_usd"].sum()
    daily["cumulative_usd"] = daily["deferred_usd"].cumsum()
    fig_cum = go.Figure()
    fig_cum.add_trace(go.Scatter(
        x=daily["date"], y=daily["cumulative_usd"], mode="lines+markers",
        name="Cumulative deferred $", fill="tozeroy",
        line=dict(color=theme.RED, width=2), marker=dict(size=5),
        fillcolor="rgba(192,80,77,0.20)",
    ))
    fig_cum.update_layout(title="Cumulative deferred production ($) over window",
                          yaxis_title="Cumulative deferred $")
    st.plotly_chart(theme.style_fig(fig_cum, height=300, legend=False),
                    width="stretch")

    # (b) Stacked-by-cause — period split of deferred $ per day.
    fig_split = go.Figure()
    for i, cause in enumerate(sorted(ledger["cause"].unique())):
        sub = ledger[ledger["cause"] == cause]
        fig_split.add_trace(go.Bar(
            x=sub["date"], y=sub["deferred_usd"], name=cause,
            marker_color=theme.COLORWAY[i % len(theme.COLORWAY)],
        ))
    fig_split.update_layout(barmode="stack", title="Deferred $ by cause (period split)",
                            yaxis_title="Deferred $/day")
    st.plotly_chart(theme.style_fig(fig_split, height=300), width="stretch")

    with st.expander("Ledger detail (tidy: date · cause · bbl · $ · cumulative)"):
        st.dataframe(ledger, width="stretch", hide_index=True)
else:
    st.info("No deferred-production events accrued in the trailing window on this fleet "
            "(the synthetic demo carries a single rate-loss event landing on the latest day). "
            "The ledger populates richly on a fleet with sustained deferrals.")

st.markdown(
    "📊 **Full base-management accounting in [Deferment IQ]"
    "(https://diazaeric1-deferment-iq.hf.space)** — potential/entitlement modeling, "
    "downtime-vs-underperformance waterfall, $-Pareto by cause, MTTR, capture-rate, and "
    "the recoverable-opportunity split (planned + reservoir excluded). This ledger is the "
    "lightweight Monitor→Quantify upstream of that weekly VP review.")

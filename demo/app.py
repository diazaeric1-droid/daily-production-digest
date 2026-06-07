"""Streamlit fleet explorer for the Daily Production Digest.

Multipage (``st.navigation`` + ``st.Page``): a Fleet Overview page (trends,
snapshot KPIs, the morning brief, the deferred-$ offender bar, the lost-production
ledger, and a sortable fleet table) plus one drill-down page per well (its own
oil/gas/water + SCADA diagnostics, a health note, and the anomaly economics).

Detection stays deterministic; the brief is BYOK-optional (a deterministic brief
renders with no API key). Heavy loads are cached on string args.
"""
from __future__ import annotations

import subprocess
import sys
from datetime import date
from functools import partial
from pathlib import Path

# Ensure repo root is on sys.path so `src.*` imports work on Streamlit Cloud, and
# the demo dir so the vendored `theme` / `fleet_registry` resolve regardless of cwd
# (Streamlit adds the entrypoint dir at runtime; AppTest / other contexts may not).
DEMO_DIR = Path(__file__).resolve().parent
REPO_ROOT = DEMO_DIR.parent
for _p in (str(REPO_ROOT), str(DEMO_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

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

import fleet_registry
import theme
from src import __version__
from src.anomaly_detector import (
    _expected_decline_rate,
    load_acknowledgements,
    scan_fleet,
)
from src.data_loader import (
    build_fleet_table,
    fleet_summary,
    load_fleet,
    production_variance_pct,
    slice_window,
)
from src.ledger import build_ledger


DATA_DIR = REPO_ROOT / "data" / "synthetic" / "fleet"
BRIEFS_DIR = REPO_ROOT / "briefs"
ACK_PATH = REPO_ROOT / "acknowledged.yml"

# Time-range control: label -> trailing-window length in days (None = Lifetime).
RANGE_OPTIONS: dict[str, int | None] = {
    "7D": 7, "30D": 30, "3mo": 90, "6mo": 180, "1Y": 365, "Lifetime": None,
}
DEFAULT_RANGE = "30D"


# ---- cached heavy loads (string args so they hash/cache cleanly) -----------

@st.cache_data(show_spinner=False)
def _load_fleet_cached(data_dir: str) -> dict:
    """Cache the expensive per-well CSV load. Returns dict[str, DataFrame]."""
    return load_fleet(data_dir)


@st.cache_resource(show_spinner=False)
def _scan_fleet_cached(data_dir: str, ack_path: str) -> list:
    """Cache the deterministic fleet scan over the latest day per well.

    Uses cache_resource (not cache_data) because it returns a list of `Anomaly`
    dataclass objects — Streamlit's cache_data serializer rejects custom classes on
    Python 3.14 / newer Streamlit. The scan result is read-only here, so sharing the
    cached object across sessions is safe."""
    fleet = _load_fleet_cached(data_dir)
    acknowledged = load_acknowledgements(ack_path)
    return scan_fleet(fleet, acknowledged=acknowledged)


@st.cache_data(show_spinner=False)
def _build_ledger_cached(data_dir: str, ack_path: str, window_days: int = 30):
    """Cache the day-by-day ledger replay over a trailing window."""
    fleet = _load_fleet_cached(data_dir)
    acknowledged = load_acknowledgements(ack_path)
    return build_ledger(fleet, window_days=window_days, acknowledged=acknowledged)


def _bootstrap_fleet() -> None:
    """Generate synthetic fleet data on first run — or regenerate if the on-disk data
    predates the current schema (a redeploy reusing an old container without the
    `gas_mcfd` channel), so the loader never trips on a stale column set."""
    existing = sorted(DATA_DIR.glob("well_*.csv"))
    stale = False
    if existing:
        try:
            stale = "gas_mcfd" not in pd.read_csv(existing[0], nrows=1).columns
        except Exception:
            stale = True
    if not existing or stale:
        with st.status("First-time setup: generating synthetic fleet…", expanded=False):
            subprocess.run(
                [sys.executable, str(REPO_ROOT / "data" / "synthetic" / "generate_fleet.py")],
                check=True,
            )


# ---- shared helpers --------------------------------------------------------

def _time_range_control(context: str) -> int | None:
    """Render the shared trailing-window segmented control and return its window
    length in days (None = Lifetime). State is namespaced per ``context`` so the
    overview and each well page remember their own selection across reruns."""
    key = f"range_{context}"
    label = st.segmented_control(
        "Time range", options=list(RANGE_OPTIONS), default=DEFAULT_RANGE,
        key=key, help="Slices the trailing window for every chart + KPI on this page.")
    if label is None:  # segmented_control allows clearing the selection
        label = DEFAULT_RANGE
    return RANGE_OPTIONS[label]


def _fleet_daily_totals(fleet: dict, window_days: int | None) -> pd.DataFrame:
    """Per-day fleet totals (oil / gas / water) over the trailing window, indexed
    by date. Water = Σ(bfpd − bopd)."""
    oil: dict = {}
    gas: dict = {}
    water: dict = {}
    for df in fleet.values():
        if not len(df):
            continue
        win = slice_window(df, window_days)
        for _, row in win.iterrows():
            d = row["date"]
            if pd.notna(row.get("bopd")):
                oil[d] = oil.get(d, 0.0) + float(row["bopd"])
                if pd.notna(row.get("bfpd")):
                    water[d] = water.get(d, 0.0) + float(row["bfpd"]) - float(row["bopd"])
            if pd.notna(row.get("gas_mcfd")):
                gas[d] = gas.get(d, 0.0) + float(row["gas_mcfd"])
    idx = sorted(set(oil) | set(gas) | set(water))
    return pd.DataFrame({
        "date": idx,
        "oil": [oil.get(d, float("nan")) for d in idx],
        "gas": [gas.get(d, float("nan")) for d in idx],
        "water": [water.get(d, float("nan")) for d in idx],
    })


def _line(x, y, name, color, y_title, title):
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x, y=y, mode="lines", name=name,
                             line=dict(color=color, width=2)))
    fig.update_layout(title=title, yaxis_title=y_title)
    return theme.style_fig(fig, height=300, legend=False)


def _anomaly_for(well_id: str, anomalies: list):
    for a in anomalies:
        if a.well_id == well_id:
            return a
    return None


def _back_to_overview():
    # Link to the registered overview page object when available (set during
    # navigation wiring); fall back to the entrypoint path otherwise.
    target = globals().get("overview")
    try:
        st.page_link(target if target is not None else "app.py",
                     label="← Back to Fleet overview", icon="📊")
    except Exception:
        pass


# =====================================================================
# PAGE: Fleet overview
# =====================================================================

def render_overview() -> None:
    theme.header(
        "Daily Production Digest",
        subtitle="Scheduled AI agent that writes a morning brief for asset teams. "
                 "Built by an ex-OXY / ex-Shell Staff PE.",
        chips=[(f"v{__version__}", "ver"), ("fleet explorer", "info"),
               ("scheduled agent", "info")],
    )

    with st.expander(f"🆕 What's new in v{__version__}"):
        st.markdown(
            "- **Fleet explorer (multipage)** — a Fleet Overview plus a **drill-down page "
            "per well** (`st.navigation`), each with its own production + SCADA-diagnostic "
            "charts and a health note.\n"
            "- **Gas channel** — every well now carries **`gas_mcfd`** (GOR-correlated to oil) "
            "and **~400 days** of history, so the **time-range toggle** (7D · 30D · 3mo · 6mo · "
            "1Y · Lifetime) is meaningful.\n"
            "- **Oil / Gas / Water fleet trends** + a **production-variance** KPI (recent-7d vs "
            "first-7d of the window) shown as oil/gas/water metric deltas.\n"
            "- **Sortable fleet table** — one row per well with lift, lateral, basin·formation, "
            "rates, water cut, GOR, variance, runtime, and the anomaly flag.\n"
            "- Brief + lost-production ledger retained; brief controls moved into the page body."
        )

    fleet = _load_fleet_cached(str(DATA_DIR))
    anomalies = _scan_fleet_cached(str(DATA_DIR), str(ACK_PATH))
    active = [a for a in anomalies if not a.acknowledged]
    anomaly_map = {a.well_id: f"{a.severity} · {a.category}" for a in active}

    window_days = _time_range_control("overview")
    totals = _fleet_daily_totals(fleet, window_days)

    # --- three fleet trend charts (Oil | Gas | Water) -----------------------
    st.subheader("Fleet production trend")
    t_oil, t_gas, t_water = st.tabs(["Oil (BOPD)", "Gas (MCFD)", "Water (BWPD)"])
    with t_oil:
        st.plotly_chart(_line(totals["date"], totals["oil"], "Oil", theme.BLUE,
                              "Total BOPD", "Total fleet oil rate (BOPD)"), width="stretch")
    with t_gas:
        st.plotly_chart(_line(totals["date"], totals["gas"], "Gas", theme.AMBER,
                              "Total MCFD", "Total fleet gas rate (MCFD)"), width="stretch")
    with t_water:
        st.plotly_chart(_line(totals["date"], totals["water"], "Water", theme.TEAL,
                              "Total BWPD", "Total fleet water rate (BWPD)"), width="stretch")

    # --- fleet snapshot KPIs incl. production variance ----------------------
    summary = fleet_summary(fleet)
    var_oil = production_variance_pct(totals["oil"].values)
    var_gas = production_variance_pct(totals["gas"].values)
    var_water = production_variance_pct(totals["water"].values)

    st.subheader("Fleet snapshot")
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Wells", summary["well_count"])
    k2.metric("Total BOPD", f"{summary['total_bopd']:,.0f}",
              delta=f"{var_oil:+.1f}% over window")
    k3.metric("Total MCFD", f"{summary['total_gas_mcfd']:,.0f}",
              delta=f"{var_gas:+.1f}% over window")
    bwpd = summary["total_bfpd"] - summary["total_bopd"]
    k4.metric("Total BWPD", f"{bwpd:,.0f}", delta=f"{var_water:+.1f}% over window",
              delta_color="inverse")
    k5, k6, k7 = st.columns(3)
    k5.metric("Water cut", f"{summary['water_cut_pct']:.0f}%")
    k6.metric("Avg runtime", f"{summary['avg_runtime_pct']:.1f}%")
    total_deferred = sum(a.deferred_usd_per_day for a in active)
    k7.metric("Deferred at risk", f"${total_deferred:,.0f}/day")

    # --- brief + offenders + fleet table ------------------------------------
    BRIEFS_DIR.mkdir(exist_ok=True)
    tab_brief, tab_anom, tab_table = st.tabs(
        ["📝 Morning brief", "🚨 Anomalies", "📋 Fleet table"])

    with tab_brief:
        _brief_panel(fleet, anomalies)

    with tab_anom:
        _anomaly_panel(anomalies, active)

    with tab_table:
        st.caption("One row per well over the selected window — sort any column. "
                   "Open a well from the **Wells** section in the sidebar to drill in.")
        table = build_fleet_table(fleet, window_days=window_days,
                                  anomaly_by_well=anomaly_map)
        st.dataframe(table, width="stretch", hide_index=True)

    # --- lost-production ledger ---------------------------------------------
    _ledger_section()


def _brief_panel(fleet: dict, anomalies: list) -> None:
    """Brief controls (BYOK + generate) + brief-history selector, in the body."""
    summary = fleet_summary(fleet)
    c1, c2 = st.columns([1, 1])
    with c1:
        byok_key = st.text_input(
            "🔑 Anthropic API key (optional)", type="password",
            help="Bring your own key — used only for this session, never stored. Powers the "
                 "Senior-PE narrated brief. Without it, a deterministic brief is rendered.")
        if st.button("Run morning brief now", type="primary"):
            with st.spinner("Scanning fleet + writing brief…"):
                from src.brief_writer import (
                    MissingAPIKey, render_brief_markdown, write_brief,
                )
                client = None
                if byok_key:
                    from anthropic import Anthropic
                    client = Anthropic(api_key=byok_key)
                try:
                    brief_md = write_brief(summary, anomalies, client=client)
                except MissingAPIKey:
                    brief_md = render_brief_markdown(summary, anomalies)
                    st.info("No API key entered — generated a deterministic brief.")
                today = date.today().isoformat()
                (BRIEFS_DIR / f"{today}.md").write_text(brief_md)
            st.success("Brief generated.")
            st.rerun()
    with c2:
        briefs = sorted(BRIEFS_DIR.glob("*.md"), reverse=True)
        if briefs:
            selected = st.selectbox("Brief history", briefs,
                                    format_func=lambda p: p.stem)
        else:
            selected = None

    st.divider()
    if selected:
        st.markdown(selected.read_text())
    elif (BRIEFS_DIR / "sample.md").exists():
        st.info("Showing committed sample brief. Click \"Run morning brief now\" for a fresh one.")
        st.markdown((BRIEFS_DIR / "sample.md").read_text())
    else:
        st.info("No briefs yet. Click \"Run morning brief now\".")


def _anomaly_panel(anomalies: list, active: list) -> None:
    if not anomalies:
        st.success("No anomalies on the latest scan.")
        return
    sev_counts = pd.Series([a.severity for a in active]).value_counts()
    cols = st.columns(max(len(sev_counts), 1))
    for c, (sev, count) in zip(cols, sev_counts.items()):
        c.metric(sev, int(count))

    offenders = [a for a in active if a.deferred_usd_per_day > 0][:10]
    if offenders:
        offenders = list(reversed(offenders))  # largest at top
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

    df = pd.DataFrame([
        {"Well": a.well_id, "Sev": a.severity, "Category": a.category,
         "Deferred $/day": f"${a.deferred_usd_per_day:,.0f}" if a.deferred_usd_per_day else "—",
         "Headline": a.headline, "Ack": "🔕" if a.acknowledged else ""}
        for a in anomalies
    ])
    st.dataframe(df, width="stretch", hide_index=True)


def _ledger_section() -> None:
    st.divider()
    st.subheader("📉 Lost-production ledger")
    ledger, led_summary = _build_ledger_cached(str(DATA_DIR), str(ACK_PATH), 30)

    win_start = led_summary.get("window_start")
    win_end = led_summary.get("window_end")
    win_label = (f"{win_start.date()} → {win_end.date()}"
                 if win_start is not None and win_end is not None else "trailing window")
    st.caption(
        f"Cumulative deferred production accrued over the {win_label} window "
        f"({led_summary['days_scanned']} day(s) with a scannable baseline) — the same "
        "deterministic scan + deferred-$ economics as the morning brief, summed by cause.")

    lc1, lc2, lc3 = st.columns(3)
    lc1.metric("Period deferred $", f"${led_summary['period_deferred_usd']:,.0f}")
    lc2.metric("Recoverable $ (est.)", f"${led_summary['recoverable_usd']:,.0f}",
               help="~65% of period deferred — excludes the typically planned/reservoir-driven "
                    "share. Full base-management split lives in Deferment IQ.")
    lc3.metric("Top cause", str(led_summary["top_cause"] or "—"),
               delta=f"${led_summary['top_cause_usd']:,.0f}" if led_summary["top_cause"] else None,
               delta_color="off")

    if not ledger.empty:
        daily = ledger.groupby("date", as_index=False)["deferred_usd"].sum()
        daily["cumulative_usd"] = daily["deferred_usd"].cumsum()
        fig_cum = go.Figure()
        fig_cum.add_trace(go.Scatter(
            x=daily["date"], y=daily["cumulative_usd"], mode="lines+markers",
            name="Cumulative deferred $", fill="tozeroy",
            line=dict(color=theme.RED, width=2), marker=dict(size=5),
            fillcolor="rgba(192,80,77,0.20)"))
        fig_cum.update_layout(title="Cumulative deferred production ($) over window",
                              yaxis_title="Cumulative deferred $")
        st.plotly_chart(theme.style_fig(fig_cum, height=300, legend=False), width="stretch")

        fig_split = go.Figure()
        for i, cause in enumerate(sorted(ledger["cause"].unique())):
            sub = ledger[ledger["cause"] == cause]
            fig_split.add_trace(go.Bar(
                x=sub["date"], y=sub["deferred_usd"], name=cause,
                marker_color=theme.COLORWAY[i % len(theme.COLORWAY)]))
        fig_split.update_layout(barmode="stack", title="Deferred $ by cause (period split)",
                                yaxis_title="Deferred $/day")
        st.plotly_chart(theme.style_fig(fig_split, height=300), width="stretch")

        with st.expander("Ledger detail (tidy: date · cause · bbl · $ · cumulative)"):
            st.dataframe(ledger, width="stretch", hide_index=True)
    else:
        st.info("No deferred-production events accrued in the trailing window on this fleet.")

    st.markdown(
        "📊 **Full base-management accounting in [Deferment IQ]"
        "(https://deferment-iq.streamlit.app)** — potential/entitlement modeling, "
        "downtime-vs-underperformance waterfall, $-Pareto by cause, MTTR, capture-rate, and "
        "the recoverable-opportunity split. This ledger is the lightweight Monitor→Quantify "
        "upstream of that weekly VP review.")


# =====================================================================
# PAGE: per-well drill-down
# =====================================================================

def render_well(well_id: str) -> None:
    fleet = _load_fleet_cached(str(DATA_DIR))
    anomalies = _scan_fleet_cached(str(DATA_DIR), str(ACK_PATH))
    meta = fleet_registry.get(well_id)
    df = fleet.get(well_id)

    theme.header(
        f"{well_id} · {meta.name}",
        subtitle=f"{meta.lift} · {meta.basin} · {meta.formation} · {meta.area}",
        chips=[(f"v{__version__}", "ver"), (meta.peer_group, "info")],
    )
    _back_to_overview()

    if df is None or not len(df):
        st.warning("No SCADA history for this well.")
        return

    window_days = _time_range_control(well_id)
    win = slice_window(df, window_days)
    last = win.iloc[-1]

    bopd = float(last["bopd"]) if pd.notna(last["bopd"]) else float("nan")
    bfpd = float(last["bfpd"]) if pd.notna(last["bfpd"]) else float("nan")
    gas = float(last["gas_mcfd"]) if pd.notna(last.get("gas_mcfd")) else float("nan")
    bwpd = bfpd - bopd
    water_cut = (bwpd / bfpd * 100.0) if bfpd > 0 else float("nan")
    gor = (gas * 1000.0 / bopd) if bopd > 0 else float("nan")
    var_oil = production_variance_pct(win["bopd"].values)

    # metrics row
    m = st.columns(5)
    m[0].metric("BOPD", f"{bopd:,.0f}", delta=f"{var_oil:+.1f}%")
    m[1].metric("BWPD", f"{bwpd:,.0f}")
    m[2].metric("MCFD", f"{gas:,.0f}")
    m[3].metric("Water cut %", f"{water_cut:.1f}%")
    m[4].metric("GOR (scf/bbl)", f"{gor:,.0f}")
    m2 = st.columns(5)
    m2[0].metric("Lateral (ft)", f"{meta.lateral_length_ft:,}")
    m2[1].metric("Days on prod", f"{len(df)}")
    m2[2].metric("Intake psi", f"{float(last['intake_pressure_psi']):.0f}")
    m2[3].metric("Runtime %", f"{float(last['runtime_pct']):.1f}")
    m2[4].metric("Prod variance %", f"{var_oil:+.1f}%")

    # production graphs
    st.subheader("Production")
    p_oil, p_gas, p_water, p_wc = st.tabs(
        ["Oil (BOPD)", "Gas (MCFD)", "Water (BWPD)", "Water cut %"])
    with p_oil:
        st.plotly_chart(_line(win["date"], win["bopd"], "Oil", theme.BLUE,
                              "BOPD", "Oil rate (BOPD)"), width="stretch")
    with p_gas:
        st.plotly_chart(_line(win["date"], win["gas_mcfd"], "Gas", theme.AMBER,
                              "MCFD", "Gas rate (MCFD)"), width="stretch")
    with p_water:
        st.plotly_chart(_line(win["date"], win["bfpd"] - win["bopd"], "Water", theme.TEAL,
                              "BWPD", "Water rate (BWPD)"), width="stretch")
    with p_wc:
        wc = (win["bfpd"] - win["bopd"]) / win["bfpd"] * 100.0
        st.plotly_chart(_line(win["date"], wc, "Water cut", theme.GREY,
                              "Water cut %", "Water cut trend (%)"), width="stretch")

    # SCADA diagnostics
    st.subheader("SCADA diagnostics")
    d_int, d_temp, d_amps, d_rt = st.tabs(
        ["Intake psi", "Motor temp °F", "Motor amps", "Runtime %"])
    with d_int:
        st.plotly_chart(_line(win["date"], win["intake_pressure_psi"], "Intake",
                              theme.PURPLE, "psi", "Intake pressure (psi)"), width="stretch")
    with d_temp:
        st.plotly_chart(_line(win["date"], win["motor_temp_f"], "Temp", theme.RED,
                              "°F", "Motor temperature (°F)"), width="stretch")
    with d_amps:
        st.plotly_chart(_line(win["date"], win["motor_amps"], "Amps", theme.GREEN,
                              "A", "Motor amps (A)"), width="stretch")
    with d_rt:
        st.plotly_chart(_line(win["date"], win["runtime_pct"], "Runtime", theme.BLUE,
                              "%", "Runtime (%)"), width="stretch")

    # health note: expected-vs-actual + any anomaly
    st.subheader("Health note")
    _well_health_note(df, well_id, anomalies)
    _back_to_overview()


def _well_health_note(df: pd.DataFrame, well_id: str, anomalies: list) -> None:
    # Decline-expected rate today from history excluding today (matches the scan).
    window = df.iloc[-14:]["bopd"].values if len(df) >= 14 else df["bopd"].values
    expected = _expected_decline_rate(window[:-1], extrapolate=1) if len(window) >= 5 else None
    last = float(df.iloc[-1]["bopd"])
    if expected:
        resid = (last - expected) / expected * 100.0
        kind = "ok" if resid >= -15 else ("high" if resid < -25 else "warn")
        theme.flag(
            f"Latest BOPD {last:,.0f} vs decline-expected {expected:,.0f} "
            f"({resid:+.0f}% residual)", kind)
    else:
        st.caption("Not enough positive history to fit a decline-expected rate.")

    a = _anomaly_for(well_id, anomalies)
    if a is None:
        theme.flag("No active anomaly on the latest scan.", "ok")
        return
    kind = {"HIGH": "high", "MEDIUM": "warn"}.get(a.severity, "warn")
    suffix = " (acknowledged / planned)" if a.acknowledged else ""
    theme.flag(f"{a.severity} · {a.category}: {a.headline}{suffix}", kind)
    if a.deferred_usd_per_day:
        st.metric("Deferred production", f"{a.deferred_bopd:,.0f} bbl/day",
                  delta=f"${a.deferred_usd_per_day:,.0f}/day", delta_color="inverse")
    st.caption(f"Recommended action: {a.recommended_action}")


# =====================================================================
# Shared setup (runs every rerun) + navigation
# =====================================================================

theme.setup_page("Daily Production Digest", icon="📅")
theme.suite_nav("pe-digest")
_bootstrap_fleet()

_fleet = _load_fleet_cached(str(DATA_DIR))

overview = st.Page(render_overview, title="Fleet overview", icon="📊", default=True)
wells = [
    st.Page(partial(render_well, wid), title=wid, url_path=wid)
    for wid in sorted(_fleet)
]
st.navigation({"Fleet": [overview], "Wells": wells}).run()

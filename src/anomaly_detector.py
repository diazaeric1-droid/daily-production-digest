"""Deterministic anomaly detection rules over fleet SCADA.

Each rule returns an Anomaly dataclass with severity (HIGH/MEDIUM/LOW),
category, the well affected, and the specific evidence triggering the flag.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd


def _slope_per_step(values) -> float:
    """Least-squares slope per step over a window (robust to endpoint noise,
    unlike a 2-point first/last difference)."""
    y = np.asarray(values, dtype=float)
    if len(y) < 2:
        return 0.0
    x = np.arange(len(y))
    return float(np.polyfit(x, y, 1)[0])


def robust_z(values, x=None) -> float:
    """Robust z-score of the last point vs the rest of the window, using the
    median + MAD (median absolute deviation) instead of mean/std.

    robust_z = 0.6745 * (x - median) / MAD  (0.6745 ≈ Φ⁻¹(0.75) makes MAD a
    consistent estimator of σ for normal data). Median/MAD are unaffected by the
    very outlier we're trying to detect, so a single bad day can't inflate the
    baseline the way mean/std would.

    Returns the signed robust z of the final value relative to the *preceding*
    points. Guards MAD==0 (constant baseline) — returns 0.0 rather than dividing
    by zero — so a flat well never produces a spurious infinite z.
    """
    y = np.asarray(values, dtype=float)
    if x is not None:
        y = np.asarray(x, dtype=float)
    if len(y) < 3:
        return 0.0
    point = float(y[-1])
    baseline = y[:-1]
    med = float(np.median(baseline))
    mad = float(np.median(np.abs(baseline - med)))
    if mad <= 1e-9:
        # Degenerate (flat) baseline: fall back to std; if that's also ~0 the
        # series is constant and there is, by definition, no anomaly.
        std = float(np.std(baseline))
        if std <= 1e-9:
            return 0.0
        return (point - med) / std
    return 0.6745 * (point - med) / mad


def _expected_decline_rate(values) -> float | None:
    """Fit a simple exponential (Arps-style) decline by log-linear regression and
    return the expected value for *today* (the last index in the window).

    Production naturally declines, so a flat 7-day mean over-flags a healthy
    well. We fit log(rate) = a + b·day via np.polyfit (no scipy), then the
    decline-expected rate today is exp(a + b·t_last). Non-positive rates are
    dropped before the log; if too few positive points remain, returns None and
    the caller falls back to the flat-mean rule.
    """
    y = np.asarray(values, dtype=float)
    n = len(y)
    if n < 4:
        return None
    x = np.arange(n)
    mask = y > 0
    if mask.sum() < 3:
        return None
    xf, yf = x[mask], y[mask]
    a, b = np.polyfit(xf, np.log(yf), 1)  # slope a (per day), intercept b
    expected_today = float(np.exp(a * x[-1] + b))
    if not np.isfinite(expected_today) or expected_today <= 0:
        return None
    return expected_today


Severity = Literal["HIGH", "MEDIUM", "LOW"]


@dataclass
class Anomaly:
    well_id: str
    severity: Severity
    category: str        # e.g., "rate_drop", "intake_collapse", "amps_creep"
    headline: str        # Short human-readable summary
    evidence: dict       # The specific numbers backing the call
    recommended_action: str


# ---- detection rules --------------------------------------------------------

def detect_rate_drop(well_id: str, scada: pd.DataFrame) -> Anomaly | None:
    """Flag if last 24h BOPD is >15% below 7-day rolling average."""
    if len(scada) < 8:
        return None
    last_day = scada.iloc[-1]["bopd"]
    baseline = scada.iloc[-8:-1]["bopd"].mean()
    if baseline <= 0:
        return None
    drop_pct = (last_day - baseline) / baseline * 100
    if drop_pct < -25:
        severity = "HIGH"
        action = "Field check within 2 hours; check pump status, separator levels, and ESDV positions"
    elif drop_pct < -15:
        severity = "MEDIUM"
        action = "Review next-day; pull dyno card or ESP readings before end of day"
    else:
        return None
    # Robust z of today's rate vs this well's own recent baseline (median + MAD).
    rz = robust_z(scada.iloc[-8:]["bopd"].values)
    return Anomaly(
        well_id=well_id, severity=severity, category="rate_drop",
        headline=f"BOPD dropped {abs(drop_pct):.0f}% vs 7-day baseline ({abs(rz):.1f}σ off own baseline)",
        evidence={"last_24h_bopd": round(last_day, 1), "baseline_bopd": round(baseline, 1),
                  "drop_pct": round(drop_pct, 1), "robust_z": round(rz, 2)},
        recommended_action=action,
    )


def detect_rate_drop_decline_aware(well_id: str, scada: pd.DataFrame) -> Anomaly | None:
    """Decline-aware rate drop. A 15% drop on a well declining 1%/day is normal —
    flat-mean rules over-flag it. Fit an exponential (Arps) decline by log-linear
    regression over the window, compute the decline-EXPECTED rate today, and flag
    only when today's rate is materially below what the decline trend predicts.

    Refinement to detect_rate_drop (both stay in RULES); this one catches the
    *excess* drop after accounting for natural decline, so it suppresses false
    positives on steep-but-healthy decliners and still catches a real step-down.
    """
    if "bopd" not in scada.columns or len(scada) < 8:
        return None
    window = scada.iloc[-14:]["bopd"].values if len(scada) >= 14 else scada["bopd"].values
    last_day = float(window[-1])
    expected = _expected_decline_rate(window)
    if expected is None or expected <= 0:
        return None  # fall back to flat-mean detect_rate_drop
    resid_pct = (last_day - expected) / expected * 100
    if resid_pct >= -15:  # within decline-expected band → not an anomaly
        return None
    if resid_pct < -25:
        severity = "HIGH"
        action = "Field check within 2 hours; drop exceeds natural decline — check pump status, separator levels, ESDV positions"
    else:
        severity = "MEDIUM"
        action = "Review next-day; drop is beyond the decline trend — pull dyno card or ESP readings before end of day"
    rz = robust_z(window[-8:])
    return Anomaly(
        well_id=well_id, severity=severity, category="rate_drop_decline_aware",
        headline=f"BOPD {abs(resid_pct):.0f}% below decline-expected ({abs(rz):.1f}σ off own baseline)",
        evidence={"last_24h_bopd": round(last_day, 1),
                  "decline_expected_bopd": round(expected, 1),
                  "residual_pct": round(resid_pct, 1),
                  "robust_z": round(rz, 2)},
        recommended_action=action,
    )


def detect_intake_collapse(well_id: str, scada: pd.DataFrame) -> Anomaly | None:
    """ESP intake pressure trending toward zero — gas interference / pump-off risk."""
    if "intake_pressure_psi" not in scada.columns or len(scada) < 5:
        return None
    last5 = scada.iloc[-5:]["intake_pressure_psi"].values
    if last5[-1] >= 40:
        return None
    # Falling trend (least-squares slope, not a noisy 2-point difference)
    slope = _slope_per_step(last5)
    if slope >= 0:
        return None
    severity = "HIGH" if last5[-1] < 25 else "MEDIUM"
    return Anomaly(
        well_id=well_id, severity=severity, category="intake_collapse",
        headline=f"Intake pressure {last5[-1]:.0f} psi, declining {slope:.1f} psi/day",
        evidence={"current_intake_psi": round(float(last5[-1]), 1),
                  "5d_slope_psi_per_day": round(float(slope), 2)},
        recommended_action="VSD frequency check + gas separator inspection; if no recovery in 48h, escalate to workover queue",
    )


def detect_motor_temp_spike(well_id: str, scada: pd.DataFrame) -> Anomaly | None:
    if "motor_temp_f" not in scada.columns or len(scada) < 8:
        return None
    last_day = scada.iloc[-1]["motor_temp_f"]
    baseline = scada.iloc[-8:-1]["motor_temp_f"].mean()
    if last_day > 340:
        severity = "HIGH"
    elif last_day > baseline + 15:
        severity = "MEDIUM"
    else:
        return None
    rz = robust_z(scada.iloc[-8:]["motor_temp_f"].values)
    return Anomaly(
        well_id=well_id, severity=severity, category="motor_temp_spike",
        headline=f"Motor temp {last_day:.0f}°F (+{last_day - baseline:.0f}°F vs 7-day avg, {abs(rz):.1f}σ off own baseline)",
        evidence={"current_temp_f": round(float(last_day), 1),
                  "baseline_temp_f": round(float(baseline), 1),
                  "robust_z": round(rz, 2)},
        recommended_action="Reduce VSD frequency; if temp not falling within 4h, plan controlled shutdown",
    )


def detect_runtime_degradation(well_id: str, scada: pd.DataFrame) -> Anomaly | None:
    if "runtime_pct" not in scada.columns or len(scada) < 1:
        return None
    last_day = scada.iloc[-1]["runtime_pct"]
    if last_day >= 90:
        return None
    severity = "HIGH" if last_day < 70 else "MEDIUM"
    return Anomaly(
        well_id=well_id, severity=severity, category="runtime_degradation",
        headline=f"Runtime only {last_day:.0f}% in last 24h",
        evidence={"runtime_pct": round(float(last_day), 1)},
        recommended_action="Pull cycle log; identify trip reason (gas lock, overload, surface power)",
    )


def detect_amps_creep(well_id: str, scada: pd.DataFrame) -> Anomaly | None:
    """Slow amps creep over 7 days — early scale / mechanical wear signal."""
    if "motor_amps" not in scada.columns or len(scada) < 8:
        return None
    window = scada.iloc[-8:]["motor_amps"].values
    # Least-squares slope over the 8-day window — the old first/last difference
    # was dominated by daily noise and missed real creep (and vice-versa).
    slope_per_day = _slope_per_step(window)
    if slope_per_day < 0.3:
        return None
    return Anomaly(
        well_id=well_id, severity="MEDIUM", category="amps_creep",
        headline=f"Motor amps creeping +{slope_per_day:.2f} A/day over 8-day window",
        evidence={"current_amps": round(float(window[-1]), 1),
                  "8d_slope_amps_per_day": round(float(slope_per_day), 2)},
        recommended_action="Trend casing pressure and intake pressure; if both stable, schedule scale-treatment workover within 30 days",
    )


RULES = [detect_rate_drop, detect_rate_drop_decline_aware, detect_intake_collapse,
         detect_motor_temp_spike, detect_runtime_degradation, detect_amps_creep]


def scan_fleet(fleet: dict[str, pd.DataFrame]) -> list[Anomaly]:
    anomalies = []
    for well_id, scada in fleet.items():
        for rule in RULES:
            result = rule(well_id, scada)
            if result is not None:
                anomalies.append(result)
    # The decline-aware rate-drop rule supersedes the flat-mean rate_drop for the
    # same well: if both fire, keep only the decline-aware one (it already
    # accounts for natural decline) so the brief doesn't double-report one drop.
    decline_aware_wells = {a.well_id for a in anomalies if a.category == "rate_drop_decline_aware"}
    anomalies = [a for a in anomalies
                 if not (a.category == "rate_drop" and a.well_id in decline_aware_wells)]
    # Sort: HIGH first, then MEDIUM, then LOW; within tier, alphabetical by well_id
    severity_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    anomalies.sort(key=lambda a: (severity_order[a.severity], a.well_id))
    return anomalies

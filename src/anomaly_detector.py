"""Deterministic anomaly detection rules over fleet SCADA.

Each rule returns an Anomaly dataclass with severity (HIGH/MEDIUM/LOW),
category, the well affected, and the specific evidence triggering the flag.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd


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
    return Anomaly(
        well_id=well_id, severity=severity, category="rate_drop",
        headline=f"BOPD dropped {drop_pct:.0f}% vs 7-day baseline",
        evidence={"last_24h_bopd": round(last_day, 1), "baseline_bopd": round(baseline, 1),
                  "drop_pct": round(drop_pct, 1)},
        recommended_action=action,
    )


def detect_intake_collapse(well_id: str, scada: pd.DataFrame) -> Anomaly | None:
    """ESP intake pressure trending toward zero — gas interference / pump-off risk."""
    if "intake_pressure_psi" not in scada.columns or len(scada) < 5:
        return None
    last5 = scada.iloc[-5:]["intake_pressure_psi"].values
    if last5[-1] >= 40:
        return None
    # Falling trend
    slope = (last5[-1] - last5[0]) / 4
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
    return Anomaly(
        well_id=well_id, severity=severity, category="motor_temp_spike",
        headline=f"Motor temp {last_day:.0f}°F (+{last_day - baseline:.0f}°F vs 7-day avg)",
        evidence={"current_temp_f": round(float(last_day), 1),
                  "baseline_temp_f": round(float(baseline), 1)},
        recommended_action="Reduce VSD frequency; if temp not falling within 4h, plan controlled shutdown",
    )


def detect_runtime_degradation(well_id: str, scada: pd.DataFrame) -> Anomaly | None:
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
    last7 = scada.iloc[-8:]["motor_amps"].values
    slope_per_day = (last7[-1] - last7[0]) / 7
    if slope_per_day < 0.5:
        return None
    return Anomaly(
        well_id=well_id, severity="MEDIUM", category="amps_creep",
        headline=f"Motor amps creeping +{slope_per_day:.1f} A/day for 7 days",
        evidence={"current_amps": round(float(last7[-1]), 1),
                  "7d_slope_amps_per_day": round(float(slope_per_day), 2)},
        recommended_action="Trend casing pressure and intake pressure; if both stable, schedule scale-treatment workover within 30 days",
    )


RULES = [detect_rate_drop, detect_intake_collapse, detect_motor_temp_spike,
         detect_runtime_degradation, detect_amps_creep]


def scan_fleet(fleet: dict[str, pd.DataFrame]) -> list[Anomaly]:
    anomalies = []
    for well_id, scada in fleet.items():
        for rule in RULES:
            result = rule(well_id, scada)
            if result is not None:
                anomalies.append(result)
    # Sort: HIGH first, then MEDIUM, then LOW; within tier, alphabetical by well_id
    severity_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    anomalies.sort(key=lambda a: (severity_order[a.severity], a.well_id))
    return anomalies

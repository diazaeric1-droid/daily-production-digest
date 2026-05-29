"""Generate a synthetic 50-well fleet with 30 days of daily SCADA per well.
Deliberately seeds in a handful of anomalies so the detector + brief writer
have something interesting to surface on first run.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


OUT = Path(__file__).parent / "fleet"
OUT.mkdir(exist_ok=True)
N_WELLS = 50
N_DAYS = 30
RNG = np.random.default_rng(11)

END_DATE = pd.Timestamp("2026-05-29")
DATES = pd.date_range(end=END_DATE, periods=N_DAYS)


def healthy_well(seed: int) -> pd.DataFrame:
    """Synthetic daily SCADA for a stable well. Noise levels reflect realistic
    daily-average CoV (~3-5%), not raw point measurements."""
    rng = np.random.default_rng(seed)
    return pd.DataFrame({
        "date": DATES,
        "bopd": np.clip(rng.normal(220, 8, N_DAYS), 80, 600),                # 3.6% CoV
        "bfpd": np.clip(rng.normal(1800, 60, N_DAYS), 1200, 2800),           # 3.3% CoV
        "intake_pressure_psi": np.clip(rng.normal(120, 4, N_DAYS), 70, 200), # 3.3% CoV
        "motor_temp_f": np.clip(rng.normal(290, 2, N_DAYS), 270, 320),       # 0.7% CoV
        "motor_amps": np.clip(rng.normal(60, 0.8, N_DAYS), 50, 72),          # 1.3% CoV
        "runtime_pct": np.clip(rng.normal(99, 0.4, N_DAYS), 92, 100),
    })


# ---- inject specific anomalies in named wells so the brief has signal -------

def well_with_rate_drop(seed: int) -> pd.DataFrame:
    df = healthy_well(seed)
    df.loc[df.index[-1], "bopd"] = df["bopd"].iloc[-1] * 0.55  # 45% drop in last 24h
    return df


def well_with_intake_collapse(seed: int) -> pd.DataFrame:
    df = healthy_well(seed)
    p = df["intake_pressure_psi"].to_numpy(copy=True)
    p[-5:] = np.linspace(p[-5], 18, 5)  # collapsing to 18 psi over 5 days
    df["intake_pressure_psi"] = p
    return df


def well_with_motor_temp_spike(seed: int) -> pd.DataFrame:
    df = healthy_well(seed)
    df.loc[df.index[-1], "motor_temp_f"] = 348  # HIGH threshold = 340
    return df


def well_with_runtime_degradation(seed: int) -> pd.DataFrame:
    df = healthy_well(seed)
    df.loc[df.index[-1], "runtime_pct"] = 62  # HIGH threshold = 70
    return df


def well_with_amps_creep(seed: int) -> pd.DataFrame:
    df = healthy_well(seed)
    creep = np.linspace(0, 9, N_DAYS)  # 9 A added over 30 days
    df["motor_amps"] = df["motor_amps"] + creep
    return df


# ---- driver -----------------------------------------------------------------

SEEDED_ANOMALIES = [
    ("well_007", well_with_rate_drop),           # HIGH rate drop
    ("well_013", well_with_intake_collapse),     # HIGH intake collapse
    ("well_022", well_with_motor_temp_spike),    # HIGH motor temp
    ("well_028", well_with_runtime_degradation), # HIGH runtime
    ("well_034", well_with_amps_creep),          # MEDIUM amps creep
    ("well_041", well_with_amps_creep),          # MEDIUM amps creep
]


def main():
    seeded_names = {name for name, _ in SEEDED_ANOMALIES}
    for name, builder in SEEDED_ANOMALIES:
        idx = int(name.split("_")[1])
        df = builder(seed=idx)
        df.to_csv(OUT / f"{name}.csv", index=False)

    for i in range(1, N_WELLS + 1):
        name = f"well_{i:03d}"
        if name in seeded_names:
            continue
        healthy_well(seed=i).to_csv(OUT / f"{name}.csv", index=False)

    print(f"Wrote {N_WELLS} wells to {OUT} ({len(SEEDED_ANOMALIES)} with seeded anomalies)")


if __name__ == "__main__":
    main()

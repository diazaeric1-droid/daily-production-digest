"""Load fleet SCADA from per-well CSV files. Production deployments would replace
this with a connector to PI / Ignition / OSIsoft / SQL data historians."""
from __future__ import annotations

from pathlib import Path

import pandas as pd


SCADA_COLUMNS = ["date", "bopd", "bfpd", "intake_pressure_psi", "motor_temp_f", "motor_amps", "runtime_pct"]


def load_well(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["date"])
    missing = set(SCADA_COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(f"{path.name if isinstance(path, Path) else path}: missing columns {missing}")
    return df.sort_values("date").reset_index(drop=True)


def load_fleet(data_dir: str | Path) -> dict[str, pd.DataFrame]:
    data_dir = Path(data_dir)
    fleet = {}
    for csv in sorted(data_dir.glob("well_*.csv")):
        fleet[csv.stem] = load_well(csv)
    return fleet


def fleet_summary(fleet: dict[str, pd.DataFrame]) -> dict[str, float]:
    """Aggregate fleet-wide stats from the most recent day per well."""
    latest_rows = [df.iloc[-1] for df in fleet.values()]
    total_bopd = sum(r["bopd"] for r in latest_rows)
    total_bfpd = sum(r["bfpd"] for r in latest_rows)
    avg_runtime = sum(r["runtime_pct"] for r in latest_rows) / max(len(latest_rows), 1)
    return {
        "well_count": len(fleet),
        "total_bopd": float(total_bopd),
        "total_bfpd": float(total_bfpd),
        "avg_runtime_pct": float(avg_runtime),
        "water_cut_pct": float((total_bfpd - total_bopd) / total_bfpd * 100) if total_bfpd > 0 else 0.0,
    }

"""Tests for the upgraded synthetic fleet + the fleet-explorer data helpers.

Covers: gas_mcfd present + positive, ~400-day history, water = bfpd − bopd,
the fleet-table builder (one row per well + expected columns), and the
production-variance helper's sign convention.
"""
from pathlib import Path

import numpy as np
import pandas as pd

from src.data_loader import (
    SCADA_COLUMNS,
    build_fleet_table,
    load_fleet,
    production_variance_pct,
    slice_window,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data" / "synthetic" / "fleet"


def test_scada_columns_include_gas():
    assert "gas_mcfd" in SCADA_COLUMNS


def test_fleet_has_gas_positive_and_long_history():
    fleet = load_fleet(DATA_DIR)
    assert fleet, "fleet should load wells"
    for well_id, df in fleet.items():
        assert "gas_mcfd" in df.columns, f"{well_id} missing gas_mcfd"
        assert (df["gas_mcfd"] > 0).all(), f"{well_id} has non-positive gas"
        # ~400 days of history (allow a little slack if regenerated differently).
        assert len(df) >= 360, f"{well_id} history too short ({len(df)})"


def test_water_equals_bfpd_minus_bopd():
    fleet = load_fleet(DATA_DIR)
    df = next(iter(fleet.values()))
    water = df["bfpd"] - df["bopd"]
    assert (water > 0).all()  # water-heavy Permian fluid stream


def test_gas_correlates_with_oil_via_gor():
    """Gas should track oil (GOR is per-well, roughly constant), so MCFD and BOPD
    are strongly positively correlated within a well."""
    fleet = load_fleet(DATA_DIR)
    df = fleet["well_001"]
    corr = np.corrcoef(df["bopd"].values, df["gas_mcfd"].values)[0, 1]
    assert corr > 0.7


def test_build_fleet_table_one_row_per_well_with_columns():
    fleet = load_fleet(DATA_DIR)
    table = build_fleet_table(fleet, window_days=30)
    assert len(table) == len(fleet)  # exactly one row per well
    expected = {
        "Well", "Lift", "Lateral (ft)", "Basin·Formation", "BOPD", "BWPD",
        "MCFD", "Water cut %", "GOR (scf/bbl)", "Production variance %",
        "Days on prod", "Runtime %", "Anomaly",
    }
    assert expected.issubset(set(table.columns))
    # GOR reconciles to gas_mcfd * 1000 / bopd on the latest day.
    row = table.iloc[0]
    df = fleet[row["Well"]]
    last = df.iloc[-1]
    assert round(last["gas_mcfd"] * 1000 / last["bopd"]) == row["GOR (scf/bbl)"]


def test_build_fleet_table_anomaly_flag_passthrough():
    fleet = load_fleet(DATA_DIR)
    table = build_fleet_table(fleet, window_days=30,
                              anomaly_by_well={"well_013": "HIGH · intake_collapse"})
    flagged = table.set_index("Well").loc["well_013", "Anomaly"]
    assert "intake_collapse" in flagged
    # Unflagged wells show the placeholder.
    assert table.set_index("Well").loc["well_001", "Anomaly"] == "—"


def test_production_variance_sign_and_magnitude():
    # Rising series → positive variance: start edge avg 100 → recent edge avg 200.
    assert production_variance_pct([100, 100, 200, 200], edge_days=2) == 100.0
    # Falling series → negative variance: 200 → 100 is a 50% drop.
    assert production_variance_pct([200, 200, 100, 100], edge_days=2) == -50.0
    # Flat series → ~0.
    assert abs(production_variance_pct([150] * 10)) < 1e-9
    # Degenerate input never raises / divides by zero.
    assert production_variance_pct([]) == 0.0
    assert production_variance_pct([0, 0, 0, 0]) == 0.0


def test_slice_window_returns_trailing_rows():
    fleet = load_fleet(DATA_DIR)
    df = next(iter(fleet.values()))
    assert len(slice_window(df, 7)) == 7
    assert len(slice_window(df, None)) == len(df)        # Lifetime
    assert len(slice_window(df, 10_000)) == len(df)      # window >= history

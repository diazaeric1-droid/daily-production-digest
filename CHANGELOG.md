# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] — 2026-06-02

### Added
- **Robust anomaly detection** — per-well rolling median + MAD robust z-scores
  (`robust_z = 0.6745·(x − median)/MAD`) so each flag can report "N sigma off
  this well's own baseline" instead of a fleet-wide rule of thumb. MAD==0 is
  guarded (no div-by-zero on a flat baseline).
- **Decline-aware rate-drop flagging** — fits an exponential (Arps) decline via
  numpy log-linear regression and flags drops relative to the decline-EXPECTED
  rate today, not a flat 7-day mean, so a healthy steep decliner stops
  over-flagging. Added as a refinement alongside the original rule.
- **Least-squares trend slopes** — amps-creep and intake-collapse now use a
  least-squares slope over the window instead of a noisy 2-point first/last
  estimate, which recovers an amps-creep well the endpoint estimator missed.
- **Pluggable historian adapter protocol** (`src/sources.py`) — a `FleetSource`
  `typing.Protocol` plus a refactored CSV adapter and two more adapters:
  `CsvTimeRangeFleetSource` (date-range filtered) and a stdlib-only
  `SQLiteFleetSource`. All honor the `SCADA_COLUMNS` contract.
- **Backtest harness** (`src/backtest.py`, `python -m src.backtest`) — scores
  every detector against the generator's seeded anomalies as ground truth and
  reports precision / recall / lead-time per rule, with an optional threshold
  sweep.

### Changed
- Empty/short-frame guards in `fleet_summary` and the detectors.
- `brief_writer` honors the `MODEL` environment variable documented in
  `.env.example`.

## [0.1.0] — Initial public demo

- Deterministic anomaly detector (rate drop, intake collapse, motor temp spike,
  runtime degradation, amps creep), Claude-powered Senior-PE brief writer,
  Streamlit history viewer, and a GitHub Actions workflow for daily cloud runs.

# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] — 2026-06-03

### Added
- **Deferred-production economics**: rate-loss anomalies carry `deferred_bopd` and
  `deferred_usd_per_day`, and the brief is **ranked by money at risk**, not z-score —
  the foreman works the biggest leak first, not the alphabetically-first well.
- **Data-quality detection** (`detect_data_quality`): a blank/zero rate while the pump
  runs is flagged as a **metering dropout**, all-tags-blank as **comms loss** — instead
  of being silently swallowed (`NaN < threshold == False`) or mistaken for a real trip.
- **Acknowledge / suppress** known events via `acknowledged.yml` so a planned workover
  doesn't re-fire HIGH every morning (alarm-fatigue control); suppressed items move to a
  "Data Quality / Acknowledged" section.
- **Water-cut context** on rate drops — a rising water cut alongside the oil drop points
  at watering out (reservoir), not a pump issue.
- **No-API-key operation**: `render_brief_markdown` produces a full deterministic brief
  when `ANTHROPIC_API_KEY` is unset, so cron/CI/the demo never crash with a bare `KeyError`.
- **Honest backtest**: near-threshold **decoy wells** (sub-threshold dip, steep-but-healthy
  decliner, noisy amps, borderline intake) so precision/recall aren't a trivial 1.00 — the
  flat-mean rate rule now visibly false-positives (precision 0.50) where decline-aware does
  not (1.00). **Lead-time** is now a real metric: detection latency from fault onset +
  early-warning days before full manifestation (the `manifest_days` parameter is actually used).
- Optional **Slack notification** step in the GitHub Action (runs only if `SLACK_WEBHOOK_URL`
  is set) — the README claim is now backed by a real step.

### Fixed
- **Decline-aware rule is now authoritative**: when a decline fit is feasible it owns the
  rate-drop call, suppressing the flat-mean rule's false positive on a steep healthy decliner;
  flat-mean survives only as a fallback for series too short to fit. It also fits the trend on
  history **excluding today** (extrapolating one step) so a one-day step-down can't flatten its
  own baseline.
- **Motor-temp MEDIUM** now requires statistical significance (robust-z ≥ 3) *and* the +15°F
  rise, so a noisy well's single warm day no longer trips a flag (robust-z was decorative).
- **GitHub Action time was wrong half the year**: the comment claimed 6:30am Central for
  `30 12 UTC`, true only in winter (CST); `30 11 UTC` is 6:30am during CDT. Documented the
  fixed-UTC/no-DST behavior.
- **SQLite adapter truncated timestamps to date-only** (`%Y-%m-%d`), collapsing sub-daily
  historian readings to one key — now stores full ISO datetime; table identifier is validated.
- `robust_z` dropped its confusing dead `x=` override parameter and now ignores NaNs.
- `write_brief` raises a typed `MissingAPIKey`; version strings aligned to 0.3.0.

## [0.2.1] — 2026-06-02

- Self-heal stale Streamlit bytecode cache at startup: purge `src/` `__pycache__`
  and evict cached `src` modules so newly-added functions reload from current source
  after a redeploy. Fixes the startup ImportError cascade seen after adding new
  symbols to existing modules (the app no longer needs a manual Reboot to pick them up).

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

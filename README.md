# Daily Production Digest

> A scheduled AI agent that runs every morning, scans your fleet's overnight SCADA, flags anomalies, and writes a one-page brief in the format a Senior PE hands to the asset team's daily standup.

Built by a Staff Production Engineer (ex-OXY, ex-Shell) who used to write this brief by hand at 6am every morning.

[![Live Demo](https://img.shields.io/badge/demo-live-brightgreen)](https://daily-production-digest.streamlit.app)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-blue)](https://www.python.org/)

---

## The problem

Every asset team starts the day asking the same three questions: *what changed overnight, what needs attention right now, and where do we stand against plan?* Answering them requires a human to pull data from 3-5 systems, eyeball trends, and write a brief — typically 60-90 minutes of senior-engineer time per day, per asset.

This system collapses that to 30 seconds. Scheduled, deterministic, repeatable.

## What it does

Every morning (cron, GitHub Actions, or Streamlit "Run Now" button):

1. **Ingests** the last 24 hours of fleet SCADA (synthetic generator included; production deployments plug into PI / Ignition / OSIsoft historians)
2. **Detects anomalies** with deterministic Python rules — rate drops >15% vs. 7-day baseline, intake pressure trending toward zero, motor temp spikes, runtime degradation, amps creep
3. **Calls Claude** to write a one-page brief in Senior-PE voice — leads with top priorities, includes field summary stats, surfaces what changed, ends with action items
4. **Persists the brief** to disk + Streamlit history view so the asset team can scroll back

## Architecture

```
                       ┌──────────────────────────┐
   cron / GH Actions ─▶│   src/scheduler.py       │◀── Streamlit "Run Now"
                       └─────────────┬────────────┘
                                     ▼
   ┌──────────────────────────────────────────────────────────────┐
   │  data_loader.py  →  anomaly_detector.py  →  brief_writer.py  │
   │  (load SCADA)       (deterministic rules)    (Claude prose)  │
   └─────────────────────────────────┬────────────────────────────┘
                                     ▼
                       ┌──────────────────────────┐
                       │  briefs/YYYY-MM-DD.md    │
                       └──────────────────────────┘
```

LLM is used only for the narrative layer. Anomaly detection is deterministic Python — engineers trust the numbers, the LLM writes them up.

## Quick start

```bash
git clone https://github.com/<your-user>/daily-production-digest
cd daily-production-digest
pip install -e ".[demo]"
cp .env.example .env  # add ANTHROPIC_API_KEY

# Generate 30 days of synthetic fleet SCADA (50 wells)
python data/synthetic/generate_fleet.py

# Run the morning brief once
python -m src.scheduler

# Streamlit history viewer
streamlit run demo/app.py
```

## Scheduling

**Local (cron):**
```cron
0 6 * * * cd /path/to/daily-production-digest && /path/to/.venv/bin/python -m src.scheduler
```

**GitHub Actions (free, runs in cloud):** see `.github/workflows/morning-brief.yml`. Set `ANTHROPIC_API_KEY` as a repo secret; the workflow runs daily at 6am Central, commits the brief back to the repo, and posts a Slack notification if any wells hit HIGH priority.

**Streamlit Cloud:** the deployed demo has a "Run Now" button so anyone can see what a fresh brief looks like without waiting.

## Sample brief

See [`briefs/sample.md`](briefs/sample.md) for a complete agent-generated brief on a synthetic 50-well fleet — top priorities ranked, field summary, anomalies surfaced, action items.

## Roadmap

- [x] v0.1 — Anomaly detector + Claude brief writer + Streamlit history
- [x] v0.1 — GitHub Actions workflow for daily cloud execution
- [ ] v0.2 — Plug-in connector pattern (PI, Ignition, Quorum)
- [ ] v0.3 — Slack / email distribution with PDF attachment
- [ ] v0.4 — Multi-asset support (separate briefs per asset team)
- [ ] v0.5 — Trend comparison vs. last week (not just last 24h)

## License

MIT.

## Contact

Eric Diaz II — [LinkedIn](https://www.linkedin.com/in/eric-a-diaz2) — diaz.a.eric1@gmail.com

Available for senior AI engineering roles and consulting engagements with E&P operators.

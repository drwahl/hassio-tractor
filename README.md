# tractor-ha

Home Assistant setup for tractor hour tracking, maintenance scheduling, and implement inventory. Run once to create everything; manage it from the HA dashboard after that.

## What it sets up

- **Hour logging** — log sessions with start/end time, usage category, and active implements. Tap "Start Now" when you head out, "End & Log" when you're done.
- **Implement inventory** — track what's on the PTO and loader at any time. Add and remove implements from the dashboard.
- **Usage categories** — break down hours by activity (Snow, Mowing, Grading, etc.). Add categories from the dashboard.
- **Maintenance tracking** — per-service hour gauges, a status table showing last service / due at / hours remaining, and HA notifications when it's time to order parts or service is due.
- **Pre-winter check** — fires October 1st and alerts if any service will come due before spring based on projected winter hours.
- **Service Schedule dashboard tab** — edit every service interval, warning threshold, and part number directly in HA without touching the script.

## Requirements

- Home Assistant (any recent version)
- Python 3.9+
- `websocket-client` Python package (`pip install websocket-client`)

## Setup

**1. Clone this repo**

```bash
git clone <repo-url>
cd tractor-ha
pip install websocket-client
```

**2. Edit `setup.py`**

Fill in the connection details at the top:

```python
HA_HOST  = "homeassistant.local:8123"
HA_TOKEN = "your-long-lived-access-token-here"
```

> Create a token in HA: Profile → Long-Lived Access Tokens → Create Token

Fill in `CFG` with your tractor's details:

```python
CFG = {
    "tractor_name":    "My Tractor",
    "dashboard_slug":  "my-tractor",
    "initial_hours":   0.0,          # current hour-meter reading
    "last_service": {
        "engine_oil": 0,             # hour reading when last serviced (0 = unknown)
        ...
    },
    ...
}
```

Uncomment and edit the entries in `SERVICES` for your maintenance schedule. At least one service is required; run with `--force-empty-services` to skip service tracking entirely.

**3. Run**

```bash
python3 setup.py
```

Re-running is safe — existing helpers keep their values. Only new items are created. Use this to add new service types later.

**4. Open your dashboard**

Navigate to `http://<your-ha-host>:8123/<dashboard_slug>` (e.g. `/my-tractor`).

## Day-to-day use

| Action | How |
|--------|-----|
| Log a session | Set meter reading + category + implements, tap **Start Now**, tap **End & Log** when done |
| Log a past session | Use the manual Start/End datetime inputs and **Log Session** |
| Change what's attached | Update PTO/Loader selects before logging |
| Add an implement | Dashboard → Manage Implements → Add |
| Remove an implement | Select it in the attachment dropdown, then Dashboard → Manage Implements → Remove |
| Add a usage category | Dashboard → Log Session → New category name → Add Category |

## Changing your service schedule

After initial setup, service intervals, warning thresholds, and part numbers are stored as HA helpers. Edit them any time without re-running the script:

- **Dashboard** → Service Schedule tab
- **HA Settings** → Helpers → search `tractor svc`

To add a brand-new service type, add a row to `SERVICES` in `setup.py` and re-run.

## File overview

```
setup.py    Everything. Edit the top section, run once.
README.md   This file.
```

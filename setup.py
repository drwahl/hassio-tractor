#!/usr/bin/env python3
"""
Tractor maintenance and usage tracking for Home Assistant.

Configure the HA_HOST, HA_TOKEN, CFG, and SERVICES sections below,
then run once:  python3 setup.py

Re-running is safe — existing helpers keep their current values,
automations and the dashboard are always overwritten with the latest config.

After initial setup, all service intervals, warning thresholds, and part
numbers are editable directly in HA: Settings → Helpers (or the
"Service Schedule" tab on the dashboard).
"""

import argparse
import json
import ssl
import sys
import urllib.request
import websocket

# ════════════════════════════════════════════════════════════════════
#  CONNECTION — set your HA host and a long-lived access token
#  (Profile → Long-Lived Access Tokens → Create Token)
# ════════════════════════════════════════════════════════════════════

HA_HOST  = "hassio.local:8123"
HA_TOKEN = "your-long-lived-access-token-here"

# ════════════════════════════════════════════════════════════════════
#  CONFIGURATION
# ════════════════════════════════════════════════════════════════════

CFG = {
    # Shown in the dashboard title and notification alerts
    "tractor_name": "CT5558 Bobcat",

    # Lovelace dashboard URL path — must contain a hyphen
    "dashboard_slug": "my-tractor",

    # Prefix used in automation IDs (no spaces; underscores OK)
    "auto_prefix": "tractor",

    # Current hour-meter reading on the tractor
    "initial_hours": 0.0,

    # Hour reading when you last performed each service (0 = never / unknown)
    "last_service": {
        "transmission_hst": 0,
        "engine_oil":        0,
        "air_filter":        0,
        "fuel_filter":       0,
        "hydraulic_filter":  0,
    },

    # Usage categories (first entry is the default selection)
    "categories": ["Snow", "Mowing", "Grading", "Hauling", "Maintenance", "Other"],

    # Implement inventory — add or remove as needed
    "pto_implements":    ["Snow Blower", "Mowing Deck", "Post Hole Digger"],
    "loader_implements": ["Snow Plow", "Wood Splitter", "Bucket"],

    # Pre-winter check fires on Oct 1; warns for services due within this many hours
    "prewinter_horizon_h": 40,
}

# ════════════════════════════════════════════════════════════════════
#  SERVICE SCHEDULE — defaults written to HA on first run only.
#  After that, edit everything in HA: Settings → Helpers, or the
#  "Service Schedule" tab on your dashboard.
#
#  To add a new service type, add a row here and re-run setup.py.
#
#  Columns:
#   slug            — used in entity/automation IDs (no spaces)
#   display_name    — shown in notifications and the dashboard
#   icon            — mdi: icon name
#   interval_h      — service interval in hours
#   annual_years    — also service every this many years
#   order_warn_h    — "order parts" alert this many hours before due
#   due_warn_h      — "due soon" alert this many hours before due
#   oem_part        — OEM part number(s)
#   aftermarket     — aftermarket alternatives
#   notes           — service notes
# ════════════════════════════════════════════════════════════════════

SERVICES = [
    # Add one tuple per service interval. All entries are commented out by default.
    # Uncomment, duplicate, and edit as needed for your tractor.
    #
    # (
    #     "engine_oil",               # slug: unique ID, lowercase, underscores only
    #     "Engine Oil and Filter",    # display name shown in alerts and dashboard
    #     "mdi:oil",                  # icon (browse icons at pictogrammers.com/library/mdi)
    #     200,                        # interval_h: service every this many hours
    #     1,                          # annual_years: also service every this many years
    #     30,                         # order_warn_h: "order parts" alert this many hours before due
    #     10,                         # due_warn_h: "due soon" alert this many hours before due
    #     "OEM part number",          # oem_part
    #     "Aftermarket alternative",  # aftermarket
    #     "Any notes about this service.",  # notes
    # ),
    # (
    #     "air_filter",
    #     "Air Filter",
    #     "mdi:air-filter",
    #     200, 1, 30, 10,
    #     "OEM part number",
    #     "Aftermarket alternative",
    #     "Replace outer element first; inspect inner element each time.",
    # ),
    # (
    #     "hydraulic_filter",
    #     "Hydraulic Oil Filter",
    #     "mdi:filter",
    #     500, 2, 60, 20,
    #     "OEM part number",
    #     "Aftermarket alternative",
    #     "Change hydraulic fluid at this interval too.",
    # ),
]

# ════════════════════════════════════════════════════════════════════
#  Nothing below this line should need editing.
# ════════════════════════════════════════════════════════════════════

_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode    = ssl.CERT_NONE

_msg_id = 0
_existing_ids: set = set()


def _next_id():
    global _msg_id
    _msg_id += 1
    return _msg_id


def connect():
    ws = websocket.create_connection(
        f"wss://{HA_HOST}/api/websocket",
        sslopt={"cert_reqs": ssl.CERT_NONE},
    )
    ws.recv()
    ws.send(json.dumps({"type": "auth", "access_token": HA_TOKEN}))
    if json.loads(ws.recv()).get("type") != "auth_ok":
        raise RuntimeError("HA authentication failed — check HA_TOKEN")
    return ws


def ws_call(ws, payload):
    mid = _next_id()
    ws.send(json.dumps({"id": mid, **payload}))
    resp = json.loads(ws.recv())
    if not resp.get("success"):
        raise RuntimeError(resp.get("error", {}).get("message", str(resp)))
    return resp.get("result")


def ha_rest(path, payload=None, method="POST"):
    req = urllib.request.Request(
        f"https://{HA_HOST}{path}",
        data=json.dumps(payload).encode() if payload is not None else None,
        method=method,
        headers={"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, context=_ssl_ctx) as r:
        return r.status, json.loads(r.read().decode())


def load_existing(ws):
    global _existing_ids
    result = ws_call(ws, {"type": "config/entity_registry/list"})
    _existing_ids = {e["entity_id"] for e in result}


def try_create(ws, payload, expected_id) -> bool:
    """Create a helper. Returns True if newly created, False if already existed."""
    if expected_id in _existing_ids:
        print(f"  exists   {expected_id}")
        return False
    try:
        ws_call(ws, payload)
        _existing_ids.add(expected_id)
        print(f"  created  {expected_id}")
        return True
    except RuntimeError as e:
        msg = str(e).lower()
        if "already" in msg or "exists" in msg:
            print(f"  exists   {expected_id}")
            return False
        else:
            raise


def set_num(entity_id, value):
    ha_rest("/api/services/input_number/set_value", {"entity_id": entity_id, "value": value})


def set_txt(entity_id, value):
    ha_rest("/api/services/input_text/set_value", {"entity_id": entity_id, "value": value})


# ── entity ID helpers ─────────────────────────────────────────────────────────

def svc_last_eid(slug):    return f"input_number.tractor_last_{slug}"
def svc_pct_eid(slug):     return f"input_number.tractor_{slug}_pct_used"
def svc_cfg_num(slug, f):  return f"input_number.tractor_svc_{slug}_{f}"
def svc_cfg_txt(slug, f):  return f"input_text.tractor_svc_{slug}_{f}"
def cat_eid(cat):          return f"input_number.tractor_hours_{cat.lower().replace(' ', '_')}"
def impl_eid(slot, name):  return f"input_number.tractor_implement_{slot}_{name.lower().replace(' ', '_')}"


# ── step 1: core tracking helpers ────────────────────────────────────────────

def create_core_helpers(ws):
    print("\n── Core helpers ─────────────────────────────────────────────────")

    try_create(ws, {
        "type": "input_number/create", "name": "Tractor Hours",
        "min": 0, "max": 99999, "step": 0.1, "unit_of_measurement": "h",
        "icon": "mdi:counter", "mode": "box",
    }, "input_number.tractor_hours")

    for slug, _, icon, _, _, _, _, _, _, _ in SERVICES:
        t = slug.replace('_', ' ').title()
        try_create(ws, {
            "type": "input_number/create",
            "name": f"Tractor Last {t}",
            "min": 0, "max": 99999, "step": 0.1, "unit_of_measurement": "h",
            "icon": icon, "mode": "box",
        }, svc_last_eid(slug))

        try_create(ws, {
            "type": "input_number/create",
            "name": f"Tractor {t} Pct Used",
            "min": 0, "max": 100, "step": 0.1, "unit_of_measurement": "%",
            "icon": icon, "mode": "box",
        }, svc_pct_eid(slug))


# ── step 2: service config helpers (editable in HA UI) ───────────────────────

def create_service_config_helpers(ws):
    print("\n── Service config helpers (editable via Settings → Helpers) ─────")
    for slug, _, icon, ih, annual_yrs, order_warn, due_warn, oem, am, notes in SERVICES:
        t = slug.replace('_', ' ').title()

        num_fields = [
            ("interval",   f"Tractor Svc {t} Interval",   1, 9999, 1,   "h",  icon,           ih),
            ("warn_order", f"Tractor Svc {t} Warn Order",  0,  500, 1,   "h",  "mdi:cart",     order_warn),
            ("warn_due",   f"Tractor Svc {t} Warn Due",    0,  500, 1,   "h",  "mdi:alert",    due_warn),
            ("annual",     f"Tractor Svc {t} Annual",      1,   10, 1,  "yr",  "mdi:calendar", annual_yrs),
        ]
        txt_fields = [
            ("oem",   f"Tractor Svc {t} OEM",   oem),
            ("alt",   f"Tractor Svc {t} Alt",   am),
            ("notes", f"Tractor Svc {t} Notes", notes),
        ]

        for field, name, mn, mx, step, unit, icon_, default in num_fields:
            eid = svc_cfg_num(slug, field)
            if try_create(ws, {
                "type": "input_number/create", "name": name,
                "min": mn, "max": mx, "step": step,
                "unit_of_measurement": unit, "icon": icon_, "mode": "box",
            }, eid):
                set_num(eid, default)

        for field, name, default in txt_fields:
            eid = svc_cfg_txt(slug, field)
            if try_create(ws, {
                "type": "input_text/create", "name": name,
                "max": 255, "icon": "mdi:note-text",
            }, eid):
                set_txt(eid, default)


# ── step 3: category helpers ──────────────────────────────────────────────────

def create_category_helpers(ws):
    print("\n── Category helpers ─────────────────────────────────────────────")

    try_create(ws, {
        "type": "input_select/create", "name": "Tractor Log Category",
        "options": CFG["categories"], "icon": "mdi:tag",
    }, "input_select.tractor_log_category")

    for cat in CFG["categories"]:
        try_create(ws, {
            "type": "input_number/create",
            "name": f"Tractor Hours {cat.title()}",
            "min": 0, "max": 99999, "step": 0.1, "unit_of_measurement": "h",
            "icon": "mdi:tractor", "mode": "box",
        }, cat_eid(cat))

    try_create(ws, {
        "type": "input_text/create", "name": "Tractor New Category",
        "max": 64, "icon": "mdi:tag-plus",
    }, "input_text.tractor_new_category")

    try_create(ws, {
        "type": "input_button/create", "name": "Tractor Add Category",
        "icon": "mdi:tag-plus",
    }, "input_button.tractor_add_category")


# ── step 4: implement helpers ─────────────────────────────────────────────────

def create_implement_helpers(ws):
    print("\n── Implement helpers ────────────────────────────────────────────")

    pto_opts  = ["None"] + CFG["pto_implements"]
    load_opts = ["None"] + CFG["loader_implements"]

    try_create(ws, {
        "type": "input_select/create", "name": "Tractor PTO Attachment",
        "options": pto_opts, "icon": "mdi:cog",
    }, "input_select.tractor_pto_attachment")

    try_create(ws, {
        "type": "input_select/create", "name": "Tractor Loader Attachment",
        "options": load_opts, "icon": "mdi:forklift",
    }, "input_select.tractor_loader_attachment")

    for name in CFG["pto_implements"]:
        try_create(ws, {
            "type": "input_number/create",
            "name": f"Tractor Implement PTO {name}",
            "min": 0, "max": 99999, "step": 0.1, "unit_of_measurement": "h",
            "icon": "mdi:cog", "mode": "box",
        }, impl_eid("pto", name))

    for name in CFG["loader_implements"]:
        try_create(ws, {
            "type": "input_number/create",
            "name": f"Tractor Implement Loader {name}",
            "min": 0, "max": 99999, "step": 0.1, "unit_of_measurement": "h",
            "icon": "mdi:forklift", "mode": "box",
        }, impl_eid("loader", name))

    try_create(ws, {
        "type": "input_text/create", "name": "Tractor New Implement Name",
        "max": 64, "icon": "mdi:plus-circle",
    }, "input_text.tractor_new_implement_name")

    try_create(ws, {
        "type": "input_select/create", "name": "Tractor New Implement Type",
        "options": ["PTO", "Loader"], "icon": "mdi:format-list-bulleted-type",
    }, "input_select.tractor_new_implement_type")

    for btn_name, btn_eid in [
        ("Tractor Add Implement",           "input_button.tractor_add_implement"),
        ("Tractor Remove PTO Implement",    "input_button.tractor_remove_pto_implement"),
        ("Tractor Remove Loader Implement", "input_button.tractor_remove_loader_implement"),
    ]:
        try_create(ws, {
            "type": "input_button/create", "name": btn_name, "icon": "mdi:cog",
        }, btn_eid)


# ── step 5: session log helpers ───────────────────────────────────────────────

def create_log_helpers(ws):
    print("\n── Session log helpers ──────────────────────────────────────────")

    try_create(ws, {
        "type": "input_datetime/create", "name": "Tractor Session Start",
        "has_date": True, "has_time": True,
    }, "input_datetime.tractor_session_start")

    try_create(ws, {
        "type": "input_datetime/create", "name": "Tractor Session End",
        "has_date": True, "has_time": True,
    }, "input_datetime.tractor_session_end")

    try_create(ws, {
        "type": "input_number/create", "name": "Tractor Log Hours",
        "min": 0, "max": 99999, "step": 0.1, "unit_of_measurement": "h",
        "icon": "mdi:pencil", "mode": "box",
    }, "input_number.tractor_log_hours")

    try_create(ws, {
        "type": "input_button/create", "name": "Tractor Log Session",
        "icon": "mdi:content-save",
    }, "input_button.tractor_log_session")

    try_create(ws, {
        "type": "input_button/create", "name": "Tractor Start Now",
        "icon": "mdi:play-circle",
    }, "input_button.tractor_start_now")

    try_create(ws, {
        "type": "input_button/create", "name": "Tractor End And Log",
        "icon": "mdi:stop-circle",
    }, "input_button.tractor_end_and_log")


# ── step 6: set initial values (only for newly created entities) ──────────────

def set_initial_values(ws):
    print("\n── Setting initial values (skipped if already existed) ──────────")
    h = float(CFG["initial_hours"])

    if try_create(ws, {
        "type": "input_number/create", "name": "Tractor Hours Init Sentinel",
        "min": 0, "max": 1, "step": 1, "icon": "mdi:counter", "mode": "box",
    }, "input_number.tractor_hours_init_sentinel"):
        # Sentinel was just created → this is a fresh install, set all values
        set_num("input_number.tractor_hours", h)
        set_num("input_number.tractor_log_hours", h)
        print(f"  tractor_hours = {h}")
        for slug, _, _, ih, _, _, _, _, _, _ in SERVICES:
            last = float(CFG["last_service"].get(slug, 0))
            set_num(svc_last_eid(slug), last)
            pct = round(min(max((h - last) / ih * 100, 0), 100), 1)
            set_num(svc_pct_eid(slug), pct)
            print(f"  {slug}: last={last}h  pct={pct}%")
    else:
        print("  existing install — hour values left untouched")


# ── step 7: automations ───────────────────────────────────────────────────────

def p(s):
    return CFG["auto_prefix"] + "_" + s


def create_automations():
    print("\n── Automations ──────────────────────────────────────────────────")
    name = CFG["tractor_name"]

    # ── update service % helpers ─────────────────────────────────────────────
    set_actions = []
    for slug, _, _, ih, _, _, _, _, _, _ in SERVICES:
        last = svc_last_eid(slug)
        pct  = svc_pct_eid(slug)
        iv   = f"(states('{svc_cfg_num(slug, 'interval')}') | float({ih}))"
        expr = (
            f"{{{{ [[((states('input_number.tractor_hours') | float(0)"
            f" - states('{last}') | float(0)) / {iv} * 100)"
            f" | round(1), 0] | max, 100] | min }}}}"
        )
        set_actions.append({
            "service": "input_number.set_value",
            "data": {"entity_id": pct, "value": expr},
        })
    _auto(p("update_service_pct"), {
        "alias": f"{name} — Update service % helpers",
        "mode": "single",
        "trigger": [
            {"platform": "state",         "entity_id": "input_number.tractor_hours"},
            {"platform": "homeassistant", "event": "start"},
        ],
        "condition": [],
        "action": set_actions,
    })

    # ── log session ──────────────────────────────────────────────────────────
    _auto(p("log_session"), {
        "alias": f"{name} — Log Session",
        "mode": "single",
        "trigger": [{"platform": "state", "entity_id": "input_button.tractor_log_session"}],
        "condition": [],
        "action": [
            {"variables": {
                "new_h":       "{{ states('input_number.tractor_log_hours') | float(0) }}",
                "cur_h":       "{{ states('input_number.tractor_hours') | float(0) }}",
                "delta":       "{{ [states('input_number.tractor_log_hours') | float(0) - states('input_number.tractor_hours') | float(0), 0] | max | round(1) }}",
                "cat":         "{{ states('input_select.tractor_log_category') }}",
                "cat_slug":    "{{ states('input_select.tractor_log_category') | lower | replace(' ', '_') }}",
                "pto":         "{{ states('input_select.tractor_pto_attachment') }}",
                "pto_slug":    "{{ states('input_select.tractor_pto_attachment') | lower | replace(' ', '_') }}",
                "loader":      "{{ states('input_select.tractor_loader_attachment') }}",
                "loader_slug": "{{ states('input_select.tractor_loader_attachment') | lower | replace(' ', '_') }}",
                "t_start":     "{{ states('input_datetime.tractor_session_start') }}",
                "t_end":       "{{ states('input_datetime.tractor_session_end') }}",
            }},
            {"condition": "template", "value_template": "{{ delta > 0 }}"},
            {"service": "input_number.set_value",
             "target": {"entity_id": "input_number.tractor_hours"},
             "data": {"value": "{{ new_h }}"}},
            {"service": "input_number.set_value",
             "data": {
                 "entity_id": "input_number.tractor_hours_{{ cat_slug }}",
                 "value": "{{ (states('input_number.tractor_hours_' + cat_slug) | float(0)) + delta }}",
             }},
            {"choose": [{"conditions": [
                {"condition": "template", "value_template": "{{ pto not in ['None', '', 'unknown'] }}"}],
                "sequence": [{"service": "input_number.set_value", "data": {
                    "entity_id": "input_number.tractor_implement_pto_{{ pto_slug }}",
                    "value": "{{ (states('input_number.tractor_implement_pto_' + pto_slug) | float(0)) + delta }}",
                }}]}]},
            {"choose": [{"conditions": [
                {"condition": "template", "value_template": "{{ loader not in ['None', '', 'unknown'] }}"}],
                "sequence": [{"service": "input_number.set_value", "data": {
                    "entity_id": "input_number.tractor_implement_loader_{{ loader_slug }}",
                    "value": "{{ (states('input_number.tractor_implement_loader_' + loader_slug) | float(0)) + delta }}",
                }}]}]},
            {"service": "input_number.set_value",
             "target": {"entity_id": "input_number.tractor_log_hours"},
             "data": {"value": "{{ new_h }}"}},
            {"service": "logbook.log", "data": {
                "name": name,
                "message": (
                    "Logged {{ delta }}h · Cat: {{ cat }}"
                    "{% if pto != 'None' %} · PTO: {{ pto }}{% endif %}"
                    "{% if loader != 'None' %} · Loader: {{ loader }}{% endif %}"
                    " — {{ t_start }} → {{ t_end }} · Meter: {{ new_h }}h"
                ),
                "entity_id": "input_number.tractor_hours",
            }},
        ],
    })

    # ── start now ────────────────────────────────────────────────────────────
    _auto(p("start_now"), {
        "alias": f"{name} — Start Now",
        "mode": "single",
        "trigger": [{"platform": "state", "entity_id": "input_button.tractor_start_now"}],
        "condition": [],
        "action": [
            {"service": "input_datetime.set_datetime",
             "data": {"entity_id": "input_datetime.tractor_session_start",
                      "datetime": "{{ now().strftime('%Y-%m-%d %H:%M:%S') }}"}},
        ],
    })

    # ── end and log ───────────────────────────────────────────────────────────
    _auto(p("end_and_log"), {
        "alias": f"{name} — End and Log",
        "mode": "single",
        "trigger": [{"platform": "state", "entity_id": "input_button.tractor_end_and_log"}],
        "condition": [],
        "action": [
            {"variables": {
                "new_h":       "{{ states('input_number.tractor_log_hours') | float(0) }}",
                "cur_h":       "{{ states('input_number.tractor_hours') | float(0) }}",
                "delta":       "{{ [states('input_number.tractor_log_hours') | float(0) - states('input_number.tractor_hours') | float(0), 0] | max | round(1) }}",
                "cat":         "{{ states('input_select.tractor_log_category') }}",
                "cat_slug":    "{{ states('input_select.tractor_log_category') | lower | replace(' ', '_') }}",
                "pto":         "{{ states('input_select.tractor_pto_attachment') }}",
                "pto_slug":    "{{ states('input_select.tractor_pto_attachment') | lower | replace(' ', '_') }}",
                "loader":      "{{ states('input_select.tractor_loader_attachment') }}",
                "loader_slug": "{{ states('input_select.tractor_loader_attachment') | lower | replace(' ', '_') }}",
                "t_start":     "{{ states('input_datetime.tractor_session_start') }}",
                "t_end":       "{{ now().strftime('%Y-%m-%d %H:%M:%S') }}",
            }},
            {"condition": "template", "value_template": "{{ delta > 0 }}"},
            {"service": "input_datetime.set_datetime",
             "data": {"entity_id": "input_datetime.tractor_session_end",
                      "datetime": "{{ t_end }}"}},
            {"service": "input_number.set_value",
             "target": {"entity_id": "input_number.tractor_hours"},
             "data": {"value": "{{ new_h }}"}},
            {"service": "input_number.set_value",
             "data": {
                 "entity_id": "input_number.tractor_hours_{{ cat_slug }}",
                 "value": "{{ (states('input_number.tractor_hours_' + cat_slug) | float(0)) + delta }}",
             }},
            {"choose": [{"conditions": [
                {"condition": "template", "value_template": "{{ pto not in ['None', '', 'unknown'] }}"}],
                "sequence": [{"service": "input_number.set_value", "data": {
                    "entity_id": "input_number.tractor_implement_pto_{{ pto_slug }}",
                    "value": "{{ (states('input_number.tractor_implement_pto_' + pto_slug) | float(0)) + delta }}",
                }}]}]},
            {"choose": [{"conditions": [
                {"condition": "template", "value_template": "{{ loader not in ['None', '', 'unknown'] }}"}],
                "sequence": [{"service": "input_number.set_value", "data": {
                    "entity_id": "input_number.tractor_implement_loader_{{ loader_slug }}",
                    "value": "{{ (states('input_number.tractor_implement_loader_' + loader_slug) | float(0)) + delta }}",
                }}]}]},
            {"service": "input_number.set_value",
             "target": {"entity_id": "input_number.tractor_log_hours"},
             "data": {"value": "{{ new_h }}"}},
            {"service": "logbook.log", "data": {
                "name": name,
                "message": (
                    "Logged {{ delta }}h · Cat: {{ cat }}"
                    "{% if pto != 'None' %} · PTO: {{ pto }}{% endif %}"
                    "{% if loader != 'None' %} · Loader: {{ loader }}{% endif %}"
                    " — {{ t_start }} → {{ t_end }} · Meter: {{ new_h }}h"
                ),
                "entity_id": "input_number.tractor_hours",
            }},
        ],
    })

    # ── add category ─────────────────────────────────────────────────────────
    _auto(p("add_category"), {
        "alias": f"{name} — Add Category",
        "mode": "single",
        "trigger": [{"platform": "state", "entity_id": "input_button.tractor_add_category"}],
        "condition": [],
        "action": [
            {"variables": {
                "cat_name": "{{ states('input_text.tractor_new_category') | trim | lower }}",
                "cur_opts": "{{ state_attr('input_select.tractor_log_category', 'options') | list }}",
            }},
            {"condition": "template", "value_template": "{{ cat_name != '' and cat_name not in cur_opts }}"},
            {"service": "input_select.set_options",
             "data": {"entity_id": "input_select.tractor_log_category",
                      "options": "{{ cur_opts + [cat_name] }}"}},
            {"service": "input_select.select_option",
             "data": {"entity_id": "input_select.tractor_log_category", "option": "{{ cat_name }}"}},
            {"service": "input_text.set_value",
             "data": {"entity_id": "input_text.tractor_new_category", "value": ""}},
            {"service": "input_number.create",
             "data": {
                 "name": "Tractor Hours {{ cat_name | capitalize }}",
                 "min": 0, "max": 99999, "step": 0.1,
                 "unit_of_measurement": "h", "icon": "mdi:tractor", "mode": "box",
             }},
        ],
    })

    # ── add implement ────────────────────────────────────────────────────────
    _auto(p("add_implement"), {
        "alias": f"{name} — Add Implement",
        "mode": "single",
        "trigger": [{"platform": "state", "entity_id": "input_button.tractor_add_implement"}],
        "condition": [],
        "action": [
            {"variables": {
                "impl_name": "{{ states('input_text.tractor_new_implement_name') | trim }}",
                "impl_type": "{{ states('input_select.tractor_new_implement_type') }}",
                "sel_eid": (
                    "{{ 'input_select.tractor_pto_attachment'"
                    " if states('input_select.tractor_new_implement_type') == 'PTO'"
                    " else 'input_select.tractor_loader_attachment' }}"
                ),
                "cur_opts": (
                    "{{ state_attr('input_select.tractor_pto_attachment', 'options')"
                    " if states('input_select.tractor_new_implement_type') == 'PTO'"
                    " else state_attr('input_select.tractor_loader_attachment', 'options') }}"
                ),
            }},
            {"condition": "template", "value_template": "{{ impl_name != '' and impl_name not in cur_opts }}"},
            {"service": "input_select.set_options",
             "data": {"entity_id": "{{ sel_eid }}", "options": "{{ cur_opts + [impl_name] }}"}},
            {"service": "input_select.select_option",
             "data": {"entity_id": "{{ sel_eid }}", "option": "{{ impl_name }}"}},
            {"service": "input_text.set_value",
             "data": {"entity_id": "input_text.tractor_new_implement_name", "value": ""}},
            {"service": "input_number.create",
             "data": {
                 "name": "Tractor Implement {{ impl_type }} {{ impl_name }}",
                 "min": 0, "max": 99999, "step": 0.1,
                 "unit_of_measurement": "h",
                 "icon": "{{ 'mdi:cog' if impl_type == 'PTO' else 'mdi:forklift' }}",
                 "mode": "box",
             }},
        ],
    })

    # ── remove PTO implement ─────────────────────────────────────────────────
    _auto(p("remove_pto_implement"), {
        "alias": f"{name} — Remove PTO Implement",
        "mode": "single",
        "trigger": [{"platform": "state", "entity_id": "input_button.tractor_remove_pto_implement"}],
        "condition": [],
        "action": [
            {"variables": {
                "cur_impl": "{{ states('input_select.tractor_pto_attachment') }}",
                "cur_opts": "{{ state_attr('input_select.tractor_pto_attachment', 'options') | list }}",
            }},
            {"condition": "template", "value_template": "{{ cur_impl not in ['None', '', 'unknown'] }}"},
            {"service": "input_select.set_options",
             "data": {"entity_id": "input_select.tractor_pto_attachment",
                      "options": "{{ cur_opts | reject('equalto', cur_impl) | list }}"}},
            {"service": "input_select.select_option",
             "data": {"entity_id": "input_select.tractor_pto_attachment", "option": "None"}},
        ],
    })

    # ── remove loader implement ───────────────────────────────────────────────
    _auto(p("remove_loader_implement"), {
        "alias": f"{name} — Remove Loader Implement",
        "mode": "single",
        "trigger": [{"platform": "state", "entity_id": "input_button.tractor_remove_loader_implement"}],
        "condition": [],
        "action": [
            {"variables": {
                "cur_impl": "{{ states('input_select.tractor_loader_attachment') }}",
                "cur_opts": "{{ state_attr('input_select.tractor_loader_attachment', 'options') | list }}",
            }},
            {"condition": "template", "value_template": "{{ cur_impl not in ['None', '', 'unknown'] }}"},
            {"service": "input_select.set_options",
             "data": {"entity_id": "input_select.tractor_loader_attachment",
                      "options": "{{ cur_opts | reject('equalto', cur_impl) | list }}"}},
            {"service": "input_select.select_option",
             "data": {"entity_id": "input_select.tractor_loader_attachment", "option": "None"}},
        ],
    })

    # ── service order / due alerts ───────────────────────────────────────────
    for slug, svc_name, icon, ih, annual_yrs, order_warn, due_warn, oem, am, notes in SERVICES:
        last_eid = svc_last_eid(slug)
        iv_ref   = f"(states('{svc_cfg_num(slug, 'interval')}') | float({ih}))"
        ow_ref   = f"(states('{svc_cfg_num(slug, 'warn_order')}') | float({order_warn}))"
        dw_ref   = f"(states('{svc_cfg_num(slug, 'warn_due')}') | float({due_warn}))"
        an_ref   = f"(states('{svc_cfg_num(slug, 'annual')}') | float({annual_yrs}))"
        oem_ref  = f"states('{svc_cfg_txt(slug, 'oem')}')"
        alt_ref  = f"states('{svc_cfg_txt(slug, 'alt')}')"
        nts_ref  = f"states('{svc_cfg_txt(slug, 'notes')}')"

        _auto(f"{p(slug)}_order", {
            "alias": f"{name} — {svc_name}: order parts",
            "mode": "single",
            "trigger": [
                {"platform": "state",        "entity_id": "input_number.tractor_hours"},
                {"platform": "time_pattern", "hours": "9", "minutes": "0", "seconds": "0"},
            ],
            "condition": [{"condition": "or", "conditions": [
                {"condition": "template", "value_template": (
                    f"{{{{ (states('input_number.tractor_hours') | float(0)"
                    f" - states('{last_eid}') | float(0)) >= {iv_ref} - {ow_ref} }}}}"
                )},
                {"condition": "and", "conditions": [
                    {"condition": "template",
                     "value_template": "{{ now().month == 9 and now().day == 1 }}"},
                    {"condition": "template", "value_template": (
                        f"{{{{ (as_timestamp(now()) - as_timestamp("
                        f"states.{last_eid}.last_changed)) > {an_ref} * 11 * 30 * 86400 }}}}"
                    )},
                ]},
            ]}],
            "action": [{"service": "persistent_notification.create", "data": {
                "title":           f"🛒 {name} — Order: {svc_name}",
                "notification_id": f"{p(slug)}_order",
                "message": (
                    f"**{svc_name}** — order parts soon.\n\n"
                    f"**OEM:** {{{{ {oem_ref} }}}}\n"
                    f"**Aftermarket:** {{{{ {alt_ref} }}}}\n"
                    f"**Notes:** {{{{ {nts_ref} }}}}\n\n"
                    f"Interval: {{{{ {iv_ref} | int }}}}h or {{{{ {an_ref} | int }}}}yr(s).  "
                    f"Last: {{{{ states('{last_eid}') | float(0) | round(1) }}}}h"
                ),
            }}],
        })

        _auto(f"{p(slug)}_due", {
            "alias": f"{name} — {svc_name}: due soon",
            "mode": "single",
            "trigger": [{"platform": "state", "entity_id": "input_number.tractor_hours"}],
            "condition": [{"condition": "template", "value_template": (
                f"{{{{ (states('input_number.tractor_hours') | float(0)"
                f" - states('{last_eid}') | float(0)) >= {iv_ref} - {dw_ref} }}}}"
            )}],
            "action": [{"service": "persistent_notification.create", "data": {
                "title":           f"⚠️ {name} — SERVICE DUE: {svc_name}",
                "notification_id": f"{p(slug)}_due",
                "message": (
                    f"**{svc_name}** is due soon.\n\n"
                    f"**OEM:** {{{{ {oem_ref} }}}}\n"
                    f"**Aftermarket:** {{{{ {alt_ref} }}}}\n\n"
                    f"Current: {{{{ states('input_number.tractor_hours') | float(0) | round(1) }}}}h · "
                    f"Last: {{{{ states('{last_eid}') | float(0) | round(1) }}}}h · "
                    f"Due at: {{{{ (states('{last_eid}') | float(0) + {iv_ref}) | round(1) }}}}h"
                ),
            }}],
        })

    # ── pre-winter check ─────────────────────────────────────────────────────
    horizon  = CFG["prewinter_horizon_h"]
    any_due  = " or ".join(
        f"(states('input_number.tractor_hours') | float(0)"
        f" - states('{svc_last_eid(sl)}') | float(0)) + {horizon}"
        f" >= (states('{svc_cfg_num(sl, 'interval')}') | float({ih}))"
        for sl, _, _, ih, _, _, _, _, _, _ in SERVICES
    )
    msg_lines = (
        f"Winter is coming. Services that may be needed before spring "
        f"(based on ~{horizon}h projected winter use). "
        f"Order parts while it's still warm.\n\n"
        f"{{% set h = states('input_number.tractor_hours') | float(0) %}}\n"
    )
    for sl, svc_name, _, ih, _, _, _, _, _, _ in SERVICES:
        last   = svc_last_eid(sl)
        iv_ref = f"(states('{svc_cfg_num(sl, 'interval')}') | float({ih}))"
        msg_lines += (
            f"{{% if (h - states('{last}') | float(0)) + {horizon} >= {iv_ref} %}}\n"
            f"  • {svc_name}"
            f" (due at {{{{ (states('{last}') | float(0) + {iv_ref}) | int }}}}h)\n"
            f"    OEM: {{{{ states('{svc_cfg_txt(sl, 'oem')}') }}}}"
            f" | Alt: {{{{ states('{svc_cfg_txt(sl, 'alt')}') }}}}\n"
            f"{{% endif %}}\n"
        )
    _auto(p("prewinter_check"), {
        "alias": f"{name} — Pre-winter maintenance check",
        "mode": "single",
        "trigger": [{"platform": "time_pattern", "hours": "9", "minutes": "0", "seconds": "0"}],
        "condition": [
            {"condition": "template",
             "value_template": "{{ now().month == 10 and now().day == 1 }}"},
            {"condition": "template", "value_template": f"{{{{ {any_due} }}}}"},
        ],
        "action": [{"service": "persistent_notification.create", "data": {
            "title":           f"🚜 {name} — Pre-Winter Service Reminder",
            "notification_id": p("prewinter"),
            "message":         msg_lines,
        }}],
    })


def _auto(auto_id, config):
    try:
        status, _ = ha_rest(f"/api/config/automation/config/{auto_id}", payload=config)
        print(f"  {'ok' if status == 200 else status}       automation.{auto_id}")
    except Exception as e:
        print(f"  FAILED   automation.{auto_id} — {e}")


# ── step 8: dashboard ─────────────────────────────────────────────────────────

def build_dashboard():
    name = CFG["tractor_name"]

    # Status table — reads intervals and thresholds from helpers at display time
    status_md = (
        f"{{% set h = states('input_number.tractor_hours') | float(0) %}}\n"
        "| | Service | Last | Due at | Remaining |\n"
        "|--|---------|:----:|:------:|:---------:|\n"
    )
    for slug, svc_name, _, ih, _, order_warn, due_warn, _, _, _ in SERVICES:
        last_ref = f"states('{svc_last_eid(slug)}') | float(0)"
        iv_ref   = f"states('{svc_cfg_num(slug, 'interval')}') | float({ih})"
        ow_ref   = f"states('{svc_cfg_num(slug, 'warn_order')}') | float({order_warn})"
        dw_ref   = f"states('{svc_cfg_num(slug, 'warn_due')}') | float({due_warn})"
        rem      = f"(({iv_ref}) - (h - ({last_ref})))"
        ic       = f"('🔴' if {rem} <= ({dw_ref}) else ('🟡' if {rem} <= ({ow_ref}) else '🟢'))"
        status_md += (
            f"| {{{{ {ic} }}}} | {svc_name}"
            f" | {{{{ ({last_ref}) | int }}}}h"
            f" | {{{{ (({last_ref}) + ({iv_ref})) | int }}}}h"
            f" | {{{{ [{rem}, 0] | max | int }}}}h |\n"
        )

    # Parts and notes — read live from text helpers
    parts_md = (
        "| Service | OEM Part # | Aftermarket |\n"
        "|---------|:----------:|-------------|\n"
    )
    notes_md = ""
    for slug, svc_name, _, _, _, _, _, _, _, _ in SERVICES:
        parts_md += (
            f"| {svc_name}"
            f" | {{{{ states('{svc_cfg_txt(slug, 'oem')}') }}}}"
            f" | {{{{ states('{svc_cfg_txt(slug, 'alt')}') }}}} |\n"
        )
        notes_md += f"- **{svc_name}:** {{{{ states('{svc_cfg_txt(slug, 'notes')}') }}}}\n"

    def gauge(slug, svc_name):
        return {
            "type": "gauge", "entity": svc_pct_eid(slug), "name": svc_name,
            "min": 0, "max": 100, "needle": True,
            "severity": {"green": 0, "yellow": 70, "red": 90},
        }

    def btn(eid, label, icon):
        return {
            "type": "button", "entity": eid, "name": label, "icon": icon,
            "tap_action": {"action": "call-service", "service": "input_button.press",
                           "service_data": {"entity_id": eid}},
        }

    slugs = [(s[0], s[1]) for s in SERVICES]
    mid   = len(slugs) // 2 + len(slugs) % 2
    gauge_row_1 = {"type": "horizontal-stack",
                   "cards": [gauge(sl, n) for sl, n in slugs[:mid]]}
    gauge_row_2 = {"type": "horizontal-stack",
                   "cards": [gauge(sl, n) for sl, n in slugs[mid:]]}

    last_entities = [{"entity": "input_number.tractor_hours",
                      "name": "Meter Reading", "icon": "mdi:counter"},
                     {"type": "divider"}]
    for slug, svc_name, icon, _, _, _, _, _, _, _ in SERVICES:
        last_entities.append({
            "entity": svc_last_eid(slug),
            "name":   f"Last: {svc_name}",
            "icon":   icon,
        })

    # Service Schedule view — all config helpers, editable inline
    svc_schedule_cards = []
    for slug, svc_name, icon, _, _, _, _, _, _, _ in SERVICES:
        svc_schedule_cards.append({
            "type": "entities",
            "title": svc_name,
            "icon": icon,
            "show_header_toggle": False,
            "entities": [
                {"entity": svc_cfg_num(slug, "interval"),   "name": "Interval (h)"},
                {"entity": svc_cfg_num(slug, "warn_order"), "name": "Order parts (h before due)"},
                {"entity": svc_cfg_num(slug, "warn_due"),   "name": "Alert due (h before due)"},
                {"entity": svc_cfg_num(slug, "annual"),     "name": "Also every (years)"},
                {"entity": svc_cfg_txt(slug, "oem"),        "name": "OEM Part #"},
                {"entity": svc_cfg_txt(slug, "alt"),        "name": "Aftermarket"},
                {"entity": svc_cfg_txt(slug, "notes"),      "name": "Notes"},
            ],
        })

    return {
        "title": name,
        "views": [
            {
                "title": "Overview", "path": "overview", "icon": "mdi:tractor",
                "badges": [], "cards": [
                    {"type": "entities", "title": "Log Session",
                     "show_header_toggle": False, "entities": [
                        {"entity": "input_number.tractor_log_hours",         "name": "New Meter Reading"},
                        {"entity": "input_select.tractor_log_category",      "name": "Category"},
                        {"entity": "input_select.tractor_pto_attachment",    "name": "PTO Attachment", "icon": "mdi:cog"},
                        {"entity": "input_select.tractor_loader_attachment", "name": "Loader Attachment", "icon": "mdi:forklift"},
                        {"type": "divider"},
                        btn("input_button.tractor_start_now",   "Start Now",   "mdi:play-circle"),
                        btn("input_button.tractor_end_and_log", "End & Log",   "mdi:stop-circle"),
                        {"type": "divider"},
                        {"type": "section", "label": "Manual time entry"},
                        {"entity": "input_datetime.tractor_session_start", "name": "Start"},
                        {"entity": "input_datetime.tractor_session_end",   "name": "End"},
                        btn("input_button.tractor_log_session", "Log Session (manual)", "mdi:content-save"),
                    ]},
                    {"type": "entities", "title": f"🚜 {name}",
                     "show_header_toggle": False, "entities": last_entities},
                    gauge_row_1,
                    gauge_row_2,
                    {"type": "markdown", "title": "Maintenance Status", "content": status_md},
                    {"type": "entities", "title": "Manage Implements",
                     "show_header_toggle": False, "entities": [
                        {"type": "section", "label": "Add"},
                        {"entity": "input_text.tractor_new_implement_name",   "name": "Name"},
                        {"entity": "input_select.tractor_new_implement_type", "name": "Type (PTO / Loader)"},
                        btn("input_button.tractor_add_implement", "Add Implement", "mdi:plus-circle"),
                        {"type": "divider"},
                        {"type": "section", "label": "Remove (select implement above first)"},
                        btn("input_button.tractor_remove_pto_implement",    "Remove Selected PTO Implement",    "mdi:minus-circle"),
                        btn("input_button.tractor_remove_loader_implement", "Remove Selected Loader Implement", "mdi:minus-circle"),
                        {"type": "divider"},
                        {"entity": "input_text.tractor_new_category", "name": "New category name"},
                        btn("input_button.tractor_add_category", "Add Category", "mdi:tag-plus"),
                    ]},
                    {"type": "markdown", "title": "Parts Reference", "content": parts_md},
                    {"type": "markdown", "title": "Service Notes",   "content": notes_md},
                ],
            },
            {
                "title": "Service Schedule", "path": "service-schedule",
                "icon": "mdi:wrench-clock",
                "badges": [], "cards": svc_schedule_cards,
            },
        ],
    }


def save_dashboard(ws):
    print("\n── Dashboard ────────────────────────────────────────────────────")
    slug = CFG["dashboard_slug"]
    try:
        ws_call(ws, {"type": "lovelace/dashboards/create",
                     "url_path": slug, "title": CFG["tractor_name"],
                     "icon": "mdi:tractor", "show_in_sidebar": True,
                     "require_admin": False})
        print(f"  created  dashboard /{slug}")
    except RuntimeError:
        print(f"  exists   dashboard /{slug}")

    ws_call(ws, {"type": "lovelace/config/save",
                 "url_path": slug, "config": build_dashboard()})
    print(f"  saved    /{slug}")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force-empty-services", action="store_true",
                        help="Allow running with an empty SERVICES list")
    args = parser.parse_args()

    if not SERVICES and not args.force_empty_services:
        print("ERROR: SERVICES is empty.")
        print()
        print("Service tracking (maintenance intervals, alerts, gauges) won't")
        print("be set up without at least one entry in the SERVICES list.")
        print()
        print("Add your service schedule to SERVICES in this file, then re-run.")
        print("If you genuinely want to skip service tracking for now, run with:")
        print()
        print("  python3 setup.py --force-empty-services")
        print()
        sys.exit(1)

    print(f"Connecting to {HA_HOST}...")
    ws = connect()
    load_existing(ws)

    create_core_helpers(ws)
    create_service_config_helpers(ws)
    create_category_helpers(ws)
    create_implement_helpers(ws)
    create_log_helpers(ws)

    set_initial_values(ws)
    create_automations()
    save_dashboard(ws)

    ws.close()
    print(f"\nDone — open /{CFG['dashboard_slug']} in HA.")
    print()
    print("Service intervals, thresholds, and part numbers are now stored as")
    print("HA helpers. Edit them at any time via:")
    print("  • The 'Service Schedule' tab on your dashboard")
    print("  • HA Settings → Helpers (search 'tractor svc')")
    print()
    print("To add a new service type: add a row to SERVICES and re-run setup.py.")
    print("To add categories/implements: use the dashboard buttons.")


if __name__ == "__main__":
    main()

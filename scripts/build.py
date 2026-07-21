#!/usr/bin/env python3
"""Build the Hive Office daily focus dashboard.

Fetches open and recently completed tasks from ClickUp, computes today's
batch-calendar mode, and writes a fully self-contained interactive HTML
page to docs/index.html. ClickUp data is baked in at build time; personal
state (energy, lead measures, strikethroughs, one-move override) lives in
the browser's localStorage.

No third-party dependencies. Python 3.9+.
Run from anywhere: python3 scripts/build.py
"""

import base64
import html
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
FONTS = ROOT / "assets" / "fonts"
ET = ZoneInfo("America/New_York")

# Optional: a ClickUp task whose description carries the morning check-in.
# If it was updated today and contains "Today's biggest lever: ...", that
# line becomes the suggested one move.
CHECKIN_TASK_ID = "86bathwpq"


def load_env():
    """Read a .env file at the repo root if present. Real env vars win."""
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def get_config():
    load_env()
    token = os.environ.get("CLICKUP_API_TOKEN", "")
    approvals = os.environ.get("APPROVALS_LIST_IDS", "")
    revenue = os.environ.get("REVENUE_LIST_IDS", "")
    placeholder = (not token) or token.startswith("YOUR_") or token == "changeme"
    split = lambda s: [x.strip() for x in s.split(",") if x.strip() and not x.strip().startswith("YOUR_")]
    return {
        "token": token,
        "approvals": split(approvals),
        "revenue": split(revenue),
        "placeholder": placeholder,
    }


def api_get(path, token, params=""):
    url = f"https://api.clickup.com/api/v2/{path}"
    if params:
        url += ("&" if "?" in url else "?") + params
    req = urllib.request.Request(url, headers={"Authorization": token})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def fetch_tasks(list_id, token, extra=""):
    """Return tasks for a ClickUp list, or None on failure."""
    tasks, page = [], 0
    while True:
        try:
            data = api_get(f"list/{list_id}/task", token, f"subtasks=false&page={page}&{extra}")
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, OSError) as exc:
            print(f"  warning: could not fetch list {list_id}: {exc}", file=sys.stderr)
            return None
        batch = data.get("tasks", [])
        tasks.extend(batch)
        if data.get("last_page", True) or not batch:
            return tasks
        page += 1


def fetch_all(list_ids, token, extra=""):
    """Fetch tasks across several lists. Returns (tasks, fetch_ok)."""
    if not list_ids:
        return [], False
    tasks, any_ok = [], False
    for list_id in list_ids:
        result = fetch_tasks(list_id, token, extra)
        if result is not None:
            any_ok = True
            tasks.extend(result)
    return tasks, any_ok


def due_dt(task):
    raw = task.get("due_date")
    if not raw:
        return None
    try:
        return datetime.fromtimestamp(int(raw) / 1000, tz=timezone.utc).astimezone(ET)
    except (ValueError, TypeError):
        return None


def priority_rank(task):
    """Lower is more urgent. ClickUp orderindex: 1 urgent .. 4 low."""
    pri = task.get("priority") or {}
    try:
        return int(pri.get("orderindex", 99))
    except (ValueError, TypeError):
        return 99


def fmt_due(task, today):
    dt = due_dt(task)
    if dt is None:
        return "no due date"
    day = dt.date()
    if day < today:
        return f"was due {dt.strftime('%b %-d')}"
    if day == today:
        return "due today"
    if (day - today).days == 1:
        return "due tomorrow"
    return f"due {dt.strftime('%a, %b %-d')}"


def spotlight_why(task, today):
    bits = []
    pri = (task.get("priority") or {}).get("priority")
    if pri:
        bits.append(f"{pri} priority")
    dt = due_dt(task)
    if dt:
        if dt.date() < today:
            bits.append("overdue")
        elif dt.date() == today:
            bits.append("due today")
    return ", ".join(bits) or "top of the queue"


def fetch_checkin_one(token, today):
    """Pull today's biggest lever from the morning check-in mirror task."""
    try:
        t = api_get(f"task/{CHECKIN_TASK_ID}", token, "include_markdown_description=true")
    except Exception:
        return None
    raw = t.get("date_updated")
    if not raw:
        return None
    try:
        updated = datetime.fromtimestamp(int(raw) / 1000, tz=timezone.utc).astimezone(ET).date()
    except (ValueError, TypeError):
        return None
    if updated != today:
        return None
    desc = t.get("markdown_description") or t.get("description") or ""
    m = re.search(r"Today's biggest lever:?\*{0,2}\s*([^\n]+)", desc, re.IGNORECASE)
    if not m:
        return None
    return m.group(1).replace("*", "").strip() or None


def font_face(family, filename, weight):
    path = FONTS / filename
    if not path.exists():
        return ""
    b64 = base64.b64encode(path.read_bytes()).decode()
    return (
        f"@font-face{{font-family:'{family}';font-style:normal;"
        f"font-weight:{weight};font-display:swap;"
        f"src:url(data:font/woff2;base64,{b64}) format('woff2');}}"
    )


def gather_data(cfg):
    now = datetime.now(ET)
    today = now.date()
    midnight_ms = int(datetime(today.year, today.month, today.day, tzinfo=ET).timestamp() * 1000)

    if cfg["placeholder"]:
        approvals_open, approvals_ok = [], False
        approvals_closed = []
        revenue_open, revenue_ok = [], False
        liam_one = None
    else:
        approvals_open, approvals_ok = fetch_all(cfg["approvals"], cfg["token"], "include_closed=false")
        closed_raw, _ = fetch_all(
            cfg["approvals"], cfg["token"], f"include_closed=true&date_done_gt={midnight_ms}"
        )
        approvals_closed = [
            t for t in closed_raw
            if t.get("date_closed") and int(t["date_closed"]) >= midnight_ms
        ]
        revenue_open, revenue_ok = fetch_all(cfg["revenue"], cfg["token"], "include_closed=false")
        liam_one = fetch_checkin_one(cfg["token"], today)

    far_future = datetime(2100, 1, 1, tzinfo=ET)
    approvals_open.sort(key=lambda t: (due_dt(t) or far_future, priority_rank(t)))

    def row(t, closed):
        return {
            "id": t.get("id", ""),
            "name": t.get("name", "(untitled)"),
            "due": fmt_due(t, today),
            "overdue": bool(due_dt(t) and due_dt(t).date() < today),
            "closed": closed,
        }

    open_ids = {t.get("id") for t in approvals_open}
    queue = [row(t, False) for t in approvals_open]
    queue += [row(t, True) for t in approvals_closed if t.get("id") not in open_ids]

    spot = None
    if revenue_open:
        best = sorted(revenue_open, key=lambda t: (due_dt(t) or far_future, priority_rank(t)))[0]
        spot = {"name": best.get("name", ""), "why": spotlight_why(best, today)}

    n = len(revenue_open)
    if not revenue_ok and not cfg["placeholder"]:
        pipe = {"state": "attn", "text": "Pipeline signal unavailable"}
    elif n == 0:
        pipe = {"state": "behind", "text": "Pipeline empty, nothing in motion"}
    else:
        pipe = {"state": "on", "text": f"Pipeline {n} open opportunit{'y' if n == 1 else 'ies'}"}

    return {
        "generatedAt": now.strftime("%-I:%M %p ET, %A, %B %-d"),
        "queue": queue,
        "approvalsOk": approvals_ok,
        "spotlight": spot,
        "revenueOk": revenue_ok,
        "pipe": pipe,
        "liamOne": liam_one,
        "placeholder": cfg["placeholder"],
    }


TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Daily Focus</title>
<style>
  __FONTS_CSS__
  :root {
    color-scheme: light;
    --teal: #015D75;
    --teal-dark: #01475A;
    --gold: #C9A84C;
    --ivory: #FAF6EC;
    --card: #FFFFFF;
    --ink: #1F2A2E;
    --ink-soft: #5A6B70;
    --on: #3E7C59;
    --attn: #C9A84C;
    --behind: #B4552D;
    --line: #E8E0CE;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    background: var(--ivory);
    color: var(--ink);
    font-family: "DM Sans", "Segoe UI", -apple-system, Helvetica, Arial, sans-serif;
    padding: 20px 22px 40px;
    max-width: 980px;
    margin: 0 auto;
  }
  h1, h2, .serif { font-family: "Cormorant Garamond", Georgia, "Times New Roman", serif; }

  /* ---------- Tier 1 ---------- */
  .t1 { padding: 6px 0 18px; }
  .t1-top { display: flex; justify-content: space-between; align-items: baseline; flex-wrap: wrap; gap: 8px; }
  .datemode { font-size: 15px; color: var(--ink-soft); letter-spacing: .3px; }
  .datemode b { color: var(--teal); font-weight: 600; }
  .weekline { display: flex; align-items: center; gap: 12px; margin-top: 10px; }
  .weeknum { font-size: 22px; font-weight: 600; color: var(--teal); font-family: "Cormorant Garamond", Georgia, serif; white-space: nowrap; }
  .wbar { flex: 1; height: 10px; background: #EEE7D6; border-radius: 6px; overflow: hidden; min-width: 120px; }
  .wbar > div { height: 100%; background: linear-gradient(90deg, var(--teal), #0A7D99); border-radius: 6px; transition: width .5s; }
  .one-action {
    margin-top: 18px;
    font-family: "Cormorant Garamond", Georgia, serif;
    font-size: clamp(26px, 4.5vw, 40px);
    line-height: 1.18;
    color: var(--teal-dark);
    font-weight: 600;
    cursor: pointer;
  }
  .one-action:hover { text-decoration: underline dotted var(--gold); text-underline-offset: 6px; }
  .one-label { font-size: 11px; letter-spacing: 2px; text-transform: uppercase; color: var(--gold); font-weight: 700; margin-top: 20px; }
  .one-src { font-size: 12px; color: var(--ink-soft); margin-top: 8px; }
  .t1-btns { display: flex; gap: 8px; }
  .chipbtn {
    border: 1px solid var(--line); background: var(--card); color: var(--teal);
    border-radius: 20px; padding: 5px 14px; font-size: 12px; cursor: pointer; font-family: inherit;
  }
  .chipbtn:hover { border-color: var(--gold); }

  /* ---------- Tier 2 ---------- */
  .t2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 14px; margin-top: 10px; }
  .card { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 16px 18px; }
  .card h3 { font-size: 11px; letter-spacing: 1.8px; text-transform: uppercase; color: var(--ink-soft); font-weight: 700; margin-bottom: 12px; }
  .gauge { display: flex; align-items: center; gap: 12px; }
  .gauge-dot { width: 46px; height: 46px; border-radius: 50%; flex: none; box-shadow: inset 0 0 0 4px rgba(255,255,255,.4); }
  .gauge-word { font-family: "Cormorant Garamond", Georgia, serif; font-size: 26px; font-weight: 600; }
  .gauge-sub { font-size: 12px; color: var(--ink-soft); margin-top: 2px; }
  .q-scroll { max-height: 300px; overflow-y: auto; }
  .q-item {
    display: flex; justify-content: space-between; align-items: baseline; gap: 12px;
    font-size: 13px; padding: 6px 0; border-bottom: 1px dashed var(--line); cursor: pointer;
  }
  .q-item:last-child { border-bottom: 0; }
  .q-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .q-due { font-size: 11px; color: var(--ink-soft); white-space: nowrap; }
  .q-due.late { color: var(--behind); font-weight: 600; }
  .q-item.done .q-name { text-decoration: line-through; color: var(--ink-soft); }
  .q-item.done .q-due { text-decoration: line-through; color: var(--ink-soft); font-weight: 400; }
  .q-item:hover .q-name { color: var(--teal); }
  .q-item.done:hover .q-name { color: var(--ink-soft); }
  .q-meta { font-size: 12px; color: var(--ink-soft); margin-top: 8px; }
  .energy-btns { display: flex; gap: 8px; margin-bottom: 10px; }
  .ebtn {
    flex: 1; padding: 9px 0; border-radius: 10px; border: 1px solid var(--line);
    background: var(--ivory); cursor: pointer; font-size: 13px; font-family: inherit; color: var(--ink-soft);
  }
  .ebtn.sel { background: var(--teal); color: #fff; border-color: var(--teal); font-weight: 600; }
  .mood-in {
    width: 100%; border: 1px solid var(--line); border-radius: 10px; padding: 8px 10px;
    font-family: inherit; font-size: 13px; background: var(--ivory); color: var(--ink);
  }
  .cap-read { margin-top: 10px; font-size: 13px; color: var(--teal-dark); line-height: 1.45; font-weight: 500; }

  /* ---------- Tier 3 ---------- */
  .t3 { margin-top: 26px; border-top: 2px solid var(--line); padding-top: 18px; }
  .t3 summary { cursor: pointer; font-family: "Cormorant Garamond", Georgia, serif; font-size: 20px; color: var(--teal); font-weight: 600; list-style: none; }
  .t3 summary::before { content: "\25C8  "; color: var(--gold); }
  .fuel { background: var(--teal); color: #F5EFDF; border-radius: 14px; padding: 22px 24px; margin-top: 14px; }
  .fuel-quote { font-family: "Cormorant Garamond", Georgia, serif; font-size: 22px; line-height: 1.35; font-style: italic; }
  .fuel-who { margin-top: 10px; font-size: 12px; letter-spacing: 1.5px; text-transform: uppercase; color: var(--gold); font-weight: 700; }
  .board { margin-top: 16px; }
  .board h4 { font-size: 11px; letter-spacing: 1.8px; text-transform: uppercase; color: var(--ink-soft); font-weight: 700; margin-bottom: 10px; }
  .lead { display: flex; align-items: center; gap: 10px; padding: 8px 0; border-bottom: 1px dashed var(--line); font-size: 14px; }
  .lead input[type=checkbox] { width: 18px; height: 18px; accent-color: var(--teal); cursor: pointer; }
  .lead .done { text-decoration: line-through; color: var(--ink-soft); }
  .lead-add { display: flex; gap: 8px; margin-top: 10px; }
  .lead-add input { flex: 1; border: 1px solid var(--line); border-radius: 10px; padding: 8px 10px; font-family: inherit; font-size: 13px; background: #fff; }
  .lead-add button { border: 0; background: var(--gold); color: #fff; border-radius: 10px; padding: 0 16px; cursor: pointer; font-family: inherit; font-weight: 600; }
  .lead-del { margin-left: auto; border: 0; background: none; color: #C4B79A; cursor: pointer; font-size: 15px; }

  /* ---------- Focus overlay ---------- */
  .focus-ov {
    position: fixed; inset: 0; background: var(--teal-dark); color: #F5EFDF; z-index: 50;
    display: none; flex-direction: column; align-items: center; justify-content: center; text-align: center; padding: 8vw;
  }
  .focus-ov.show { display: flex; }
  .focus-ov .fq { font-family: "Cormorant Garamond", Georgia, serif; font-size: clamp(30px, 6vw, 56px); line-height: 1.2; font-weight: 600; }
  .focus-ov .fp { margin-top: 24px; font-size: 15px; letter-spacing: 2px; text-transform: uppercase; }
  .focus-ov .fx { position: absolute; top: 20px; right: 26px; background: none; border: 1px solid rgba(255,255,255,.4); color: #fff; border-radius: 20px; padding: 6px 16px; cursor: pointer; font-family: inherit; }

  /* ---------- Settings / edit ---------- */
  .panel {
    position: fixed; inset: 0; background: rgba(31,42,46,.45); z-index: 40; display: none; align-items: center; justify-content: center;
  }
  .panel.show { display: flex; }
  .panel-box { background: #fff; border-radius: 16px; padding: 24px; width: min(420px, 92vw); }
  .panel-box h2 { color: var(--teal); font-size: 22px; margin-bottom: 14px; }
  .panel-box label { display: block; font-size: 12px; color: var(--ink-soft); margin: 12px 0 4px; letter-spacing: .5px; }
  .panel-box input, .panel-box textarea {
    width: 100%; border: 1px solid var(--line); border-radius: 10px; padding: 9px 11px; font-family: inherit; font-size: 14px;
  }
  .panel-box textarea { min-height: 70px; resize: vertical; }
  .panel-actions { display: flex; gap: 10px; margin-top: 18px; justify-content: flex-end; }
  .pbtn { border: 0; border-radius: 10px; padding: 9px 18px; cursor: pointer; font-family: inherit; font-weight: 600; }
  .pbtn.save { background: var(--teal); color: #fff; }
  .pbtn.cancel { background: var(--ivory); color: var(--ink-soft); }
  .reset-banner {
    background: var(--gold); color: #fff; border-radius: 12px; padding: 14px 18px; margin-bottom: 16px;
    font-size: 14px; font-weight: 600; display: none;
  }
  .muted { color: var(--ink-soft); font-size: 13px; }
  .stamp { margin-top: 26px; font-size: 12px; color: var(--ink-soft); opacity: .8; }
  @media (max-width: 560px) {
    body { padding: 14px 14px 32px; }
    .one-action { font-size: 26px; }
  }
</style>
</head>
<body>

<div class="reset-banner" id="resetBanner"></div>

<!-- TIER 1 -->
<section class="t1">
  <div class="t1-top">
    <div class="datemode" id="dateMode">Loading the day...</div>
    <div class="t1-btns">
      <button class="chipbtn" id="focusBtn">Focus view</button>
      <button class="chipbtn" id="setBtn">Settings</button>
    </div>
  </div>
  <div class="weekline">
    <div class="weeknum" id="weekNum">Week ...</div>
    <div class="wbar"><div id="weekBar" style="width:0%"></div></div>
  </div>
  <div class="one-label">Today's one move</div>
  <div class="one-action" id="oneAction" title="Tap to set your own">Reading the field...</div>
  <div class="one-src" id="oneSrc"></div>
</section>

<!-- TIER 2 -->
<section class="t2">
  <div class="card">
    <h3>Pace</h3>
    <div class="gauge">
      <div class="gauge-dot" id="paceDot" style="background:#D9D2BE"></div>
      <div>
        <div class="gauge-word" id="paceWord">...</div>
        <div class="gauge-sub" id="paceSub"></div>
      </div>
    </div>
  </div>
  <div class="card">
    <h3>Approval batch queue</h3>
    <div class="q-scroll" id="queueList"><span class="muted">Loading...</span></div>
    <div class="q-meta" id="queueMeta"></div>
  </div>
  <div class="card">
    <h3>Energy today</h3>
    <div class="energy-btns">
      <button class="ebtn" data-e="low">Low</button>
      <button class="ebtn" data-e="medium">Medium</button>
      <button class="ebtn" data-e="high">High</button>
    </div>
    <input class="mood-in" id="moodIn" maxlength="24" placeholder="One word mood">
    <div class="cap-read" id="capRead">Tap an energy level for today's honest capacity.</div>
  </div>
</section>

<!-- TIER 3 -->
<details class="t3" open>
  <summary>The Fuel</summary>
  <div class="fuel">
    <div class="fuel-quote" id="fuelQuote">...</div>
    <div class="fuel-who" id="fuelWho"></div>
  </div>
  <div class="board card" style="margin-top:16px">
    <h4>This week's lead measures (12-Week Year scoreboard)</h4>
    <div id="leadList"></div>
    <div class="lead-add">
      <input id="leadIn" maxlength="90" placeholder="Add a committed weekly action">
      <button id="leadAdd">Add</button>
    </div>
  </div>
</details>

<p class="stamp" id="stamp"></p>

<!-- Focus overlay -->
<div class="focus-ov" id="focusOv">
  <button class="fx" id="focusX">Close</button>
  <div class="fq" id="focusQ"></div>
  <div class="fp" id="focusP"></div>
</div>

<!-- Settings panel -->
<div class="panel" id="setPanel">
  <div class="panel-box">
    <h2 class="serif">Dashboard settings</h2>
    <label>12-Week Year start date (a Monday)</label>
    <input type="date" id="startIn">
    <label>Cycle name (optional)</label>
    <input type="text" id="cycleIn" maxlength="60" placeholder="e.g. Q3 Pivot Cycle">
    <div class="panel-actions">
      <button class="pbtn cancel" id="setCancel">Cancel</button>
      <button class="pbtn save" id="setSave">Save</button>
    </div>
  </div>
</div>

<!-- One-thing editor -->
<div class="panel" id="onePanel">
  <div class="panel-box">
    <h2 class="serif">Today's one move</h2>
    <p class="muted">From your morning check-in or your gut. Leave blank to let ClickUp decide.</p>
    <textarea id="oneIn" maxlength="200"></textarea>
    <div class="panel-actions">
      <button class="pbtn cancel" id="oneCancel">Cancel</button>
      <button class="pbtn save" id="oneSave">Save</button>
    </div>
  </div>
</div>

<script>
window.HIVE = __HIVE_DATA__;
</script>
<script>
(function () {
  "use strict";

  var H = window.HIVE || {};
  var TODAY = new Date();
  var ISO = localISO(TODAY);
  var DOW = TODAY.getDay();

  function localISO(d) {
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
  }
  function store(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }
  function load(k, dflt) {
    try { var v = localStorage.getItem(k); return v === null ? dflt : JSON.parse(v); } catch (e) { return dflt; }
  }
  function esc(s) {
    return String(s || "").replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  /* ----- Rhythm mode ----- */
  var MODES = ["Off", "Money Open", "Camera Day", "Writing Day", "People Day", "Money Close", "Off"];
  var mode = MODES[DOW];
  var friAfternoon = (DOW === 5 && TODAY.getHours() >= 12);
  var modeLabel = friAfternoon ? "Off (Money Close is done, rest now)" : mode;
  var dayName = TODAY.toLocaleDateString("en-US", { weekday: "long" });
  var niceDate = TODAY.toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric" });
  document.getElementById("dateMode").innerHTML =
    esc(niceDate) + " &nbsp;&middot;&nbsp; <b>" + esc(dayName) + " &middot; " + esc(modeLabel) + "</b>";

  /* ----- 12-Week Year ----- */
  var startDate = load("l12_start", "2026-07-01");
  var cycleName = load("l12_cycle", "");
  var week = 0, past12 = false;
  (function () {
    var s = new Date(startDate + "T00:00:00");
    var days = Math.floor((TODAY - s) / 86400000);
    if (days < 0) { week = 0; } else { week = Math.floor(days / 7) + 1; }
    if (week > 12) { past12 = true; week = 12; }
    var wn = document.getElementById("weekNum");
    wn.textContent = week === 0 ? "Cycle starts " + startDate : "Week " + week + " of 12" + (cycleName ? " · " + cycleName : "");
    document.getElementById("weekBar").style.width = Math.min(100, Math.round((week / 12) * 100)) + "%";
    if (past12) {
      var b = document.getElementById("resetBanner");
      b.style.display = "block";
      b.textContent = "This 12-Week Year is complete. Time for a review and reset: score the cycle, celebrate, and set the next 12 weeks in Settings.";
    }
  })();

  /* ----- Scoreboard (lead measures) ----- */
  var leadKey = "l12_leads_w" + week + "_" + startDate;
  var leads = load(leadKey, []);
  function renderLeads() {
    var el = document.getElementById("leadList");
    if (!leads.length) {
      el.innerHTML = '<div class="muted">No committed actions set for this week yet. Add 2 or 3 below.</div>';
    } else {
      el.innerHTML = leads.map(function (l, i) {
        return '<div class="lead"><input type="checkbox" data-i="' + i + '"' + (l.done ? " checked" : "") + ">" +
          '<span class="' + (l.done ? "done" : "") + '">' + esc(l.text) + "</span>" +
          '<button class="lead-del" data-d="' + i + '" title="Remove">✕</button></div>';
      }).join("");
    }
    el.querySelectorAll("input[type=checkbox]").forEach(function (cb) {
      cb.addEventListener("change", function () {
        leads[+cb.dataset.i].done = cb.checked; store(leadKey, leads); renderLeads(); renderPace();
      });
    });
    el.querySelectorAll(".lead-del").forEach(function (btn) {
      btn.addEventListener("click", function () {
        leads.splice(+btn.dataset.d, 1); store(leadKey, leads); renderLeads(); renderPace();
      });
    });
  }
  document.getElementById("leadAdd").addEventListener("click", addLead);
  document.getElementById("leadIn").addEventListener("keydown", function (e) { if (e.key === "Enter") addLead(); });
  function addLead() {
    var inp = document.getElementById("leadIn");
    var t = inp.value.trim();
    if (!t || leads.length >= 5) return;
    leads.push({ text: t, done: false }); inp.value = "";
    store(leadKey, leads); renderLeads(); renderPace();
  }

  /* ----- Pace gauge (scoreboard + build-time revenue signal) ----- */
  var paceState = "unset";
  var pipeSig = H.pipe || null;
  function renderPace() {
    var dot = document.getElementById("paceDot");
    var word = document.getElementById("paceWord");
    var sub = document.getElementById("paceSub");
    var states = [], parts = [];
    if (leads.length) {
      var done = leads.filter(function (l) { return l.done; }).length;
      var dayIdx = DOW === 0 ? 7 : DOW; /* Mon=1 ... Sun=7 */
      var expected = leads.length * Math.min(1, dayIdx / 5); /* full board expected by Friday */
      var sb;
      if (done >= expected) sb = done > expected ? "ahead" : "on";
      else if (expected - done <= 0.75) sb = "on";
      else sb = "behind";
      states.push(sb);
      parts.push("Scoreboard " + done + " of " + leads.length);
    }
    if (pipeSig) { states.push(pipeSig.state); parts.push(pipeSig.text); }
    if (!states.length) {
      paceState = "unset";
      dot.style.background = "#D9D2BE";
      word.textContent = "Not set";
      sub.textContent = "Add lead measures below to sharpen the gauge.";
      pickFuel(); return;
    }
    if (states.indexOf("behind") >= 0) paceState = "behind";
    else if (states.indexOf("attn") >= 0) paceState = "attn";
    else if (states.every(function (s) { return s === "ahead"; })) paceState = "ahead";
    else paceState = "on";
    var map = {
      ahead: ["var(--on)", "Ahead"],
      on: ["var(--on)", "On pace"],
      attn: ["var(--attn)", "Watch"],
      behind: ["var(--behind)", "Behind"]
    };
    var m = map[paceState];
    dot.style.background = m[0];
    word.textContent = m[1];
    sub.textContent = parts.join(" · ");
    pickFuel();
  }

  /* ----- Energy and capacity ----- */
  var eKey = "l12_energy_" + ISO;
  var eState = load(eKey, { energy: null, mood: "" });
  function renderEnergy() {
    document.querySelectorAll(".ebtn").forEach(function (b) {
      b.classList.toggle("sel", b.dataset.e === eState.energy);
    });
    document.getElementById("moodIn").value = eState.mood || "";
    var out = document.getElementById("capRead");
    if (!eState.energy) { out.textContent = "Tap an energy level for today's honest capacity."; return; }
    var base = { low: 1.5, medium: 3, high: 4.5 }[eState.energy];
    var msgs = {
      low: base + " focused hours available. Do the one move, then stop. Everything else can wait or be delegated.",
      medium: base + " focused hours available. Protect them for the one move first, then one lead measure.",
      high: base + " focused hours available. One move first, then swing at the scoreboard. Do not book new commitments today."
    };
    out.textContent = msgs[eState.energy] + (eState.mood ? " Mood: " + eState.mood + "." : "");
  }
  document.querySelectorAll(".ebtn").forEach(function (b) {
    b.addEventListener("click", function () {
      eState.energy = b.dataset.e; store(eKey, eState); renderEnergy();
    });
  });
  document.getElementById("moodIn").addEventListener("change", function (e) {
    eState.mood = e.target.value.trim(); store(eKey, eState); renderEnergy();
  });

  /* ----- Mentor fuel ----- */
  var BANK = {
    proctor: { who: "Bob Proctor", q: [
      "The gap is a paradigm problem before it is a tactics problem. What belief is running this week's behavior?",
      "You do not get what you want, you get what you believe. Check the belief under the number.",
      "Change the image on the screen of your mind and the results have no choice but to follow."
    ]},
    oprah: { who: "Oprah Winfrey", q: [
      "Listen for what is underneath what they are saying. That is where the real conversation lives.",
      "Every person you meet today is asking: do you see me, do you hear me, does what I say matter?",
      "Lead with grounded calm. Presence is the most generous thing you can bring into a room."
    ]},
    grede: { who: "Emma Grede", q: [
      "Build the system, not the moment. Infrastructure is what lets the brand breathe without you.",
      "Discipline is the luxury. Repeatable beats impressive every single week.",
      "Treat the business like infrastructure, not improvisation. What gets systemized today?"
    ]},
    clear: { who: "James Clear, Atomic Habits", q: [
      "You do not rise to your goals, you fall to your systems. Make today's system 1 percent better.",
      "Every action is a vote for the person you are becoming. Cast today's vote on purpose.",
      "Small habits do not add up, they compound. Show up for the boring rep today."
    ]},
    harrington: { who: "H. James Harrington", q: [
      "Quality is built into the process, not inspected in afterward. Is today's work a system or a patch?",
      "Measurement is the first step to control and improvement. If you cannot measure it, you cannot improve it.",
      "Design the process so it runs without you. That is the real deliverable today."
    ]},
    bench: [
      { who: "Myron Golden", q: "Do not chase money, become the person offers chase. Make the offer today, boldly." },
      { who: "Alex Hormozi", q: "Volume negates luck. Make more offers today than feels comfortable." },
      { who: "Leila Hormozi", q: "The business grows at the speed of your operations. Close the loop, do not open five new ones." },
      { who: "Rachel Rodgers", q: "Million dollar decisions are made quickly and from abundance. Price it, say it, send it." },
      { who: "Pauleanna Reid", q: "Your story is the strategy. Tell it plainly today and let it sell for you." },
      { who: "Erin OnDemand", q: "Document, do not just create. Today's work is tomorrow's proof." },
      { who: "Brandy Mabra", q: "You are the CEO, not the employee of your business. Make one CEO decision today." }
    ]
  };
  var sysWords = /clickup|automation|backend|system|infrastructure|build.*(course|shell|hub)|workflow|sop/i;
  var dailySeed = 0;
  for (var i = 0; i < ISO.length; i++) dailySeed = (dailySeed * 31 + ISO.charCodeAt(i)) % 9973;
  var queue = H.queue || [];
  function pickFuel() {
    var pick;
    var taskBlob = queue.map(function (t) { return t.name; }).join(" ");
    if (paceState === "behind") pick = bankPick("proctor");
    else if (mode === "People Day") pick = bankPick("oprah");
    else if (sysWords.test(taskBlob)) pick = bankPick("harrington");
    else if (mode === "Money Open" || mode === "Camera Day") {
      pick = BANK.bench[dailySeed % BANK.bench.length];
    }
    else if (mode === "Writing Day" || mode === "Money Close") pick = bankPick("clear");
    else pick = bankPick("clear");
    document.getElementById("fuelQuote").textContent = "“" + pick.q + "”";
    document.getElementById("fuelWho").textContent = pick.who;
  }
  function bankPick(key) {
    var b = BANK[key];
    return { who: b.who, q: b.q[dailySeed % b.q.length] };
  }

  /* ----- One move ----- */
  var oneKey = "l12_one_" + ISO;
  var manualOne = load(oneKey, "");
  var autoOne = H.spotlight || null;
  var liamOne = H.liamOne || null;
  function renderOne() {
    var el = document.getElementById("oneAction");
    var src = document.getElementById("oneSrc");
    if (manualOne) {
      el.textContent = manualOne;
      src.textContent = "Set by you. Tap to change.";
    } else if (liamOne) {
      el.textContent = liamOne;
      src.textContent = "From this morning's check-in. Tap to override.";
    } else if (autoOne) {
      el.textContent = autoOne.name;
      src.textContent = "Auto-picked from ClickUp (" + autoOne.why + "). Tap to override.";
    } else {
      el.textContent = mode === "Off" ? "Rest. That is the assignment." : "Nothing is flagged in the pipeline. Pick one lead measure and move it.";
      src.textContent = "Tap to set today's one move.";
    }
    document.getElementById("focusQ").textContent = el.textContent;
    document.getElementById("focusP").textContent = dayName + " · " + modeLabel + " · Week " + week + " of 12";
  }
  document.getElementById("oneAction").addEventListener("click", function () {
    document.getElementById("oneIn").value = manualOne || "";
    document.getElementById("onePanel").classList.add("show");
  });
  document.getElementById("oneCancel").addEventListener("click", function () {
    document.getElementById("onePanel").classList.remove("show");
  });
  document.getElementById("oneSave").addEventListener("click", function () {
    manualOne = document.getElementById("oneIn").value.trim();
    store(oneKey, manualOne);
    document.getElementById("onePanel").classList.remove("show");
    renderOne();
  });

  /* ----- Focus overlay ----- */
  document.getElementById("focusBtn").addEventListener("click", function () {
    document.getElementById("focusOv").classList.add("show");
  });
  document.getElementById("focusX").addEventListener("click", function () {
    document.getElementById("focusOv").classList.remove("show");
  });

  /* ----- Settings ----- */
  document.getElementById("setBtn").addEventListener("click", function () {
    document.getElementById("startIn").value = startDate;
    document.getElementById("cycleIn").value = cycleName;
    document.getElementById("setPanel").classList.add("show");
  });
  document.getElementById("setCancel").addEventListener("click", function () {
    document.getElementById("setPanel").classList.remove("show");
  });
  document.getElementById("setSave").addEventListener("click", function () {
    var v = document.getElementById("startIn").value;
    if (v) store("l12_start", v);
    store("l12_cycle", document.getElementById("cycleIn").value.trim());
    location.reload();
  });

  /* ----- Approval batch queue with strikethrough ----- */
  /* Struck state lives in localStorage keyed by ClickUp task id, so it
     survives the nightly rebuild until the task actually closes. */
  var strikes = load("hive_strikes", {});
  (function pruneStrikes() {
    var live = {};
    queue.forEach(function (t) { live[t.id] = 1; });
    var changed = false;
    Object.keys(strikes).forEach(function (id) {
      if (!live[id]) { delete strikes[id]; changed = true; }
    });
    if (changed) store("hive_strikes", strikes);
  })();
  function isDone(t) { return t.closed || !!strikes[t.id]; }
  function renderQueue() {
    var el = document.getElementById("queueList");
    if (!queue.length) {
      el.innerHTML = '<span class="muted">' +
        (H.approvalsOk || H.placeholder ? "Nothing is waiting on your sign off." : "ClickUp did not load. The queue may not be current.") +
        "</span>";
    } else {
      var open = queue.filter(function (t) { return !isDone(t); });
      var done = queue.filter(function (t) { return isDone(t); });
      el.innerHTML = open.concat(done).map(function (t) {
        var late = t.overdue && !isDone(t);
        return '<div class="q-item' + (isDone(t) ? " done" : "") + '" data-id="' + esc(t.id) + '" title="Tap to strike through">' +
          '<span class="q-name">' + esc(t.name) + "</span>" +
          '<span class="q-due' + (late ? " late" : "") + '">' + esc(t.due) + "</span></div>";
      }).join("");
      el.querySelectorAll(".q-item").forEach(function (row) {
        row.addEventListener("click", function () {
          var id = row.dataset.id;
          var item = null;
          queue.forEach(function (t) { if (t.id === id) item = t; });
          if (item && item.closed) return; /* completed in ClickUp, leave it */
          if (strikes[id]) delete strikes[id]; else strikes[id] = ISO;
          store("hive_strikes", strikes);
          renderQueue();
        });
      });
    }
    var doneCount = queue.filter(isDone).length;
    var meta = queue.length ? doneCount + " of " + queue.length + " cleared" : "";
    var nextBatch = "Monday";
    if (DOW === 1) nextBatch = "today (Monday)";
    else if (DOW === 2 || DOW === 3) nextBatch = DOW === 3 ? "today (Wednesday)" : "Wednesday";
    document.getElementById("queueMeta").textContent =
      (meta ? meta + " · " : "") + "next batch: " + nextBatch;
  }

  /* ----- Footer stamp ----- */
  var stamp = document.getElementById("stamp");
  if (H.placeholder) {
    stamp.textContent = "Preview build. Connect a ClickUp token and list IDs to see live data.";
  } else {
    stamp.textContent = "ClickUp data as of " + (H.generatedAt || "the last build") +
      ". Tap any queue item to strike it through; it clears for good once the task closes in ClickUp.";
  }

  renderQueue();
  renderLeads();
  renderPace();
  renderEnergy();
  renderOne();
})();
</script>
</body>
</html>
"""


def build_page(cfg):
    data = gather_data(cfg)
    fonts_css = font_face("Cormorant Garamond", "CormorantGaramond-600.woff2", "600") + font_face(
        "DM Sans", "DMSans.woff2", "400 500"
    )
    hive_json = json.dumps(data, ensure_ascii=True).replace("<", "\\u003c")
    return TEMPLATE.replace("__FONTS_CSS__", fonts_css).replace("__HIVE_DATA__", hive_json)


def main():
    cfg = get_config()
    if cfg["placeholder"]:
        print("No real CLICKUP_API_TOKEN found. Building a preview page with empty states.")
    DOCS.mkdir(exist_ok=True)
    page = build_page(cfg)
    out = DOCS / "index.html"
    out.write_text(page)
    print(f"Wrote {out} ({len(page):,} bytes)")


if __name__ == "__main__":
    main()

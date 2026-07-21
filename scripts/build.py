#!/usr/bin/env python3
"""Build the Hive Office daily dashboard.

Fetches open tasks from ClickUp, computes today's batch-calendar mode,
and writes a fully self-contained HTML page to docs/index.html.

No third-party dependencies. Python 3.9+.
Run from anywhere: python3 scripts/build.py
"""

import base64
import html
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
FONTS = ROOT / "assets" / "fonts"
ET = ZoneInfo("America/New_York")

# Brand
TEAL = "#015D75"
GOLD = "#C9A84C"
IVORY = "#FCFCF7"

BATCH_CALENDAR = {
    0: ("Money Open", "Open the books, move the pipeline, start the week with revenue."),
    1: ("Camera Day", "Record, batch, capture. Everything visual happens today."),
    2: ("Writing Day", "Words only. Newsletters, curriculum, long form."),
    3: ("People Day", "Calls, meetings, team, and partners."),
    4: ("Money Close", "Close the loops, reconcile, and clear the queue before the weekend."),
    5: ("Off", "The office is closed. Rest is part of the system."),
    6: ("Off", "The office is closed. Rest is part of the system."),
}


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


def fetch_open_tasks(list_id, token):
    """Return a list of open tasks for a ClickUp list, or None on failure."""
    tasks, page = [], 0
    while True:
        url = (
            f"https://api.clickup.com/api/v2/list/{list_id}/task"
            f"?include_closed=false&subtasks=false&page={page}"
        )
        req = urllib.request.Request(url, headers={"Authorization": token})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, OSError) as exc:
            print(f"  warning: could not fetch list {list_id}: {exc}", file=sys.stderr)
            return None
        batch = data.get("tasks", [])
        tasks.extend(batch)
        if data.get("last_page", True) or not batch:
            return tasks
        page += 1


def fetch_all(list_ids, token):
    """Fetch tasks across several lists. Returns (tasks, fetch_ok)."""
    if not list_ids:
        return [], False
    tasks, any_ok = [], False
    for list_id in list_ids:
        result = fetch_open_tasks(list_id, token)
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


def pick_spotlight(tasks):
    """Nearest due date wins; priority breaks ties and covers undated tasks."""
    if not tasks:
        return None
    far_future = datetime(2100, 1, 1, tzinfo=ET)
    return sorted(tasks, key=lambda t: (due_dt(t) or far_future, priority_rank(t)))[0]


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


def build_page(cfg):
    now = datetime.now(ET)
    today = now.date()
    mode, mode_line = BATCH_CALENDAR[now.weekday()]

    if cfg["placeholder"]:
        spotlight_tasks, revenue_ok = [], False
        approval_tasks, approvals_ok = [], False
    else:
        spotlight_tasks, revenue_ok = fetch_all(cfg["revenue"], cfg["token"])
        approval_tasks, approvals_ok = fetch_all(cfg["approvals"], cfg["token"])

    spotlight = pick_spotlight(spotlight_tasks)
    far_future = datetime(2100, 1, 1, tzinfo=ET)
    approval_tasks.sort(key=lambda t: (due_dt(t) or far_future, priority_rank(t)))

    fonts_css = font_face("Cormorant Garamond", "CormorantGaramond-600.woff2", "600") + font_face(
        "DM Sans", "DMSans.woff2", "400 500"
    )

    if spotlight:
        spotlight_html = (
            f'<p class="task-name">{html.escape(spotlight["name"])}</p>'
            f'<p class="task-due">{html.escape(fmt_due(spotlight, today))}</p>'
        )
    else:
        spotlight_html = '<p class="empty">Nothing is flagged in the pipeline today.</p>'

    if approval_tasks:
        rows = "".join(
            f'<li><span class="task-name">{html.escape(t["name"])}</span>'
            f'<span class="task-due">{html.escape(fmt_due(t, today))}</span></li>'
            for t in approval_tasks
        )
        approvals_html = f'<ul class="queue">{rows}</ul>'
    else:
        approvals_html = '<p class="empty">Nothing is waiting on your sign off.</p>'

    footnote = ""
    if cfg["placeholder"]:
        footnote = "Preview build. Connect a ClickUp token and list IDs to see live data."
    elif not (revenue_ok and approvals_ok):
        footnote = "ClickUp could not be reached for part of this page, so it may not be current."

    footnote_html = f'<p class="footnote">{html.escape(footnote)}</p>' if footnote else ""
    date_line = now.strftime("%A, %B %-d, %Y")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>The Hive Office</title>
<style>
{fonts_css}
*{{margin:0;padding:0;box-sizing:border-box;}}
body{{background:{IVORY};color:{TEAL};font-family:'DM Sans',-apple-system,'Helvetica Neue',sans-serif;
  font-weight:400;line-height:1.6;min-height:100vh;display:flex;justify-content:center;}}
main{{width:100%;max-width:620px;padding:9vh 28px 64px;}}
.eyebrow{{font-size:13px;letter-spacing:.22em;text-transform:uppercase;color:{GOLD};font-weight:500;}}
.date{{margin-top:14px;font-size:16px;color:{TEAL};opacity:.75;}}
h1{{font-family:'Cormorant Garamond',Georgia,serif;font-weight:600;font-size:56px;line-height:1.1;
  margin-top:6px;letter-spacing:.01em;}}
.mode-line{{margin-top:10px;font-size:16px;opacity:.8;max-width:44ch;}}
section{{margin-top:56px;}}
h2{{font-size:13px;letter-spacing:.18em;text-transform:uppercase;color:{GOLD};font-weight:500;
  padding-bottom:12px;border-bottom:1px solid rgba(201,168,76,.35);}}
.spotlight{{margin-top:20px;}}
.spotlight .task-name{{font-family:'Cormorant Garamond',Georgia,serif;font-weight:600;font-size:28px;line-height:1.25;}}
.task-due{{font-size:14px;color:{GOLD};font-weight:500;margin-top:4px;}}
.queue{{list-style:none;margin-top:8px;}}
.queue li{{display:flex;justify-content:space-between;align-items:baseline;gap:24px;
  padding:14px 0;border-bottom:1px solid rgba(1,93,117,.1);}}
.queue li:last-child{{border-bottom:none;}}
.queue .task-name{{font-size:16px;}}
.queue .task-due{{margin-top:0;white-space:nowrap;}}
.empty{{margin-top:20px;font-size:16px;opacity:.65;}}
.closing{{margin-top:72px;font-family:'Cormorant Garamond',Georgia,serif;font-size:22px;font-weight:600;
  font-style:italic;color:{TEAL};opacity:.85;}}
.footnote{{margin-top:28px;font-size:12px;opacity:.45;}}
@media (max-width:480px){{h1{{font-size:42px;}}main{{padding-top:6vh;}}
  .queue li{{flex-direction:column;gap:2px;}}}}
</style>
</head>
<body>
<main>
  <header>
    <p class="eyebrow">The Hive Office</p>
    <p class="date">{date_line}</p>
    <h1>{html.escape(mode)}</h1>
    <p class="mode-line">{html.escape(mode_line)}</p>
  </header>
  <section>
    <h2>Today's Money Move</h2>
    <div class="spotlight">{spotlight_html}</div>
  </section>
  <section>
    <h2>Waiting on You</h2>
    {approvals_html}
  </section>
  <p class="closing">That is the morning picture. Send a quick note back if anything needs to move.</p>
  {footnote_html}
</main>
</body>
</html>
"""


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

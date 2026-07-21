# Hive Office Daily Dashboard

A one page morning brief that rebuilds itself every weekday at 7 AM Eastern and publishes to one fixed URL you can set as your browser homepage. It shows:

1. Today's date and your batch-calendar mode (Money Open, Camera Day, Writing Day, People Day, Money Close)
2. The single most time-sensitive item in your revenue pipeline
3. Every open item waiting on your sign off, with due dates

No app, no server, no database. A small script runs on GitHub's computers each morning, asks ClickUp what's open, and rewrites one static web page.

## One-time setup (about 10 minutes)

### 1. Put this project on GitHub

If the repo does not exist yet, create it at github.com (name it `hive-office-dashboard`, Private is fine) and push this folder to it on the `main` branch.

### 2. Add your three secrets

In the repo on github.com: **Settings > Secrets and variables > Actions > New repository secret**. Add these three, one at a time:

| Name | Value |
|---|---|
| `CLICKUP_API_TOKEN` | Your ClickUp token. Get it in ClickUp: your avatar > Settings > Apps > Generate under API Token. It starts with `pk_`. |
| `APPROVALS_LIST_IDS` | The List ID (or several, separated by commas) holding items waiting on your sign off. |
| `REVENUE_LIST_IDS` | The List ID (or several, separated by commas) for your enrollment and partnership pipeline. |

To find a List ID: open the list in ClickUp in your browser. The ID is the long number in the URL right after `/li/`.

### 3. Turn on GitHub Pages

**Settings > Pages**. Under Build and deployment set:

- Source: **Deploy from a branch**
- Branch: **main**, folder: **/docs**

Save. After a minute or two the page is live at:

**`https://YOUR-GITHUB-USERNAME.github.io/hive-office-dashboard/`**

That is the URL to bookmark or set as your browser homepage. (If the repo is private, Pages requires a paid GitHub plan; on the free plan just make the repo public. The page only ever shows task names and due dates.)

### 4. Run it once by hand to check

**Actions tab > Daily brief > Run workflow**. When it finishes green, refresh your Pages URL. From then on it refreshes itself every weekday morning.

## Testing locally

You can build the page on your own computer without touching GitHub:

```
cp .env.example .env
# open .env and paste in your real token and list IDs
python3 scripts/build.py
open docs/index.html
```

With placeholder values it still builds a preview page with friendly empty states, so you can see the layout before wiring in real data.

## About the schedule

The workflow runs at `0 11 * * 1-5`, which is 11:00 UTC, Monday through Friday. That is 7:00 AM Eastern during daylight saving time. When clocks fall back in November it becomes 6:00 AM Eastern. If that bothers you, edit `.github/workflows/daily-brief.yml` and change `11` to `12` for the winter months, then back again in March. GitHub's scheduler also sometimes starts jobs a few minutes late; that is normal.

## If something looks wrong

- Page says "Preview build": the `CLICKUP_API_TOKEN` secret is missing or still a placeholder.
- Page says ClickUp could not be reached: usually a wrong List ID or an expired token. Re-check the secrets.
- Page never updates: check the Actions tab for a red X and open the failed run to see the message.

The page is intentionally forgiving: if ClickUp is down or a list is empty, it shows a calm "nothing waiting" state instead of an error.

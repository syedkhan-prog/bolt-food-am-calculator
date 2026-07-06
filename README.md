# Bolt Food AM Campaign Calculator

Dashboard for planning AM campaign spend, checking budget envelopes against Capital Allocation, and tracking estimate accuracy week over week.

**Fork of** [duncancalleja/bolt-food-campaign-calculator](https://github.com/duncancalleja/bolt-food-campaign-calculator) with budget forecasting, peer cost benchmarks, and automatic learning from snapshots vs actuals.

---

## For AMs (non-technical) — open the dashboard in 2 minutes

### Option A — GitHub Pages (easiest, no install)

1. Open: **https://syedkhan-prog.github.io/bolt-food-am-calculator/am-spend-dashboard.html**  
   *(after Pages is enabled — see “One-time setup” below)*

### Option B — Clone and open locally

1. Install [Git](https://git-scm.com/downloads) (one-time).
2. Open Terminal (Mac) or Command Prompt (Windows) and run:

```bash
git clone https://github.com/syedkhan-prog/bolt-food-am-calculator.git
cd bolt-food-am-calculator
python3 -m http.server 8765
```

3. In your browser go to: **http://localhost:8765/am-spend-dashboard.html**

> **Important:** Do not double-click the HTML file. Always use the local server URL above, or GitHub Pages — otherwise budget data and sheets will not load.

### Using the dashboard

1. Pick your **country** in the top dropdown (e.g. Malta).
2. The dashboard loads your **traitless OPS sheet** automatically for Malta; other countries may need a sheet ID in settings (see `data/SHEETS_AND_SNAPSHOTS.md`).
3. Use **filters** (week, campaign type, reason, search) to narrow the view.
4. Check the **AM Budget Envelope** panel — weekly cap from Capital Allocation vs your planned Bolt spend.
5. **Forward plan (July)** shows next month’s CA budget target.
6. Expand **Estimate Snapshots & Accuracy Tracker** to see learning from last week’s actuals.

### Malta sheet

Malta’s Google Sheet is configured in `data/sheet-config.json` — everyone gets the same sheet when using this repo.

---

## Main files

| File | What it is |
|------|------------|
| `am-spend-dashboard.html` | **AM spend dashboard** (primary tool) |
| `campaign-cost-calculator.html` | Per-provider cost calculator |
| `investment-dashboard.html` | Investment overview |
| `data/{country}-budget.json` | CA budget envelope + July forward plan |
| `data/{country}-dash.json` | Campaign history & actuals (refreshed from Databricks) |
| `data/sheet-config.json` | Shared Google Sheet IDs per country |

---

## For admins — refreshing data

Requires Bolt VPN + Databricks token. For **automatic Monday refreshes** via GitHub Actions, add a repo secret:

1. GitHub → **https://github.com/syedkhan-prog/bolt-food-am-calculator/settings/secrets/actions**
2. **New repository secret** → Name: `DATABRICKS_TOKEN` → Value: your Databricks SQL PAT
3. Re-run **Actions → Refresh Data from Databricks → Run workflow**

Without this secret, scheduled runs skip Databricks (dashboard still works with last committed data).

```bash
python3 scripts/build_ca_targets.py          # when new CA month published
python3 scripts/refresh_data.py              # all countries (~1–2 hrs)
python3 scripts/refresh_budget_data.py       # budget envelopes
python3 scripts/build_cost_benchmarks.py     # cold-start peer DI%
python3 scripts/learn_from_snapshots.py      # snapshot vs actuals corrections
python3 scripts/validate_forecast.py         # accuracy backtest
```

GitHub Actions runs **weekly snapshots** (Sunday) and **data refresh** (Monday) if `DATABRICKS_TOKEN` is set in repo secrets.

More detail: `README_BUDGET.md`, `data/SHEETS_AND_SNAPSHOTS.md`.

---

## One-time setup — GitHub Pages (for link sharing)

Repo → **Settings** → **Pages** → Source: **Deploy from branch** → branch `main` → folder `/ (root)` → Save.

Share: `https://syedkhan-prog.github.io/bolt-food-am-calculator/am-spend-dashboard.html`

---

## Upstream

Original calculator: https://github.com/duncancalleja/bolt-food-campaign-calculator  
This repo is an independent fork; changes here are not pushed to Duncan’s repo.

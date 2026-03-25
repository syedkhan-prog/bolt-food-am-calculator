# Shared sheets & team snapshots

## Malta (`mt`) — shared OPS sheet (repository)

- **`data/sheet-config.json`** holds the **Malta** Traitless OPS Google Sheet (`sheetId` + `gid`).
- When this file is updated on GitHub and the site is deployed, **every AM** sees the same Malta sheet automatically.
- On the dashboard, Malta’s Sheet ID / GID fields are **read-only** (they mirror the repo).

## Other countries — this browser only

- Sheet ID and GID are **not** read from `sheet-config.json` for countries other than Malta.
- Each AM enters values (or uses **Import sheet JSON** with `{"sheetId":"…","gid":"…"}`). Data is stored in **`localStorage` on that device only** — nothing is pushed to GitHub from the dashboard.
- Optional: keep a small JSON file on your machine and re-import when you change browsers or machines.

## `team-snapshots.json`

- Optional **shared snapshot history** for calibration / learning.
- **Export all countries** on the dashboard downloads local snapshot data; you can merge into `team-snapshots.json` and push for team-wide history.

## Scheduled weekly snapshots (GitHub Actions)

- Workflow **Weekly team snapshots** (Sunday 08:00 UTC, or run manually) collects snapshots for countries that have a sheet available in that environment.
- **In CI**, only **Malta** is driven from `sheet-config.json` (no per-country `localStorage`). Other countries are snapshot only when an AM runs the dashboard locally with sheet IDs saved in the browser, or via Sunday in-browser runs.

## Sunday auto-snapshots (browser)

- If someone opens the **Spend dashboard on a Sunday**, the app snapshots countries that have a configured sheet (Malta from repo; others from local storage).
- Snapshots live under `am_spend_snapshots_<cc>` in the browser.

---

## Optional: change Malta’s sheet via GitHub (admins)

Edit **`data/sheet-config.json`** for the `mt` entry and push, or use **`repository_dispatch`** / the **`workers/`** Cloudflare pattern if your org already deployed that — not required for normal AM use.

## Files reference

| File | Role |
|------|------|
| `data/sheet-config.json` | **Malta** shared OPS sheet (repo); other keys may exist as placeholders |
| `data/team-snapshots.json` | Optional shared snapshot history |
| `.github/workflows/weekly-snapshots.yml` | Scheduled snapshot collector |
| `scripts/ci_weekly_snapshots.mjs`, `scripts/merge_team_snapshots.py` | CI helpers |

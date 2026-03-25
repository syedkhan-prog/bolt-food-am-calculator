# Optional: webhook / Worker (advanced)

The **Spend dashboard** does **not** require a webhook. Malta uses **`data/sheet-config.json`** in the repo; other countries use **browser storage** only.

If your org wants to **automate edits** to `sheet-config.json` (e.g. for Malta) via `repository_dispatch`, the optional Cloudflare Worker in **`workers/`** and **`update-sheet-config.yml`** still exist — use only if IT deploys and maintains them.

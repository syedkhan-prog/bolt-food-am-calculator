# Budget Forecast (Capital Allocation Integration)

This fork adds **data-driven AM budget envelopes** to the AM Spend Dashboard, replacing manual weekly budget inputs.

## How it works

1. **Targets** — `data/capital-allocation-targets.json` holds GMV targets and DI% from the monthly Capital Allocation sheet (Global Summary tab). Update this when a new month’s sheet is published.

2. **Live actuals** — `scripts/refresh_budget_data.py` queries Databricks (same tables as Looker dashboards 26798 GMV and 6362 DI spend):
   - GMV MTD from `etl_delivery_order_monetary_metrics` (ex-Bolt Market)
   - Campaign spend by objective from `etl_delivery_campaign_order_metrics` (ex-Bolt Market)

3. **Envelope formula** (RR-paced, no hallucination):
   ```
   gmv_run_rate_eom = gmv_mtd × (days_in_month / elapsed_days)
   am_rr_budget     = gmv_run_rate_eom × (am_food_pct + marketing_food_pct)
   remaining_am     = am_rr_budget − mtd_am_bolt_spend
   weekly_envelope  = remaining_am / weeks_left_in_month
   ```

4. **Dashboard** — `am-spend-dashboard.html` loads `data/{cc}-budget.json` and compares planned OPS Bolt spend vs the weekly AM envelope.

## Refresh commands

```bash
# Budget envelopes (requires Databricks token)
python3 scripts/refresh_budget_data.py           # all countries
python3 scripts/refresh_budget_data.py sk cz mt  # specific markets

# Campaign history + provider data (existing)
python3 scripts/refresh_data.py sk cz mt
```

## Campaign cost estimates — two-regime, backtested

`estimateCampaignCost()` picks the most accurate available signal, in priority order:

1. **Tier history** (`_tierSpendEstimate`) — provider has run this/a similar tier before.
   `weekly_cost = camp_orders_wk × €/order`, with discount-depth scaling and a light
   recent-actuals blend. **Backtest: ~5% MAPE, unbiased (median ratio 1.00).** This is
   the common AM case (repeat campaigns).

2. **Peer cost-rate benchmark** (`_benchmarkEstimate`) — *cold start*, no tier history.
   Applies an empirical **DI%** (discount € ÷ provider GMV) derived from every provider
   that ran a similar campaign, looked up hierarchically:
   `cc|cat|disc|seg → cc|cat|disc → cc|cat|seg → cc|cat → cat|disc|seg → cat|disc → cat`
   `weekly_cost = provider_weekly_GMV × benchmark_DI%`.
   **5-fold provider CV: portfolio bias ≈ 1.02x (unbiased), provider-aggregate MAPE ≈ 48%**
   — ~5x better than the old flat-constant fallback (~324% per-campaign). Built by
   `scripts/build_cost_benchmarks.py` → `data/cost-benchmarks.json` (10% trimmed mean of
   per-provider medians, ≥5 providers per group). Flagged **low/medium/high confidence**.

3. **Formula fallback** — only if no peer benchmark exists at all (very rare).

Estimate source badges: `history`, `blended`, `scaled`, `peer DI` (cold-start, shows the
DI% and confidence), `formula`.

> Honest limitation: a *single* brand-new campaign is inherently uncertain (redemption is
> provider-specific). The benchmark is **unbiased in aggregate** — correct for budget
> envelopes and portfolios — but any one new campaign can be off. Repeat campaigns (with
> history) are accurate to ~5%.

Re-check accuracy anytime: `python3 scripts/validate_forecast.py`.

## Continuous learning (snapshot → actuals → next forecast)

The model improves week-over-week through a closed loop:

| When | What happens |
|------|----------------|
| **Sunday** | Traitless sheet snapshot (planned campaigns + estimates) → `data/team-snapshots.json` via CI or browser auto-snap |
| **Mon–Sun** | Campaigns run |
| **Monday** | `refresh_data.py` pulls Databricks actuals into `{cc}-dash.json` (updates tier history + peer benchmarks) |
| **Monday** | `learn_from_snapshots.py` compares last week's snapshot vs actuals → `data/{cc}-corrections.json` |
| **Every load** | Dashboard auto-learns from local/team snapshots vs embedded actuals and applies corrections |

Corrections are applied automatically to all estimates (provider-specific → category → portfolio).
No manual "Calibrate" click needed. The Accuracy Tracker panel shows what was learned.

To enable learning for a country: ensure its traitless sheet is in `sheet-config.json` so Sunday
snapshots are collected, then push merged snapshots to `team-snapshots.json`.

## Files

| File | Purpose |
|------|---------|
| `data/budget-config.json` | Sheet IDs, spend objective → CA bucket mapping |
| `data/capital-allocation-targets-YYYY-MM.json` | GMV + DI% targets per country per month |
| `data/{cc}-budget.json` | Computed envelopes + MTD actuals + forward plan |
| `data/cost-benchmarks.json` | Empirical peer DI% benchmarks (cold-start cost path) |
| `scripts/refresh_budget_data.py` | Databricks budget refresh |
| `scripts/build_cost_benchmarks.py` | Rebuilds peer DI% benchmarks from history |
| `scripts/validate_forecast.py` | Backtests both cost regimes (tier history + cold start) |

## Upstream

This repo tracks `upstream` = Duncan’s original calculator. Changes are made here only; upstream is not modified.

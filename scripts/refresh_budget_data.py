#!/usr/bin/env python3
"""
Refresh capital-allocation budget envelopes from Databricks + CA targets.

Pulls MTD GMV (Looker 26798 equivalent) and campaign spend by objective
(Looker 6362 equivalent), merges with capital allocation DI% targets, and
writes data/{cc}-budget.json for the AM spend dashboard.

Usage:
    python3 scripts/refresh_budget_data.py           # all countries in targets file
    python3 scripts/refresh_budget_data.py sk cz mt # specific countries

Requires:
    - databricks-sql-connector, pandas
    - ~/.databricks_token or DATABRICKS_TOKEN
    - data/capital-allocation-targets.json (from CA sheet)
"""

from __future__ import annotations

import calendar
import json
import os
import sys
from datetime import date, datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(__file__))
from dbx import DBX

DATA_DIR = os.path.join(ROOT, "data")


def load_json(name: str) -> dict:
    path = os.path.join(DATA_DIR, name)
    with open(path) as f:
        return json.load(f)


def parse_month(month_str: str) -> tuple[date, date, int]:
    """Return (month_start, month_end, days_in_month) for YYYY-MM."""
    year, month = map(int, month_str.split("-"))
    days = calendar.monthrange(year, month)[1]
    start = date(year, month, 1)
    end = date(year, month, days)
    return start, end, days


def pull_gmv_mtd(dbx: DBX, cc: str, month_start: date, as_of: date) -> float:
    df = dbx.query(f"""
        SELECT ROUND(SUM(COALESCE(gmv_eur, 0)), 2) AS gmv_mtd
        FROM ng_public_spark.etl_delivery_order_monetary_metrics
        WHERE country = '{cc}'
          AND order_created_date >= DATE('{month_start.isoformat()}')
          AND order_created_date <= DATE('{as_of.isoformat()}')
          AND is_bolt_market = false
    """)
    return float(df.iloc[0]["gmv_mtd"] or 0)


def pull_spend_mtd(dbx: DBX, cc: str, month_start: date, as_of: date) -> list[dict]:
    df = dbx.query(f"""
        SELECT
            COALESCE(cm.spend_objective, 'unknown') AS spend_objective,
            ROUND(SUM(COALESCE(cm.bolt_spend, 0)), 2) AS bolt_spend,
            ROUND(SUM(COALESCE(cm.provider_spend, 0)), 2) AS provider_spend
        FROM ng_public_spark.etl_delivery_campaign_order_metrics cm
        INNER JOIN ng_delivery_spark.dim_provider_v2 p
            ON cm.provider_id = p.provider_id
        WHERE cm.country = '{cc}'
          AND cm.order_created_date >= DATE('{month_start.isoformat()}')
          AND cm.order_created_date <= DATE('{as_of.isoformat()}')
          AND COALESCE(p.is_bolt_market_provider, false) = false
        GROUP BY 1
    """)
    return df.to_dict("records")


def map_spend_to_buckets(records: list[dict], mapping: dict) -> dict:
    buckets: dict[str, dict] = {}
    unmapped: dict[str, dict] = {}
    for r in records:
        raw = (r.get("spend_objective") or "unknown").lower()
        bucket = mapping.get(raw, "unmapped")
        bolt = float(r.get("bolt_spend") or 0)
        prov = float(r.get("provider_spend") or 0)
        if bucket == "unmapped":
            unmapped.setdefault(raw, {"bolt": 0.0, "provider": 0.0})
            unmapped[raw]["bolt"] += bolt
            unmapped[raw]["provider"] += prov
            continue
        buckets.setdefault(bucket, {"bolt": 0.0, "provider": 0.0})
        buckets[bucket]["bolt"] += bolt
        buckets[bucket]["provider"] += prov
    return {"buckets": buckets, "unmapped": unmapped}


def sum_am_spend(buckets: dict) -> float:
    """AM envelope = am_food + marketing_food buckets (matches CA Global Summary E+F)."""
    total = 0.0
    for bucket in ("am_food", "marketing_food"):
        total += buckets.get(bucket, {}).get("bolt", 0.0)
    return total


def build_budget(cc: str, targets: dict, config: dict, dbx: DBX, as_of: date) -> dict | None:
    country_targets = targets["countries"].get(cc)
    if not country_targets or not country_targets.get("gmv_target"):
        print(f"  skip {cc}: no CA targets")
        return None

    month_str = targets["month"]
    month_start, month_end, days_in_month = parse_month(month_str)
    if as_of > month_end:
        as_of = month_end
    if as_of < month_start:
        as_of = month_start

    elapsed_days = (as_of - month_start).days + 1
    days_left = (month_end - as_of).days + 1
    weeks_left = max(1 / 7, days_left / 7.0)

    gmv_target = float(country_targets["gmv_target"])
    di_pct = country_targets.get("di_pct", {})
    am_pct = di_pct.get("am_food", 0) + di_pct.get("marketing_food", 0)
    total_bolt_pct = di_pct.get("total_bolt", 0)

    gmv_mtd = pull_gmv_mtd(dbx, cc, month_start, as_of)
    spend_records = pull_spend_mtd(dbx, cc, month_start, as_of)
    spend_mapped = map_spend_to_buckets(spend_records, config["spend_objective_to_bucket"])

    gmv_expected_mtd = gmv_target * (elapsed_days / days_in_month)
    gmv_pace_pct = (gmv_mtd / gmv_expected_mtd) if gmv_expected_mtd > 0 else 0.0
    gmv_run_rate_eom = (gmv_mtd / elapsed_days * days_in_month) if elapsed_days > 0 else 0.0

    total_bolt_monthly = gmv_target * total_bolt_pct
    total_bolt_rr = gmv_run_rate_eom * total_bolt_pct
    am_monthly = gmv_target * am_pct
    am_rr = gmv_run_rate_eom * am_pct

    mtd_bolt = sum(float(r.get("bolt_spend") or 0) for r in spend_records)
    mtd_am = sum_am_spend(spend_mapped["buckets"])

    remaining_total = total_bolt_rr - mtd_bolt
    remaining_am = am_rr - mtd_am
    weekly_am_envelope = remaining_am / weeks_left if weeks_left > 0 else remaining_am

    by_objective = {}
    for r in spend_records:
        obj = r.get("spend_objective") or "unknown"
        by_objective[obj] = {
            "bolt": float(r.get("bolt_spend") or 0),
            "provider": float(r.get("provider_spend") or 0),
        }

    by_bucket = {
        k: {"bolt": round(v["bolt"], 2), "provider": round(v["provider"], 2)}
        for k, v in spend_mapped["buckets"].items()
    }

    return {
        "country": cc,
        "country_name": country_targets.get("country_name", cc.upper()),
        "month": month_str,
        "as_of": as_of.isoformat(),
        "refreshed": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "gmv_target": round(gmv_target, 2),
        "gmv_mtd": round(gmv_mtd, 2),
        "gmv_run_rate_eom": round(gmv_run_rate_eom, 2),
        "gmv_pace_pct": round(gmv_pace_pct, 4),
        "elapsed_days": elapsed_days,
        "days_in_month": days_in_month,
        "days_left": days_left,
        "weeks_left": round(weeks_left, 2),
        "di_pct": di_pct,
        "am_di_pct": round(am_pct, 6),
        "budget_eur": {
            "total_bolt_monthly": round(total_bolt_monthly, 2),
            "total_bolt_rr_paced": round(total_bolt_rr, 2),
            "am_monthly": round(am_monthly, 2),
            "am_rr_paced": round(am_rr, 2),
            "remaining_total_bolt": round(remaining_total, 2),
            "remaining_am": round(remaining_am, 2),
            "weekly_am_envelope": round(weekly_am_envelope, 2),
            "daily_am_envelope": round(remaining_am / days_left, 2) if days_left > 0 else 0,
        },
        "mtd_actual": {
            "bolt_spend": round(mtd_bolt, 2),
            "am_spend": round(mtd_am, 2),
            "by_objective": by_objective,
            "by_bucket": by_bucket,
            "unmapped_objectives": spend_mapped["unmapped"],
        },
        "source": {
            **config.get("sources", {}),
            "targets_file": targets.get("sheet_id", ""),
            "capital_allocation_label": targets.get("label", config.get("capital_allocation_label", "")),
        },
    }


def build_forward_plan(cc: str, forward_targets: dict) -> dict | None:
    """Target-only envelope for next month (no MTD actuals)."""
    country_targets = forward_targets["countries"].get(cc)
    if not country_targets or not country_targets.get("gmv_target"):
        return None
    gmv_target = float(country_targets["gmv_target"])
    di_pct = country_targets.get("di_pct", {})
    am_pct = di_pct.get("am_food", 0) + di_pct.get("marketing_food", 0)
    days_in_month = calendar.monthrange(*map(int, forward_targets["month"].split("-")))[1]
    am_monthly = gmv_target * am_pct
    weekly_avg = am_monthly / (days_in_month / 7.0)
    return {
        "month": forward_targets["month"],
        "label": forward_targets.get("label", ""),
        "gmv_target": round(gmv_target, 2),
        "am_di_pct": round(am_pct, 6),
        "am_monthly": round(am_monthly, 2),
        "weekly_am_avg": round(weekly_avg, 2),
        "di_pct": di_pct,
    }


def resolve_targets(config: dict, as_of: date) -> tuple[dict, dict | None]:
    """Pick current-month CA targets + optional forward month from index."""
    index_path = config.get("allocation_index_file", "capital-allocation-index.json")
    try:
        index = load_json(index_path)
    except FileNotFoundError:
        targets = load_json(config["targets_file"])
        return targets, None

    months = index.get("allocation_months", {})
    current_key = as_of.strftime("%Y-%m")
    if current_key not in months:
        current_key = index.get("default_month") or sorted(months.keys())[-1]

    current_meta = months[current_key]
    current = load_json(current_meta["targets_file"])
    current["month"] = current_key
    current["label"] = current_meta.get("label", "")

    forward = None
    sorted_keys = sorted(months.keys())
    if current_key in sorted_keys:
        idx = sorted_keys.index(current_key)
        if idx + 1 < len(sorted_keys):
            fwd_key = sorted_keys[idx + 1]
            fwd_meta = months[fwd_key]
            forward = load_json(fwd_meta["targets_file"])
            forward["month"] = fwd_key
            forward["label"] = fwd_meta.get("label", "")
    return current, forward


def main():
    requested = [c.lower() for c in sys.argv[1:]] if len(sys.argv) > 1 else None
    config = load_json("budget-config.json")
    as_of = date.today()
    targets, forward_targets = resolve_targets(config, as_of)

    _, month_end, _ = parse_month(targets["month"])
    if as_of > month_end:
        as_of = month_end

    countries = requested or sorted(
        cc for cc, t in targets["countries"].items() if t.get("gmv_target", 0) > 0
    )

    print(f"Budget refresh for {targets['month']} as of {as_of.isoformat()}")
    if forward_targets:
        print(f"Forward plan: {forward_targets['month']} ({forward_targets.get('label', '')})")
    print(f"Countries: {', '.join(countries)}")

    with DBX() as dbx:
        for cc in countries:
            print(f"  {cc}...", end=" ", flush=True)
            try:
                payload = build_budget(cc, targets, config, dbx, as_of)
                if not payload:
                    print("skipped")
                    continue
                if forward_targets:
                    fwd = build_forward_plan(cc, forward_targets)
                    if fwd:
                        payload["forward"] = fwd
                out = os.path.join(DATA_DIR, f"{cc}-budget.json")
                with open(out, "w") as f:
                    json.dump(payload, f, indent=2)
                print(
                    f"OK (GMV MTD €{payload['gmv_mtd']:,.0f}, "
                    f"AM envelope €{payload['budget_eur']['weekly_am_envelope']:,.0f}/wk)"
                )
            except Exception as e:
                print(f"ERROR: {e}")


if __name__ == "__main__":
    main()

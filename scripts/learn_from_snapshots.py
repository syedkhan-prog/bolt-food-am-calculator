#!/usr/bin/env python3
"""
Learn forecast corrections by comparing weekly traitless-sheet snapshots (planned spend)
against Databricks actuals embedded in {cc}-dash.json.

Run after refresh_data.py (Monday CI) once the previous week's campaigns have actuals.
Writes data/{cc}-corrections.json used by the dashboard to adjust next-week estimates.

Usage:
    python3 scripts/learn_from_snapshots.py           # all countries with snapshots
    python3 scripts/learn_from_snapshots.py mt sk    # specific countries
"""

from __future__ import annotations

import glob
import json
import os
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
TEAM_SNAPSHOTS = os.path.join(DATA, "team-snapshots.json")

RATIO_MIN, RATIO_MAX = 0.5, 2.0
PROVIDER_RATIO_MIN, PROVIDER_RATIO_MAX = 0.4, 2.5
MAX_WEEKS = 8


def camp_type_to_cat(camp_type: str) -> str:
    t = camp_type or ""
    if "Free" in t and "Delivery" in t:
        return "fd"
    if t == "Menu Discount":
        return "md"
    if t == "Item Discount":
        return "id"
    return "ot"


def load_team_snapshots() -> dict[str, list]:
    if not os.path.exists(TEAM_SNAPSHOTS):
        return {}
    with open(TEAM_SNAPSHOTS) as f:
        data = json.load(f)
    return data.get("byCountry") or {}


def load_local_snapshots(cc: str) -> list:
    # Not available server-side; team-snapshots.json is the shared source in CI.
    return []


def infer_target_week(snap: dict) -> tuple[int, int] | None:
    """Return (year, iso_week) the snapshot's campaigns ran in."""
    camps = snap.get("campaigns") or []
    weeks = [c.get("week") for c in camps if c.get("week")]
    if weeks:
        wk = int(statistics.mode(weeks))
    else:
        wk = int(snap.get("isoWeek") or 0) + 1
    if wk <= 0:
        return None
    year = int(str(snap.get("date", ""))[:4] or date.today().year)
    return year, wk


def week_key(year: int, iso_week: int) -> str:
    return f"{year}-W{iso_week:02d}" if iso_week < 10 else f"{year}-W{iso_week}"


def aggregate_snapshot_by_provider(campaigns: list) -> dict[str, dict]:
    by_pid: dict[str, dict] = {}
    for c in campaigns:
        pid = str(c.get("pid", ""))
        if not pid:
            continue
        cat = camp_type_to_cat(c.get("type", ""))
        entry = by_pid.setdefault(pid, {
            "est_total": 0.0, "est_bolt": 0.0, "est_prov": 0.0, "cats": set(),
        })
        entry["est_total"] += float(c.get("estTotal") or 0)
        entry["est_bolt"] += float(c.get("estBolt") or 0)
        entry["est_prov"] += float(c.get("estProv") or 0)
        entry["cats"].add(cat)
    return by_pid


def extract_actuals_for_provider(pid_data: dict, cats: set[str]) -> dict | None:
    if not pid_data:
        return None
    if isinstance(pid_data, list):
        return {"bolt": pid_data[0], "prov": pid_data[1], "total": pid_data[2]}
    bolt = prov = total = 0.0
    found = False
    for cat in cats:
        if cat in pid_data:
            v = pid_data[cat]
            if isinstance(v, list) and len(v) >= 3:
                bolt += float(v[0] or 0)
                prov += float(v[1] or 0)
                total += float(v[2] or 0)
                found = True
    if not found:
        return None
    if total <= 0 and (bolt or prov):
        total = bolt + prov
    return {"bolt": bolt, "prov": prov, "total": total}


def compare_snapshot_to_actuals(snap: dict, dbx_actuals: dict) -> dict | None:
    target = infer_target_week(snap)
    if not target:
        return None
    year, wk = target
    wk_str = week_key(year, wk)
    if wk_str not in dbx_actuals:
        return None

    week_data = dbx_actuals[wk_str]
    by_pid = aggregate_snapshot_by_provider(snap.get("campaigns") or [])

    provider_ratios: dict[str, list[float]] = defaultdict(list)
    cat_pairs: dict[str, list[tuple[float, float]]] = defaultdict(list)
    total_est = total_act = 0.0
    matched = 0

    for pid, est in by_pid.items():
        act = extract_actuals_for_provider(week_data.get(pid), est["cats"])
        if not act or act["total"] <= 0 or est["est_total"] <= 0:
            continue
        matched += 1
        ratio = act["total"] / est["est_total"]
        ratio = max(PROVIDER_RATIO_MIN, min(PROVIDER_RATIO_MAX, ratio))
        provider_ratios[pid].append(ratio)
        total_est += est["est_total"]
        total_act += act["total"]
        for cat in est["cats"]:
            cat_pairs[cat].append((est["est_total"], act["total"]))

    if matched == 0:
        return None

    portfolio_ratio = total_act / total_est if total_est > 0 else 1.0
    return {
        "week": wk_str,
        "snap_date": snap.get("date"),
        "matched_providers": matched,
        "portfolio_ratio": round(portfolio_ratio, 4),
        "total_est": round(total_est, 2),
        "total_act": round(total_act, 2),
        "provider_ratios": {pid: statistics.median(r) for pid, r in provider_ratios.items()},
        "cat_est_act": {cat: list(v) for cat, v in cat_pairs.items()},
    }


def build_corrections(cc: str, snapshots: list, dbx_actuals: dict) -> dict | None:
    if not snapshots or not dbx_actuals:
        return None

    snapshots = sorted(snapshots, key=lambda s: s.get("date", ""), reverse=True)[:MAX_WEEKS]
    week_results = []
    for snap in snapshots:
        r = compare_snapshot_to_actuals(snap, dbx_actuals)
        if r:
            week_results.append(r)

    if not week_results:
        return None

    # Rolling provider correction: median ratio across evaluated weeks.
    prov_accum: dict[str, list[float]] = defaultdict(list)
    cat_accum: dict[str, list[tuple[float, float]]] = defaultdict(list)
    port_ratios = []

    for wr in week_results:
        port_ratios.append(wr["portfolio_ratio"])
        for pid, ratio in wr["provider_ratios"].items():
            prov_accum[pid].append(ratio)
        for cat, pairs in wr["cat_est_act"].items():
            cat_accum[cat].extend(pairs)

    by_provider = {}
    for pid, ratios in prov_accum.items():
        if len(ratios) >= 1:
            r = statistics.median(ratios)
            by_provider[pid] = {
                "ratio": round(max(RATIO_MIN, min(RATIO_MAX, r)), 4),
                "weeks": len(ratios),
            }

    by_category = {}
    for cat, pairs in cat_accum.items():
        est = sum(e for e, _ in pairs)
        act = sum(a for _, a in pairs)
        if est > 0:
            r = max(RATIO_MIN, min(RATIO_MAX, act / est))
            by_category[cat] = round(r, 4)

    portfolio_ratio = statistics.median(port_ratios) if port_ratios else 1.0
    portfolio_ratio = max(RATIO_MIN, min(RATIO_MAX, portfolio_ratio))

    return {
        "country": cc,
        "refreshed": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "weeks_evaluated": len(week_results),
        "latest_week": week_results[0]["week"],
        "latest_snap_date": week_results[0]["snap_date"],
        "portfolio": {
            "ratio": round(portfolio_ratio, 4),
            "bias_pct": round((portfolio_ratio - 1) * 100, 1),
        },
        "by_category": by_category,
        "by_provider": by_provider,
        "history": [
            {
                "week": w["week"],
                "snap_date": w["snap_date"],
                "matched": w["matched_providers"],
                "ratio": w["portfolio_ratio"],
                "est": w["total_est"],
                "act": w["total_act"],
            }
            for w in week_results
        ],
    }


def main():
    requested = [c.lower() for c in sys.argv[1:]] if len(sys.argv) > 1 else None
    team = load_team_snapshots()

    countries = requested or sorted(set(team.keys()) | {
        f.replace("-dash.json", "")
        for f in os.listdir(DATA)
        if f.endswith("-dash.json")
    })

    print("Learning forecast corrections from snapshots vs Databricks actuals")
    for cc in countries:
        dash_path = os.path.join(DATA, f"{cc}-dash.json")
        if not os.path.exists(dash_path):
            print(f"  {cc}: skip (no dash json)")
            continue
        with open(dash_path) as f:
            dash = json.load(f)
        dbx_actuals = dash.get("dbx_actuals") or {}
        snaps = team.get(cc, [])
        if not snaps:
            print(f"  {cc}: skip (no snapshots in team-snapshots.json)")
            continue

        payload = build_corrections(cc, snaps, dbx_actuals)
        if not payload:
            print(f"  {cc}: no matching snapshot weeks in dbx_actuals")
            continue

        out = os.path.join(DATA, f"{cc}-corrections.json")
        with open(out, "w") as f:
            json.dump(payload, f, indent=2)
        p = payload["portfolio"]
        print(
            f"  {cc}: OK — {payload['weeks_evaluated']} week(s), "
            f"latest {payload['latest_week']}, "
            f"portfolio {p['ratio']:.3f}x ({p['bias_pct']:+.1f}%), "
            f"{len(payload['by_provider'])} provider corrections"
        )


if __name__ == "__main__":
    main()

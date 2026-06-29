#!/usr/bin/env python3
"""
Build empirical peer cost-rate benchmarks for the cold-start (no-history) path.

Problem: when a provider has NEVER run a given campaign tier, we cannot use its own
history. The old fallback used flat redemption constants (MD 92% / ID 35% / FD 92%),
which backtests at ~256% MAPE — basically guessing.

Approach: from every provider+tier that DOES have history, compute the AOV-normalised
discount intensity (a DI%):

    cost_rate = weekly_discount_spend / (provider_weekly_orders_during_campaign × AOV)
              = discount € given away per € of provider GMV

This single ratio folds redemption, discount-per-order and basket share into one stable,
interpretable number that clusters by (country, campaign type, discount depth, segment).
For a new tier we then predict:

    weekly_cost = provider_weekly_orders × AOV × cost_rate_benchmark
                = provider_weekly_GMV × benchmark DI%

which directly mirrors the capital-allocation DI% logic.

Aggregation: 10%-trimmed mean of per-provider medians (robust to outliers). Backtested
with 5-fold provider-level cross-validation:
    portfolio bias ≈ 1.02x (near-unbiased)   provider-aggregate MAPE ≈ 48%
vs flat constants:
    portfolio bias wild              per-campaign MAPE ≈ 256%

Hierarchical fallback keys (most → least specific):
    cc|cat|disc|seg → cc|cat|disc → cc|cat|seg → cc|cat → cat|disc|seg → cat|disc → cat

Output: data/cost-benchmarks.json
"""

from __future__ import annotations

import glob
import json
import os
import statistics
from collections import defaultdict
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

MIN_PROVIDERS = 5          # a benchmark group needs this many distinct providers
TRIM = 0.10                # trimmed-mean fraction each tail
COST_RATE_MIN = 0.001      # 0.1% — clamp sane DI bounds
COST_RATE_MAX = 0.60       # 60%


def seg_code(s: str) -> str:
    s = (s or "").lower()
    if "enterprise" in s:
        return "E"
    if "mid" in s:
        return "M"
    if "smb" in s:
        return "S"
    return "X"


def trimmed_mean(xs: list[float], p: float = TRIM) -> float:
    xs = sorted(xs)
    n = len(xs)
    k = int(n * p)
    core = xs[k : n - k] if n - 2 * k > 0 else xs
    return statistics.mean(core)


def hier_keys(cc: str, cat: str, disc: int, seg: str) -> list[str]:
    return [
        f"{cc}|{cat}|{disc}|{seg}",
        f"{cc}|{cat}|{disc}",
        f"{cc}|{cat}|{seg}",
        f"{cc}|{cat}",
        f"{cat}|{disc}|{seg}",
        f"{cat}|{disc}",
        f"{cat}",
    ]


def collect_records() -> list[tuple]:
    """(cc, pid, cat, disc, seg, aov, actual_weekly_spend, prov_orders_wk_during_camp)."""
    records = []
    for f in sorted(glob.glob(os.path.join(DATA, "*-dash.json"))):
        cc = os.path.basename(f).replace("-dash.json", "")
        with open(f) as fh:
            d = json.load(fh)
        ch = d.get("camp_history", {})
        lk = d.get("provider_lookup", {})
        for pid, tiers in ch.items():
            pd = lk.get(pid)
            if not pd or len(pd) < 6:
                continue
            aov = pd[3]
            if aov <= 0:
                continue
            seg = seg_code(pd[4])
            for tk, h in tiers.items():
                if len(h) < 9 or h[3] < 2 or h[0] <= 0:
                    continue
                cat = tk.split("_")[0] if "_" in tk else tk
                disc = int(h[5] or 0)
                redemption = h[8] or 0
                camp_orders = h[7] or 0
                prov_orders_camp = camp_orders / redemption if redemption > 0 else 0
                if prov_orders_camp <= 0:
                    continue
                records.append((cc, pid, cat, disc, seg, aov, h[0], prov_orders_camp))
    return records


def build_levels(records: list[tuple]) -> dict:
    # key -> pid -> [cost_rate samples]
    groups: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for (cc, pid, cat, disc, seg, aov, act, poc) in records:
        cr = act / (poc * aov)
        if cr <= 0:
            continue
        cr = max(COST_RATE_MIN, min(COST_RATE_MAX, cr))
        for k in hier_keys(cc, cat, disc, seg):
            groups[k][pid].append(cr)

    levels = {}
    for k, pm in groups.items():
        if len(pm) < MIN_PROVIDERS:
            continue
        prov_vals = [statistics.median(v) for v in pm.values()]
        cr = trimmed_mean(prov_vals)
        cr = max(COST_RATE_MIN, min(COST_RATE_MAX, cr))
        levels[k] = {"cr": round(cr, 5), "n": len(pm)}
    return levels


def lookup(levels: dict, cc: str, cat: str, disc: int, seg: str):
    for i, k in enumerate(hier_keys(cc, cat, disc, seg)):
        if k in levels:
            return levels[k]["cr"], levels[k]["n"], i
    return None


def portfolio_sanity(records: list[tuple], levels: dict) -> dict:
    """In-sample sum(pred)/sum(actual). Cross-validated numbers come from validate_forecast.py."""
    tot_a = tot_p = 0.0
    covered = 0
    for (cc, pid, cat, disc, seg, aov, act, poc) in records:
        # at prediction we'd use provider current weekly orders; here poc is the in-sample proxy
        res = lookup(levels, cc, cat, disc, seg)
        tot_a += act
        if res:
            cr, _, _ = res
            tot_p += poc * aov * cr
            covered += 1
    return {
        "in_sample_portfolio_ratio": round(tot_p / tot_a, 3) if tot_a else None,
        "coverage_pct": round(covered / len(records) * 100, 1) if records else 0,
        "tiers_used": len(records),
    }


def main():
    records = collect_records()
    if not records:
        print("No dash records found — run refresh_data.py first.")
        return
    levels = build_levels(records)
    sanity = portfolio_sanity(records, levels)

    payload = {
        "refreshed": date.today().isoformat(),
        "method": "AOV-normalised discount intensity (discount€ / provider GMV); 10% trimmed mean of provider medians",
        "min_providers": MIN_PROVIDERS,
        "cost_rate_bounds": [COST_RATE_MIN, COST_RATE_MAX],
        "key_hierarchy": ["cc|cat|disc|seg", "cc|cat|disc", "cc|cat|seg", "cc|cat", "cat|disc|seg", "cat|disc", "cat"],
        "sanity": sanity,
        "levels": levels,
    }
    out = os.path.join(DATA, "cost-benchmarks.json")
    with open(out, "w") as f:
        json.dump(payload, f, separators=(",", ":"))

    size = os.path.getsize(out) / 1024
    print(f"cost-benchmarks.json: {len(levels)} benchmark groups ({size:.0f} KB)")
    print(f"  tiers used: {sanity['tiers_used']:,}  coverage: {sanity['coverage_pct']}%")
    print(f"  in-sample portfolio ratio: {sanity['in_sample_portfolio_ratio']}x")
    # show a few global category anchors
    for cat in ("md", "id", "fd", "ot"):
        if cat in levels:
            print(f"  global {cat}: DI {levels[cat]['cr']*100:.2f}%  (n={levels[cat]['n']} providers)")


if __name__ == "__main__":
    main()

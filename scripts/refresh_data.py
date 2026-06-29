#!/usr/bin/env python3
"""
Refresh Calculator & Dashboard Data from Databricks — All Countries
Pulls 12 months of Bolt Food provider + campaign data per country and generates
per-country JSON data files in data/ folder.

Usage:
    python3 scripts/refresh_data.py              # all countries
    python3 scripts/refresh_data.py mt ro cz      # specific countries only

Requires:
    - databricks-sql-connector
    - pandas
    - ~/.databricks_token or DATABRICKS_TOKEN env var
"""

import sys, os, re, json
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(__file__))
from dbx import DBX
import pandas as pd

DATA_DIR = os.path.join(ROOT, 'data')
TODAY = date.today().isoformat()
# Weekly baseline for provider orders/GMV averages (most recent N weeks, GMV > 0 only).
RECENT_WEEKS_FOR_AVG = 8

COUNTRY_NAMES = {
    'az': 'Azerbaijan', 'bg': 'Bulgaria', 'cy': 'Cyprus', 'cz': 'Czechia',
    'ee': 'Estonia', 'ge': 'Georgia', 'gh': 'Ghana', 'ke': 'Kenya',
    'lt': 'Lithuania', 'lv': 'Latvia', 'mt': 'Malta', 'pl': 'Poland',
    'pt': 'Portugal', 'ro': 'Romania', 'sk': 'Slovakia', 'ua': 'Ukraine',
}


def discover_countries(dbx):
    """Find all countries with active food campaign data in last 90 days."""
    df = dbx.query("""
        SELECT DISTINCT country
        FROM ng_public_spark.etl_delivery_campaign_order_metrics
        WHERE order_created_date >= DATE_SUB(CURRENT_DATE(), 90)
        ORDER BY country
    """)
    return sorted(df['country'].tolist())


def pull_providers(dbx, cc):
    return dbx.query(f"""
        SELECT provider_id, provider_name, vendor_id, vendor_name, brand_name, group_name,
               account_manager_name, business_segment_v2, business_subsegment_v2,
               provider_status, provider_rating, is_bolt_plus_enrolled_provider,
               regular_commission_rate
        FROM ng_delivery_spark.dim_provider_v2
        WHERE country_code = '{cc}' AND delivery_vertical = 'food'
    """)


def pull_order_stats(dbx, cc, recent_weeks=RECENT_WEEKS_FOR_AVG):
    return dbx.query(f"""
        WITH provider_weeks AS (
            SELECT
                provider_id,
                DATE_TRUNC('WEEK', order_created_date) AS week_start,
                COUNT(order_id) AS week_orders,
                SUM(COALESCE(gmv_eur, 0)) AS week_gmv
            FROM ng_public_spark.etl_delivery_order_monetary_metrics
            WHERE country = '{cc}'
              AND order_created_date >= DATE_FORMAT(DATE_SUB(CURRENT_DATE(), 365), 'yyyy-MM-dd')
              AND is_bolt_market = false
            GROUP BY provider_id, DATE_TRUNC('WEEK', order_created_date)
        ),
        ranked AS (
            SELECT
                provider_id,
                week_start,
                week_orders,
                week_gmv,
                ROW_NUMBER() OVER (
                    PARTITION BY provider_id
                    ORDER BY week_start DESC
                ) AS week_rank
            FROM provider_weeks
        ),
        recent AS (
            SELECT provider_id, week_orders, week_gmv
            FROM ranked
            WHERE week_rank <= {recent_weeks}
              AND week_gmv > 0
        )
        SELECT
            provider_id,
            SUM(week_orders) AS total_orders,
            ROUND(SUM(week_gmv), 2) AS total_gmv,
            ROUND(
                CASE WHEN SUM(week_orders) > 0 THEN SUM(week_gmv) / SUM(week_orders) ELSE 0 END,
                2
            ) AS avg_aov,
            COUNT(*) AS active_weeks
        FROM recent
        GROUP BY provider_id
    """)


def pull_campaign_spend(dbx, cc):
    return dbx.query(f"""
        SELECT
            provider_id,
            ROUND(SUM(CAST(bolt_spend AS DOUBLE)), 2) AS bolt_spend,
            ROUND(SUM(CAST(provider_spend AS DOUBLE)), 2) AS provider_spend
        FROM ng_public_spark.etl_delivery_campaign_order_metrics
        WHERE country = '{cc}'
          AND order_created_date >= DATE_SUB(CURRENT_DATE(), 365)
        GROUP BY provider_id
    """)


def pull_weekly_actuals(dbx, cc, weeks=8):
    return dbx.query(f"""
        SELECT
            provider_id,
            WEEKOFYEAR(order_created_date) AS iso_week,
            YEAR(order_created_date) AS yr,
            CASE
                WHEN LOWER(name) LIKE '%free%delivery%' THEN 'fd'
                WHEN LOWER(name) LIKE '%menu discount%' THEN 'md'
                WHEN LOWER(name) LIKE '%item%' AND spend_objective LIKE '%portal%' THEN 'id'
                ELSE 'ot'
            END AS camp_cat,
            ROUND(SUM(CAST(bolt_spend AS DOUBLE)), 2) AS bolt,
            ROUND(SUM(CAST(provider_spend AS DOUBLE)), 2) AS prov,
            ROUND(SUM(CAST(discount_value AS DOUBLE)), 2) AS total
        FROM ng_public_spark.etl_delivery_campaign_order_metrics
        WHERE country = '{cc}'
          AND order_created_date >= DATE_SUB(CURRENT_DATE(), {weeks * 7})
          AND order_created_date < DATE_TRUNC('WEEK', CURRENT_DATE())
        GROUP BY provider_id, WEEKOFYEAR(order_created_date), YEAR(order_created_date),
                 CASE
                    WHEN LOWER(name) LIKE '%free%delivery%' THEN 'fd'
                    WHEN LOWER(name) LIKE '%menu discount%' THEN 'md'
                    WHEN LOWER(name) LIKE '%item%' AND spend_objective LIKE '%portal%' THEN 'id'
                    ELSE 'ot'
                 END
    """)


def pull_camp_history(dbx, cc):
    return dbx.query(f"""
        SELECT
            provider_id,
            name,
            CASE
                WHEN LOWER(name) LIKE '%free%delivery%' THEN 'fd'
                WHEN LOWER(name) LIKE '%menu discount%' THEN 'md'
                WHEN LOWER(name) LIKE '%item%' AND spend_objective LIKE '%portal%' THEN 'id'
                ELSE 'ot'
            END AS camp_cat,
            spend_objective,
            WEEKOFYEAR(order_created_date) AS iso_week,
            YEAR(order_created_date) AS yr,
            ROUND(SUM(CAST(bolt_spend AS DOUBLE)), 2) AS bolt,
            ROUND(SUM(CAST(provider_spend AS DOUBLE)), 2) AS prov,
            ROUND(SUM(CAST(discount_value AS DOUBLE)), 2) AS total,
            COUNT(DISTINCT order_id) AS order_count,
            ROUND(AVG(CAST(discount_value AS DOUBLE)), 2) AS avg_disc
        FROM ng_public_spark.etl_delivery_campaign_order_metrics
        WHERE country = '{cc}'
          AND order_created_date >= DATE_SUB(CURRENT_DATE(), 365)
        GROUP BY provider_id, name, spend_objective,
                 WEEKOFYEAR(order_created_date), YEAR(order_created_date),
                 CASE
                    WHEN LOWER(name) LIKE '%free%delivery%' THEN 'fd'
                    WHEN LOWER(name) LIKE '%menu discount%' THEN 'md'
                    WHEN LOWER(name) LIKE '%item%' AND spend_objective LIKE '%portal%' THEN 'id'
                    ELSE 'ot'
                 END
    """)


def pull_provider_weekly_orders(dbx, cc):
    """Provider total orders per week — denominator for historical redemption rate."""
    return dbx.query(f"""
        SELECT
            provider_id,
            WEEKOFYEAR(order_created_date) AS iso_week,
            YEAR(order_created_date) AS yr,
            COUNT(DISTINCT order_id) AS total_orders
        FROM ng_public_spark.etl_delivery_order_monetary_metrics
        WHERE country = '{cc}'
          AND order_created_date >= DATE_SUB(CURRENT_DATE(), 365)
          AND is_bolt_market = false
        GROUP BY provider_id, WEEKOFYEAR(order_created_date), YEAR(order_created_date)
    """)


def seg_code(s):
    if pd.isna(s):
        return 'S'
    s = str(s).lower()
    if 'enterprise' in s:
        return 'E'
    if 'mid' in s:
        return 'M'
    return 'S'


def _extract_disc_pct(name, cat):
    """Extract discount percentage from campaign name."""
    if cat == 'fd':
        return 0
    name_l = str(name).lower()
    patterns = [
        r'(\d+)\s*%\s*%?\s*(?:menu discount|item discount)',
        r'(?:md|menu discount|item discount)\s+(\d+)\s*%',
        r'(\d+)\s*pc[_-](?:menu|item)',
        r'(\d+)\s*%\s+(?:on|off|discount)',
        r'(?:discount|disc)\s*(\d+)\s*%',
    ]
    for pat in patterns:
        m = re.search(pat, name_l)
        if m:
            return int(m.group(1))
    return 0


def _extract_cost_share(name):
    m = re.search(r'(\d+)\s*%\s*on\s*provider', str(name).lower())
    return int(m.group(1)) if m else -1


def build_calc_data(providers, orders, camp_spend):
    merged = providers.merge(orders, on='provider_id', how='left')
    merged = merged.merge(camp_spend, on='provider_id', how='left')
    for col in ['bolt_spend', 'provider_spend', 'total_gmv', 'avg_aov', 'active_weeks']:
        merged[col] = merged[col].fillna(0)
    merged['total_orders'] = merged['total_orders'].fillna(0).astype(int)
    merged['seg_code'] = merged['business_segment_v2'].apply(seg_code)

    active = merged[(merged['total_orders'] > 0) | (merged['provider_status'] == 'active')].copy()
    active = active.sort_values('total_gmv', ascending=False)

    rows = []
    for _, r in active.iterrows():
        name = str(r['provider_name']) if pd.notna(r['provider_name']) else ''
        vid = int(r['vendor_id']) if pd.notna(r['vendor_id']) else int(r['provider_id'])
        vname = str(r['vendor_name']) if pd.notna(r['vendor_name']) else ''
        gname = str(r['group_name']) if pd.notna(r['group_name']) else ''
        comm = round(float(r['regular_commission_rate']), 1) if pd.notna(r['regular_commission_rate']) else 0
        rows.append([
            name, int(r['provider_id']), r['seg_code'],
            int(r['total_orders']), round(float(r['total_gmv']), 2),
            round(float(r['avg_aov']), 2), comm,
            int(round(float(r['bolt_spend']), 0)),
            int(round(float(r['provider_spend']), 0)),
            vid, vname, gname,
            int(r['active_weeks']) if pd.notna(r['active_weeks']) else 0
        ])
    return rows


def build_actuals_data(actuals_df):
    data = {}
    for _, r in actuals_df.iterrows():
        wk = f"{int(r['yr'])}-W{int(r['iso_week'])}"
        pid = str(int(r['provider_id']))
        cat = r['camp_cat']
        data.setdefault(wk, {}).setdefault(pid, {}).setdefault(cat, [0.0, 0.0, 0.0])
        data[wk][pid][cat][0] += round(r['bolt'], 2)
        data[wk][pid][cat][1] += round(r['prov'], 2)
        data[wk][pid][cat][2] += round(r['total'], 2)
    return data


def build_provider_lookup(providers, orders):
    merged = providers.merge(orders, on='provider_id', how='left')
    merged['total_orders'] = merged['total_orders'].fillna(0).astype(int)
    merged['total_gmv'] = merged['total_gmv'].fillna(0)
    merged['avg_aov'] = merged['avg_aov'].fillna(0)
    merged['active_weeks'] = merged['active_weeks'].fillna(0)

    lookup = {}
    for _, r in merged.iterrows():
        pid = str(int(r['provider_id']))
        am = str(r['account_manager_name']) if pd.notna(r['account_manager_name']) else 'Unknown'
        seg = str(r['business_segment_v2']) if pd.notna(r['business_segment_v2']) else 'SMB'
        lookup[pid] = [am, int(r['total_orders']), round(float(r['total_gmv']), 2),
                       round(float(r['avg_aov']), 2), seg,
                       int(r['active_weeks']) if pd.notna(r['active_weeks']) else 0]
    return lookup


def build_camp_history(hist_df, provider_weekly_orders_df=None):
    prov_weekly = {}
    if provider_weekly_orders_df is not None:
        for _, r in provider_weekly_orders_df.iterrows():
            pid = str(int(r['provider_id']))
            wk = f"{int(r['yr'])}-W{int(r['iso_week'])}"
            prov_weekly.setdefault(pid, {})[wk] = int(r['total_orders'])

    history = {}
    for _, r in hist_df.iterrows():
        pid = str(int(r['provider_id']))
        cat = r['camp_cat']
        disc_pct = _extract_disc_pct(r['name'], cat)
        cost_share = _extract_cost_share(r['name'])
        wk = f"{int(r['yr'])}-W{int(r['iso_week'])}"

        tier_key = f"{cat}_{disc_pct}" if disc_pct > 0 else cat

        history.setdefault(pid, {}).setdefault(tier_key, {}).setdefault(wk, {
            'bolt': 0, 'prov': 0, 'total': 0, 'orders': 0, 'disc_sum': 0, 'disc_pct': disc_pct, 'cost_share': cost_share
        })
        entry = history[pid][tier_key][wk]
        entry['bolt'] += float(r['bolt'])
        entry['prov'] += float(r['prov'])
        entry['total'] += float(r['total'])
        entry['orders'] += int(r['order_count'])
        entry['disc_sum'] += float(r['avg_disc']) * int(r['order_count'])

    result = {}
    for pid, cats in history.items():
        result[pid] = {}
        for tier_key, weeks in cats.items():
            n_weeks = len(weeks)
            total_bolt = sum(w['bolt'] for w in weeks.values())
            total_prov = sum(w['prov'] for w in weeks.values())
            total_total = sum(w['total'] for w in weeks.values())
            total_orders = sum(w['orders'] for w in weeks.values())
            total_disc_sum = sum(w['disc_sum'] for w in weeks.values())
            disc_pct = next(iter(weeks.values()))['disc_pct']
            cost_share = next(iter(weeks.values()))['cost_share']

            avg_disc_per_order = round(total_disc_sum / total_orders, 2) if total_orders else 0
            prov_orders_in_camp_weeks = sum(
                prov_weekly.get(pid, {}).get(wk, 0) for wk in weeks.keys()
            )
            redemption_rate = (
                round(total_orders / prov_orders_in_camp_weeks, 4)
                if prov_orders_in_camp_weeks > 0 else 0.0
            )
            camp_orders_wk = round(total_orders / n_weeks, 2) if n_weeks else 0.0
            result[pid][tier_key] = [
                round(total_total / n_weeks, 2),
                round(total_bolt / n_weeks, 2),
                round(total_prov / n_weeks, 2),
                n_weeks,
                avg_disc_per_order,
                disc_pct,
                cost_share,
                camp_orders_wk,
                redemption_rate,
            ]
    return result


def process_country(dbx, cc):
    """Pull all data for one country and write JSON files."""
    print(f"\n{'='*50}")
    print(f"  Processing {cc.upper()} ({COUNTRY_NAMES.get(cc, cc)})")
    print(f"{'='*50}")

    print(f"  [{cc}] Pulling providers...")
    providers = pull_providers(dbx, cc)
    print(f"  [{cc}] Pulling order stats (12 months)...")
    orders = pull_order_stats(dbx, cc)
    print(f"  [{cc}] Pulling campaign spend (12 months)...")
    camp_spend = pull_campaign_spend(dbx, cc)
    print(f"  [{cc}] Pulling weekly actuals (8 weeks)...")
    weekly_actuals = pull_weekly_actuals(dbx, cc)
    print(f"  [{cc}] Pulling campaign history (12 months)...")
    camp_hist_raw = pull_camp_history(dbx, cc)
    print(f"  [{cc}] Pulling provider weekly orders (redemption denominators)...")
    provider_weekly_orders = pull_provider_weekly_orders(dbx, cc)

    print(f"  [{cc}] Providers: {len(providers)}, Orders: {len(orders)}, Campaigns: {len(camp_spend)}")

    calc_data = build_calc_data(providers, orders, camp_spend)
    actuals_data = build_actuals_data(weekly_actuals)
    lookup_data = build_provider_lookup(providers, orders)
    camp_history = build_camp_history(camp_hist_raw, provider_weekly_orders)

    calc_json = {
        'country': cc,
        'country_name': COUNTRY_NAMES.get(cc, cc),
        'refreshed': TODAY,
        'weeks': RECENT_WEEKS_FOR_AVG,
        'embedded_data': calc_data,
    }

    dash_json = {
        'country': cc,
        'country_name': COUNTRY_NAMES.get(cc, cc),
        'refreshed': TODAY,
        'weeks_in_data': RECENT_WEEKS_FOR_AVG,
        'camp_history': camp_history,
        'dbx_actuals': actuals_data,
        'provider_lookup': lookup_data,
    }

    os.makedirs(DATA_DIR, exist_ok=True)
    calc_path = os.path.join(DATA_DIR, f'{cc}-calc.json')
    dash_path = os.path.join(DATA_DIR, f'{cc}-dash.json')

    with open(calc_path, 'w') as f:
        json.dump(calc_json, f, separators=(',', ':'))
    with open(dash_path, 'w') as f:
        json.dump(dash_json, f, separators=(',', ':'))

    calc_size = os.path.getsize(calc_path) / 1024
    dash_size = os.path.getsize(dash_path) / 1024

    print(f"  [{cc}] Calculator: {len(calc_data)} providers ({calc_size:.0f} KB)")
    print(f"  [{cc}] Dashboard: {len(lookup_data)} providers, {len(actuals_data)} weeks actuals ({dash_size:.0f} KB)")
    return len(calc_data), len(lookup_data)


def generate_country_index():
    """Write data/countries.json with list of available countries."""
    index = {}
    for cc, name in sorted(COUNTRY_NAMES.items()):
        calc_path = os.path.join(DATA_DIR, f'{cc}-calc.json')
        dash_path = os.path.join(DATA_DIR, f'{cc}-dash.json')
        if not (os.path.exists(calc_path) and os.path.exists(dash_path)):
            continue
        refreshed = TODAY
        try:
            with open(calc_path) as f:
                refreshed = json.load(f).get('refreshed') or refreshed
        except (OSError, json.JSONDecodeError):
            pass
        index[cc] = {'name': name, 'refreshed': refreshed}

    with open(os.path.join(DATA_DIR, 'countries.json'), 'w') as f:
        json.dump(index, f, indent=2)
    return index


def main():
    requested = [c.lower() for c in sys.argv[1:]] if len(sys.argv) > 1 else None

    print(f"Bolt Food Campaign Data Refresh — {TODAY}")
    print("Connecting to Databricks...")

    if requested:
        countries = requested
        print(f"  Refreshing specific countries: {', '.join(c.upper() for c in countries)}")
    else:
        with DBX() as dbx:
            countries = discover_countries(dbx)
        print(f"  Discovered {len(countries)} countries: {', '.join(c.upper() for c in countries)}")

    summary = {}
    for cc in countries:
        try:
            with DBX() as dbx:
                n_calc, n_dash = process_country(dbx, cc)
            summary[cc] = (n_calc, n_dash)
        except Exception as e:
            print(f"  [{cc}] ERROR: {e}")
            import traceback; traceback.print_exc()
            summary[cc] = None

    index = generate_country_index()

    # Rebuild empirical peer cost-rate benchmarks (cold-start / no-history path).
    try:
        import build_cost_benchmarks
        print("\n  Rebuilding cost-rate benchmarks (cold-start path)...")
        build_cost_benchmarks.main()
    except Exception as e:
        print(f"  WARN: could not rebuild cost benchmarks: {e}")

    print(f"\n{'='*50}")
    print(f"  SUMMARY")
    print(f"{'='*50}")
    for cc, result in summary.items():
        if result:
            print(f"  {cc.upper():>4}: {result[0]:>5} calc providers, {result[1]:>5} dash providers")
        else:
            print(f"  {cc.upper():>4}: FAILED")
    print(f"\n  {len(index)} countries in index")
    print(f"  Data files in: {DATA_DIR}")
    print(f"  Next step: git add data/ && git commit && git push")


if __name__ == '__main__':
    main()

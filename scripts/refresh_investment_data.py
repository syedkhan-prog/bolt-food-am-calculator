#!/usr/bin/env python3
"""
Refresh Investment Dashboard data from Databricks.

Queries campaign spend per country (12 weeks), saves per-country CSVs
to investment-data/, and updates countries.json.

Runs standalone or via GitHub Actions.
"""

import sys, os, json, time
from datetime import date

sys.path.insert(0, os.path.dirname(__file__))
from dbx import DBX
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, 'investment-data')
WEEKS_BACK = 84

COUNTRY_LIST_QUERY = """
SELECT UPPER(country) AS code,
       COUNT(DISTINCT provider_id) AS providers,
       ROUND(SUM(CAST(bolt_spend AS DOUBLE)), 0) AS bolt_spend,
       ROUND(SUM(CAST(provider_spend AS DOUBLE)), 0) AS provider_spend
FROM main.ng_public.etl_delivery_campaign_order_metrics
WHERE order_created_date >= DATE_SUB(CURRENT_DATE(), {weeks_back})
GROUP BY 1
ORDER BY providers DESC
"""

COUNTRY_DATA_QUERY = """
SELECT
    CONCAT(
        YEAR(c.order_created_date), '-W',
        LPAD(WEEKOFYEAR(c.order_created_date), 2, '0')
    ) AS weeks,
    UPPER(c.country) AS Country,
    COALESCE(ct.city_name, 'Unknown') AS City,
    COALESCE(p.account_manager_name, 'Unassigned') AS account_manager_name,
    COALESCE(REPLACE(p.business_segment_v2, ' (AM Segment)', ''), 'Unknown') AS business_segment,
    COALESCE(c.spend_objective, 'unknown') AS spend_objective,
    COALESCE(p.brand_name, '') AS brand_name,
    c.provider_id,
    COALESCE(p.vendor_id, c.provider_id) AS vendor_id,
    COALESCE(p.vendor_name, p.provider_name, '') AS vendor_name,
    ROUND(SUM(CAST(c.bolt_spend AS DOUBLE)), 2) AS bolt_spend,
    ROUND(SUM(CAST(c.provider_spend AS DOUBLE)), 2) AS provider_spend
FROM main.ng_public.etl_delivery_campaign_order_metrics c
JOIN main.ng_delivery.dim_provider_v2 p
    ON c.provider_id = p.provider_id
LEFT JOIN main.ng_delivery.dim_delivery_city ct
    ON c.city_id = ct.city_id
WHERE c.country = LOWER('{country}')
  AND c.order_created_date >= DATE_SUB(CURRENT_DATE(), {weeks_back})
GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9, 10
"""


def main():
    today = date.today().isoformat()
    os.makedirs(DATA_DIR, exist_ok=True)

    print(f"Refreshing investment dashboard data ({today})")

    with DBX() as dbx:
        print("  Fetching country list...")
        countries_df = dbx.query(
            COUNTRY_LIST_QUERY.format(weeks_back=WEEKS_BACK)
        )
        countries = countries_df.to_dict('records')
        print(f"  Found {len(countries)} countries")

        country_info = []
        for c in countries:
            code = c['code']
            print(f"  Fetching {code}...", end=' ', flush=True)
            t0 = time.time()
            df = dbx.query(
                COUNTRY_DATA_QUERY.format(country=code, weeks_back=WEEKS_BACK)
            )
            elapsed = time.time() - t0
            print(f"{len(df):,} rows in {elapsed:.0f}s")

            path = os.path.join(DATA_DIR, f'{code}.csv')
            df.to_csv(path, index=False)

            country_info.append({
                'code': code,
                'providers': int(c['providers']),
                'bolt_spend': float(c['bolt_spend']),
                'provider_spend': float(c['provider_spend']),
                'cached': True,
            })

    meta = {'countries': country_info, 'refreshed': today}
    with open(os.path.join(DATA_DIR, 'countries.json'), 'w') as f:
        json.dump(meta, f, separators=(',', ':'))

    print(f"\nDone. {len(countries)} countries saved to {DATA_DIR}/")
    print(f"Refreshed: {today}")


if __name__ == '__main__':
    main()

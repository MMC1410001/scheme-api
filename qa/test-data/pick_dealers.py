"""Pick a few real dealers per month from scheme_records and write qa/test-data/dealers.json.

    APP_ENV=dev uv run python qa/test-data/pick_dealers.py                  # DB from .env.dev
    APP_ENV=dev uv run python qa/test-data/pick_dealers.py --months 2026-09 2026-10 --count 5

Only reads the database. dealers.json holds real customer codes (client data): it is gitignored,
so share it with QA through the team's secret store, never in the repo or a chat. Dev, UAT and
local all load the same SAP files from GCS, so the same codes work on every environment.

Per month, the first code is the main test dealer: the most components, each from a single
agreement (API1_TC_14 expects every component once). The rest add variety: a dealer with a
negative amount, one with only invoice-quantity components (Luxury/Premium), then others.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

OUT = Path(__file__).resolve().parent / "dealers.json"
sys.path.insert(0, str(OUT.parents[2]))  # run as a file, so make the app package importable

from app.config import APP_ENV, get_settings  # noqa: E402

# One row per dealer in the month, with the facts used to choose between them.
DEALERS_SQL = """
SELECT dealer_code,
       count(DISTINCT component)                                    AS components,
       count(*) = count(DISTINCT component)                         AS single_agreement,
       bool_or(least(qualification, gsr_b, clear_cut, shortfall_amount) < 0) AS has_negative,
       bool_and(qualification_basis = 'INVOICE_QTY')                AS invoice_qty_only
FROM scheme_records
WHERE year = %s AND month = %s
GROUP BY dealer_code
"""


def pick(rows: list[dict], count: int) -> list[str]:
    clean = sorted((r for r in rows if r["single_agreement"]), key=lambda r: (-r["components"], r["dealer_code"]))
    chosen: list[str] = []

    def add(candidates):
        for r in candidates:
            if r["dealer_code"] not in chosen:
                chosen.append(r["dealer_code"])
                return

    add(clean)                                          # main dealer: most components
    add(r for r in clean if r["has_negative"])          # SAP trailing-minus values
    add(r for r in clean if r["invoice_qty_only"])      # Luxury/Premium only
    # Fill up with dealers spread across the list (not just the top), for variety.
    step = max(1, len(clean) // max(1, count))
    for r in clean[::step]:
        if len(chosen) >= count:
            break
        add([r])
    return chosen[:count]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--months", nargs="+", default=["2026-09", "2026-10"], help="YYYY-MM")
    parser.add_argument("--count", type=int, default=5, help="dealers per month")
    args = parser.parse_args()

    months = {}
    with psycopg.connect(get_settings().database_url, connect_timeout=10, row_factory=dict_row) as conn:
        conn.read_only = True
        for ym in args.months:
            year, month = (int(p) for p in ym.split("-"))
            rows = conn.execute(DEALERS_SQL, (year, month)).fetchall()
            months[ym] = pick(rows, args.count)
            print(f"{ym}: {len(rows)} dealers in the DB, picked {len(months[ym])}")

    OUT.write_text(json.dumps({
        "_about": "Real dealer codes for QA (client data, gitignored). Built by pick_dealers.py; first code per month is the main test dealer.",
        "source_env": APP_ENV,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "months": months,
    }, indent=2) + "\n")
    print("wrote", OUT)


if __name__ == "__main__":
    main()

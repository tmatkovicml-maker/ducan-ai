"""Command-line entry point for running jobs manually.

python -m ducan.jobs.cli seed
python -m ducan.jobs.cli import-sales
python -m ducan.jobs.cli check-stock
python -m ducan.jobs.cli import-assortment path/to/P4_ducan_model.xlsx
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ducan.config import get_config
from ducan.db import make_engine, make_sessionmaker
from ducan.importers.assortment_xlsx import merge_into_csv
from ducan.importers.seed import SeedError, seed_database
from ducan.jobs.tasks import check_stock, import_sales

SEED_DIR = Path("seed")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ducan")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("seed", help="Load seed/suppliers.csv and seed/products.csv")
    sub.add_parser("import-sales", help="Import CSV files from the POS inbox")
    sub.add_parser("check-stock", help="Check stock levels against movements")
    xlsx = sub.add_parser("import-assortment", help="Merge the 'Asortiman' sheet into products.csv")
    xlsx.add_argument("xlsx", type=Path)
    args = parser.parse_args(argv)

    if args.command == "import-assortment":
        updated, added = merge_into_csv(args.xlsx, SEED_DIR / "products.csv")
        print(f"products.csv: {updated} izmijenjeno, {added} dodano. Pokreni 'make seed'.")
        return 0

    config = get_config()
    factory = make_sessionmaker(make_engine(config.env.database_url))
    if args.command == "seed":
        with factory() as session:
            try:
                suppliers, products = seed_database(session, SEED_DIR, config.business.vat_rates)
            except SeedError as exc:
                print(exc, file=sys.stderr)
                return 1
            session.commit()
        print(f"Učitano: {suppliers} dobavljača, {products} proizvoda.")
    elif args.command == "import-sales":
        for outcome in import_sales(config, factory):
            print(
                f"{outcome.name}: {outcome.status} ({outcome.rows_ok} ok, "
                f"{outcome.rows_rejected} odbijeno) {outcome.message}"
            )
    elif args.command == "check-stock":
        issues = check_stock(factory)
        for issue in issues:
            print(f"{issue.sku}: stanje {issue.level}, zbroj kretanja {issue.movements_sum}")
        print("U redu." if not issues else f"{len(issues)} neusklađenih.")
        return 1 if issues else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

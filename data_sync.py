"""Excel → PostgreSQL data synchronization script.

Usage:
    python data_sync.py path/to/file.xlsx
    python data_sync.py path/to/folder/       (syncs all .xlsx files)

Normalizes column names to lowercase with underscores,
then upserts each sheet/file into a PostgreSQL table.
"""

import os
import re
import sys

import pandas as pd
from sqlalchemy import text

from db.connection import get_engine


def normalize_column(name: str) -> str:
    """Lowercase, strip, and replace non-alphanumeric chars with underscore."""
    name = str(name).strip().lower()
    name = re.sub(r"[^a-z0-9]+", "_", name)
    name = name.strip("_")
    return name or "unnamed_col"


def sync_dataframe(df: pd.DataFrame, table_name: str) -> None:
    """Write a DataFrame to PostgreSQL, replacing the data but preserving the schema.

    Uses TRUNCATE + append inside a transaction instead of if_exists="replace"
    so that existing indexes, foreign keys, and constraints are not destroyed.
    Falls back to if_exists="replace" only when the table does not yet exist.
    """
    engine = get_engine()

    # Normalize columns
    df.columns = [normalize_column(c) for c in df.columns]

    # Deduplicate column names — first occurrence keeps the name,
    # subsequent duplicates get a _2, _3, ... suffix.
    seen: dict[str, int] = {}
    new_cols: list[str] = []
    for col in df.columns:
        if col in seen:
            seen[col] += 1
            new_cols.append(f"{col}_{seen[col]}")
        else:
            seen[col] = 1
            new_cols.append(col)
    df.columns = new_cols

    with engine.begin() as conn:
        # Check whether the table already exists
        exists = conn.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name=:t"
            ),
            {"t": table_name},
        ).fetchone()

        if exists:
            # Preserve schema — truncate rows only, then append new data
            conn.execute(text(f'TRUNCATE TABLE "{table_name}" RESTART IDENTITY CASCADE'))
            df.to_sql(table_name, conn, if_exists="append", index=False)
        else:
            # Table doesn't exist yet — safe to create from scratch
            df.to_sql(table_name, conn, if_exists="replace", index=False)

    # Use plain ASCII characters to avoid Windows console encoding issues
    print(f"  Table '{table_name}' synced - {len(df)} rows, {len(df.columns)} columns")


def sync_excel(filepath: str) -> None:
    """Sync all sheets in an Excel file to separate tables."""
    basename = os.path.splitext(os.path.basename(filepath))[0]
    table_name = normalize_column(basename)

    xls = pd.ExcelFile(filepath)
    sheets = xls.sheet_names

    if len(sheets) == 1:
        df = pd.read_excel(filepath, sheet_name=sheets[0])
        sync_dataframe(df, table_name)
    else:
        for sheet in sheets:
            df = pd.read_excel(filepath, sheet_name=sheet)
            sheet_table = f"{table_name}_{normalize_column(sheet)}"
            sync_dataframe(df, sheet_table)


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python data_sync.py <path_to_xlsx_or_folder>")
        sys.exit(1)

    target = sys.argv[1]

    if os.path.isdir(target):
        files = [
            os.path.join(target, f)
            for f in os.listdir(target)
            if f.endswith((".xlsx", ".xls"))
        ]
        if not files:
            print(f"No Excel files found in {target}")
            sys.exit(1)
        for fp in sorted(files):
            print(f"Syncing: {fp}")
            sync_excel(fp)
    elif os.path.isfile(target):
        print(f"Syncing: {target}")
        sync_excel(target)
    else:
        print(f"Path not found: {target}")
        sys.exit(1)

    print("\nData sync complete.")


if __name__ == "__main__":
    main()

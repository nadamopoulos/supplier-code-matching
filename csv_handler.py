import csv
import sys
from typing import List, Tuple

from models import SupplierRecord, LookupEntry, MatchResult, MatchMethod
import config


def load_csv(file_path: str) -> Tuple[List[str], List[dict]]:
    """Load a CSV file with encoding fallback. Returns (headers, rows)."""
    encodings = ["utf-8-sig", "utf-8", "latin-1"]
    for enc in encodings:
        try:
            with open(file_path, "r", encoding=enc, newline="") as f:
                reader = csv.DictReader(f)
                headers = reader.fieldnames
                if not headers:
                    raise ValueError(f"CSV file has no headers: {file_path}")
                rows = list(reader)
                if not rows:
                    raise ValueError(f"CSV file has no data rows: {file_path}")
                return headers, rows
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Unable to decode CSV file with any supported encoding: {file_path}")


def display_columns(headers: List[str]) -> None:
    """Print a numbered list of columns."""
    print("\n  Available columns:")
    for i, col in enumerate(headers, 1):
        print(f"    {i}. {col}")
    print()


def get_column_choice(headers: List[str], prompt_text: str) -> str:
    """Prompt user for a column by name or number. Returns the actual header string."""
    while True:
        choice = input(prompt_text).strip()
        if not choice:
            continue

        # Try as number
        try:
            idx = int(choice)
            if 1 <= idx <= len(headers):
                return headers[idx - 1]
            else:
                print(f"  Number out of range. Enter 1-{len(headers)}.")
                continue
        except ValueError:
            pass

        # Try as name (case-insensitive)
        lower_choice = choice.lower()
        for h in headers:
            if h.lower() == lower_choice:
                return h

        print(f"  Column '{choice}' not found. Available columns:")
        for i, col in enumerate(headers, 1):
            print(f"    {i}. {col}")
        print()


def extract_supplier_records(
    rows: List[dict], id_col: str, name_col: str
) -> List[SupplierRecord]:
    """Extract SupplierRecord objects from CSV1 rows."""
    records = []
    for row in rows:
        uid = row.get(id_col, "").strip()
        name = row.get(name_col, "").strip()
        if uid and name:
            records.append(SupplierRecord(unique_id=uid, supplier_name=name, raw_row=row))
        elif uid:
            print(f"  Warning: Row with ID '{uid}' has empty supplier name, skipping.", file=sys.stderr)
    return records


def extract_lookup_entries(
    rows: List[dict], name_col: str, code_col: str
) -> List[LookupEntry]:
    """Extract LookupEntry objects from CSV2 rows."""
    entries = []
    for row in rows:
        name = row.get(name_col, "").strip()
        code = row.get(code_col, "").strip()
        if name and code:
            entries.append(LookupEntry(supplier_name=name, supplier_code=code))
        elif name:
            print(f"  Warning: Lookup entry '{name}' has empty supplier code, skipping.", file=sys.stderr)
    return entries


def write_output_csv(results: List[MatchResult], output_path: str) -> None:
    """Write MatchResult objects to output CSV."""
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(config.OUTPUT_COLUMNS)
        for r in results:
            writer.writerow([
                r.unique_id,
                r.supplier_name,
                r.matched_supplier_name or "",
                r.supplier_code or "",
                r.match_method.value,
                f"{r.confidence:.2f}",
            ])

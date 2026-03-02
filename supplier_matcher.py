#!/usr/bin/env python3
"""
Supplier Name Matching Tool

Matches supplier names from a source CSV to a lookup CSV using:
  Phase 1: Exact matching (after normalization)
  Phase 2: LLM fuzzy matching (Claude API)
"""

import math
import os
import sys

# Ensure the script's directory is on the import path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models import MatchResult, MatchMethod
import config
from csv_handler import (
    load_csv,
    display_columns,
    get_column_choice,
    extract_supplier_records,
    extract_lookup_entries,
    write_output_csv,
)
from exact_matcher import build_lookup_index, exact_match
from llm_matcher import create_client, llm_match_batch


def print_banner():
    print("=" * 60)
    print("  Supplier Name Matching Tool")
    print("  Match supplier names from a source CSV to a lookup CSV")
    print("=" * 60)
    print()


def print_progress_bar(completed: int, total: int, phase: str, width: int = 30):
    """Print a progress bar that overwrites the current line."""
    if total == 0:
        return
    pct = completed / total
    filled = int(width * pct)
    bar = "=" * filled + ">" * (1 if filled < width else 0) + " " * (width - filled - 1)
    sys.stdout.write(f"\r  [{bar}] {completed}/{total}  ({phase})")
    if completed == total:
        sys.stdout.write("\n")
    sys.stdout.flush()


def get_file_path(prompt_text: str) -> str:
    """Prompt user for a file path, validate it exists."""
    while True:
        path = input(prompt_text).strip()
        # Remove quotes that may come from drag-and-drop
        path = path.strip("'\"")
        if not path:
            continue
        path = os.path.expanduser(path)
        if os.path.isfile(path):
            return os.path.abspath(path)
        print(f"  File not found: {path}")


def get_output_path(prompt_text: str, default: str) -> str:
    """Prompt user for output path with a default."""
    path = input(prompt_text).strip()
    path = path.strip("'\"")
    if not path:
        path = default
    path = os.path.expanduser(path)
    return os.path.abspath(path)


def print_summary(
    total: int,
    exact_count: int,
    llm_count: int,
    no_match_count: int,
    llm_results: list,
):
    """Print final matching summary."""
    print("\n--- RESULTS ---")
    print(f"  Total records:       {total:>6,}")
    print(
        f"  Exact matches:       {exact_count:>6,}  "
        f"({exact_count / total * 100:>5.1f}%)  confidence: 1.00"
    )
    if llm_count > 0:
        avg_conf = sum(
            r.confidence for r in llm_results if r.match_method == MatchMethod.LLM
        ) / llm_count
        print(
            f"  LLM matches:         {llm_count:>6,}  "
            f"({llm_count / total * 100:>5.1f}%)  avg confidence: {avg_conf:.2f}"
        )
    else:
        print(f"  LLM matches:         {llm_count:>6,}  (  0.0%)")
    print(
        f"  No match found:      {no_match_count:>6,}  "
        f"({no_match_count / total * 100:>5.1f}%)"
    )


def main():
    print_banner()

    # --- CSV1 (Source) ---
    print("--- SOURCE CSV (CSV1) ---")
    csv1_path = get_file_path("Enter path to source CSV file: ")
    try:
        csv1_headers, csv1_rows = load_csv(csv1_path)
    except ValueError as e:
        print(f"  Error: {e}")
        sys.exit(1)
    print(f"  Loaded: {len(csv1_rows):,} rows, {len(csv1_headers)} columns")
    display_columns(csv1_headers)

    id_col = get_column_choice(csv1_headers, "Select the UNIQUE ID column (name or number): ")
    name_col_src = get_column_choice(csv1_headers, "Select the SUPPLIER NAME column (name or number): ")
    print(f"  -> Using ID: '{id_col}', Supplier Name: '{name_col_src}'")

    # --- CSV2 (Lookup) ---
    print("\n--- LOOKUP CSV (CSV2) ---")
    csv2_path = get_file_path("Enter path to lookup CSV file: ")
    try:
        csv2_headers, csv2_rows = load_csv(csv2_path)
    except ValueError as e:
        print(f"  Error: {e}")
        sys.exit(1)
    print(f"  Loaded: {len(csv2_rows):,} rows, {len(csv2_headers)} columns")
    display_columns(csv2_headers)

    name_col_lookup = get_column_choice(csv2_headers, "Select the SUPPLIER NAME column (name or number): ")
    code_col = get_column_choice(csv2_headers, "Select the SUPPLIER CODE column (name or number): ")
    print(f"  -> Using Supplier Name: '{name_col_lookup}', Supplier Code: '{code_col}'")

    # --- Output ---
    print("\n--- OUTPUT ---")
    default_output = os.path.join(os.path.dirname(csv1_path), "matched_output.csv")
    output_path = get_output_path(
        f"Enter output file path [{default_output}]: ", default_output
    )

    # --- Extract data ---
    records = extract_supplier_records(csv1_rows, id_col, name_col_src)
    lookup_entries = extract_lookup_entries(csv2_rows, name_col_lookup, code_col)

    if not records:
        print("  Error: No valid source records found.")
        sys.exit(1)
    if not lookup_entries:
        print("  Error: No valid lookup entries found.")
        sys.exit(1)

    print(f"\n  Source records: {len(records):,}")
    print(f"  Lookup entries: {len(lookup_entries):,}")

    # --- Phase 1: Exact Matching ---
    print("\n--- PHASE 1: Exact Matching ---")
    lookup_index = build_lookup_index(lookup_entries)
    exact_results, unmatched = exact_match(records, lookup_index)

    print(f"  Exact matches: {len(exact_results):,} ({len(exact_results) / len(records) * 100:.1f}%)")
    print(f"  Remaining unmatched: {len(unmatched):,}")

    # --- Phase 2: LLM Fuzzy Matching ---
    llm_results = []
    if unmatched:
        api_key = os.environ.get(config.ANTHROPIC_API_KEY_ENV)
        if not api_key:
            print(
                f"\n  {config.ANTHROPIC_API_KEY_ENV} is not set."
                f"\n  Skipping Phase 2 (LLM matching)."
                f"\n  Set it with: export {config.ANTHROPIC_API_KEY_ENV}=sk-ant-..."
                f"\n  Writing Phase 1 results only.\n"
            )
            # Create no_match results for unmatched
            for rec in unmatched:
                llm_results.append(
                    MatchResult(
                        unique_id=rec.unique_id,
                        supplier_name=rec.supplier_name,
                        matched_supplier_name=None,
                        supplier_code=None,
                        match_method=MatchMethod.NONE,
                        confidence=0.0,
                    )
                )
        else:
            num_batches = math.ceil(len(unmatched) / config.LLM_BATCH_SIZE)
            print(f"\n--- PHASE 2: LLM Fuzzy Matching ---")
            print(f"  Using model: {config.MODEL_ID}")
            print(f"  Batch size: {config.LLM_BATCH_SIZE} | Estimated API calls: {num_batches}")
            print()

            try:
                client = create_client()

                def on_progress(completed, total):
                    print_progress_bar(completed, total, "LLM matching")

                llm_results = llm_match_batch(
                    client, unmatched, lookup_entries, on_progress
                )
            except RuntimeError as e:
                print(f"\n  Error: {e}")
                print("  Writing Phase 1 results only.\n")
                for rec in unmatched:
                    llm_results.append(
                        MatchResult(
                            unique_id=rec.unique_id,
                            supplier_name=rec.supplier_name,
                            matched_supplier_name=None,
                            supplier_code=None,
                            match_method=MatchMethod.NONE,
                            confidence=0.0,
                        )
                    )
            except Exception as e:
                print(f"\n  Unexpected error during LLM matching: {e}")
                print("  Writing Phase 1 results only.\n")
                for rec in unmatched:
                    llm_results.append(
                        MatchResult(
                            unique_id=rec.unique_id,
                            supplier_name=rec.supplier_name,
                            matched_supplier_name=None,
                            supplier_code=None,
                            match_method=MatchMethod.NONE,
                            confidence=0.0,
                        )
                    )

    # --- Combine and write output ---
    # Build a map from unique_id to result, preserving original order
    result_map = {}
    for r in exact_results:
        result_map[r.unique_id] = r
    for r in llm_results:
        result_map[r.unique_id] = r

    # Output in original CSV1 order
    all_results = [result_map[rec.unique_id] for rec in records if rec.unique_id in result_map]

    write_output_csv(all_results, output_path)

    # --- Summary ---
    exact_count = sum(1 for r in all_results if r.match_method == MatchMethod.EXACT)
    llm_count = sum(1 for r in all_results if r.match_method == MatchMethod.LLM)
    no_match_count = sum(1 for r in all_results if r.match_method == MatchMethod.NONE)

    print_summary(len(all_results), exact_count, llm_count, no_match_count, all_results)
    print(f"\n  Output written to: {output_path}")
    print()


if __name__ == "__main__":
    main()

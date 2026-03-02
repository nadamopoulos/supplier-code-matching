import re
import sys
from typing import Dict, List, Tuple

from models import SupplierRecord, LookupEntry, MatchResult, MatchMethod
import config

# Legal suffixes to strip (order matters: longer patterns first)
_LEGAL_SUFFIXES = re.compile(
    r"\b("
    r"pty\.?\s*ltd\.?|"
    r"limited|incorporated|corporation|"
    r"l\.l\.c\.?|"
    r"ltd\.?|inc\.?|llc|corp\.?|gmbh|ag|s\.a\.?|plc|co\.?"
    r")\s*$",
    re.IGNORECASE,
)

_LEADING_THE = re.compile(r"^the\s+", re.IGNORECASE)
_MULTI_SPACE = re.compile(r"\s+")


def normalize_name(name: str) -> str:
    """Normalize a supplier name for exact comparison."""
    # Strip leading/trailing whitespace
    name = name.strip()
    # Collapse internal whitespace
    name = _MULTI_SPACE.sub(" ", name)
    # Lowercase
    name = name.lower()
    # Strip legal suffixes (may need multiple passes for "Pty Ltd")
    name = _LEGAL_SUFFIXES.sub("", name).strip()
    name = _LEGAL_SUFFIXES.sub("", name).strip()
    # Strip trailing punctuation
    name = name.rstrip(".,;")
    # Remove leading "The "
    name = _LEADING_THE.sub("", name).strip()
    return name


def build_lookup_index(entries: List[LookupEntry]) -> Dict[str, LookupEntry]:
    """Build a dict mapping normalized supplier name -> LookupEntry."""
    index: Dict[str, LookupEntry] = {}
    for entry in entries:
        key = normalize_name(entry.supplier_name)
        if key in index:
            existing = index[key].supplier_name
            print(
                f"  Warning: Duplicate after normalization: "
                f"'{existing}' and '{entry.supplier_name}' -> '{key}'. "
                f"Keeping first.",
                file=sys.stderr,
            )
        else:
            index[key] = entry
    return index


def exact_match(
    records: List[SupplierRecord],
    lookup_index: Dict[str, LookupEntry],
) -> Tuple[List[MatchResult], List[SupplierRecord]]:
    """
    Phase 1: Exact matching after normalization.
    Returns (matched_results, unmatched_records).
    """
    matched: List[MatchResult] = []
    unmatched: List[SupplierRecord] = []

    for record in records:
        key = normalize_name(record.supplier_name)
        if key in lookup_index:
            entry = lookup_index[key]
            # Show matched name from CSV2 if it differs from CSV1
            matched_name = (
                entry.supplier_name
                if entry.supplier_name.lower() != record.supplier_name.lower()
                else None
            )
            matched.append(
                MatchResult(
                    unique_id=record.unique_id,
                    supplier_name=record.supplier_name,
                    matched_supplier_name=matched_name,
                    supplier_code=entry.supplier_code,
                    match_method=MatchMethod.EXACT,
                    confidence=config.EXACT_MATCH_CONFIDENCE,
                )
            )
        else:
            unmatched.append(record)

    return matched, unmatched

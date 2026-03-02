import re
import sys
from typing import Dict, List, Optional, Tuple

from lib.models import SupplierRecord, LookupEntry, MatchResult, MatchMethod
from lib import config

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
_PARENTHETICAL = re.compile(r"\s*\([^)]*\)")

# Common abbreviation expansions for normalization
_ABBREVIATIONS = [
    (re.compile(r"\bbros?\.?\b"), "brothers"),
    (re.compile(r"\b&\b"), "and"),
    (re.compile(r"\bintl\.?\b"), "international"),
    (re.compile(r"\bsvcs\.?\b"), "services"),
    (re.compile(r"\bmfg\.?\b"), "manufacturing"),
    (re.compile(r"\bdist\.?\b"), "distribution"),
    (re.compile(r"\bgrp\.?\b"), "group"),
    (re.compile(r"\bmgmt\.?\b"), "management"),
]


def normalize_name(name: str) -> str:
    """Normalize a supplier name for exact comparison."""
    name = name.strip()
    name = _MULTI_SPACE.sub(" ", name)
    name = name.lower()
    name = _PARENTHETICAL.sub("", name).strip()
    name = _LEGAL_SUFFIXES.sub("", name).strip()
    name = _LEGAL_SUFFIXES.sub("", name).strip()
    name = name.rstrip(".,;")
    name = _LEADING_THE.sub("", name).strip()
    for pattern, replacement in _ABBREVIATIONS:
        name = pattern.sub(replacement, name)
    name = _MULTI_SPACE.sub(" ", name).strip()
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


def _find_substring_match(
    normalized_source: str,
    lookup_index: Dict[str, LookupEntry],
) -> Optional[LookupEntry]:
    """Check if any lookup name contains the source or vice versa.
    Returns the best (longest) match to avoid short false positives, or None."""
    best_entry = None
    best_len = 0
    src_len = len(normalized_source)
    for lookup_key, entry in lookup_index.items():
        if len(lookup_key) < 3 or src_len < 3:
            continue
        if lookup_key in normalized_source or normalized_source in lookup_key:
            if len(lookup_key) > best_len:
                best_len = len(lookup_key)
                best_entry = entry
    return best_entry


def exact_match(
    records: List[SupplierRecord],
    lookup_index: Dict[str, LookupEntry],
) -> Tuple[List[MatchResult], List[SupplierRecord]]:
    """
    Phase 1: Exact matching after normalization, with substring fallback.
    Returns (matched_results, unmatched_records).
    """
    matched: List[MatchResult] = []
    unmatched: List[SupplierRecord] = []

    for record in records:
        key = normalize_name(record.supplier_name)
        if key in lookup_index:
            entry = lookup_index[key]
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
            entry = _find_substring_match(key, lookup_index)
            if entry:
                matched.append(
                    MatchResult(
                        unique_id=record.unique_id,
                        supplier_name=record.supplier_name,
                        matched_supplier_name=entry.supplier_name,
                        supplier_code=entry.supplier_code,
                        match_method=MatchMethod.EXACT,
                        confidence=0.9,
                    )
                )
            else:
                unmatched.append(record)

    return matched, unmatched

from dataclasses import dataclass
from typing import Optional
from enum import Enum


class MatchMethod(Enum):
    EXACT = "exact"
    LLM = "llm"
    NONE = "no_match"


@dataclass
class SupplierRecord:
    """A row from CSV1 (source) with its unique ID and supplier name."""
    unique_id: str
    supplier_name: str
    raw_row: dict


@dataclass
class LookupEntry:
    """A row from CSV2 (lookup) with supplier name and code."""
    supplier_name: str
    supplier_code: str


@dataclass
class MatchResult:
    """The output of matching a single source record."""
    unique_id: str
    supplier_name: str
    matched_supplier_name: Optional[str]
    supplier_code: Optional[str]
    match_method: MatchMethod
    confidence: float

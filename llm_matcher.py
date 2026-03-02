import json
import os
import sys
import time
from difflib import SequenceMatcher
from typing import Callable, Dict, List, Optional

import anthropic

from models import SupplierRecord, LookupEntry, MatchResult, MatchMethod
import config

SYSTEM_PROMPT = """You are a supplier name matching specialist. You will be given a list of known supplier names (the "lookup list") and a batch of supplier names to match (the "query list"). For each query name, determine if it matches any supplier in the lookup list.

Rules:
- A match means the query name and lookup name refer to the SAME real-world company/supplier, despite differences in spelling, abbreviation, legal suffixes, punctuation, or formatting.
- Common variations to consider: abbreviations (Intl vs International), legal suffixes (Ltd, Inc, Corp, GmbH), punctuation differences, "The" prefix, ampersand vs "and", regional name variations, typos.
- If you are not confident in a match, set matched_name to null.
- Never fabricate a supplier name that is not in the lookup list. The matched_name MUST be copied exactly from the lookup list.

Respond with ONLY a JSON array. No explanation, no markdown fencing.
Each element must have exactly these fields:
{
  "source_name": "<exact query name as given>",
  "matched_name": "<exact lookup name as given, or null if no match>",
  "confidence": <float 0.0 to 1.0>,
  "reasoning": "<brief one-line explanation>"
}"""


def create_client() -> anthropic.Anthropic:
    """Initialize Anthropic client from environment variable."""
    api_key = os.environ.get(config.ANTHROPIC_API_KEY_ENV)
    if not api_key:
        raise RuntimeError(
            f"Environment variable {config.ANTHROPIC_API_KEY_ENV} is not set.\n"
            f"Set it with: export {config.ANTHROPIC_API_KEY_ENV}=sk-ant-..."
        )
    return anthropic.Anthropic(api_key=api_key)


def prefilter_candidates(
    query_names: List[str],
    all_lookup_names: List[str],
    top_k: int = 50,
) -> List[str]:
    """
    For a batch of query names, find the top_k most similar lookup names
    using difflib.SequenceMatcher. Returns deduplicated sorted list.
    """
    scores: Dict[str, float] = {}
    for qname in query_names:
        q_lower = qname.lower()
        for lname in all_lookup_names:
            if lname not in scores:
                scores[lname] = 0.0
            ratio = SequenceMatcher(None, q_lower, lname.lower()).ratio()
            scores[lname] = max(scores[lname], ratio)

    sorted_names = sorted(scores.keys(), key=lambda n: scores[n], reverse=True)
    return sorted_names[:top_k]


def build_matching_prompt(
    unmatched_names: List[str],
    lookup_names: List[str],
) -> str:
    """Build the user message for the API call."""
    lookup_lines = "\n".join(
        f"{i}. {name}" for i, name in enumerate(lookup_names, 1)
    )
    query_lines = "\n".join(
        f"{i}. {name}" for i, name in enumerate(unmatched_names, 1)
    )
    return (
        f"LOOKUP LIST:\n{lookup_lines}\n\n"
        f"QUERY NAMES TO MATCH:\n{query_lines}"
    )


def parse_llm_response(
    response_text: str,
    unmatched_names: List[str],
    lookup_name_set: set,
) -> List[dict]:
    """
    Parse the LLM's JSON response. Validates matched_name exists in lookup list.
    Returns list of dicts with source_name, matched_name, confidence, reasoning.
    """
    text = response_text.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first and last line if they are fences
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        print(f"  Warning: Failed to parse LLM JSON response. Marking batch as no_match.", file=sys.stderr)
        return [
            {"source_name": name, "matched_name": None, "confidence": 0.0, "reasoning": "parse_error"}
            for name in unmatched_names
        ]

    if not isinstance(parsed, list):
        print(f"  Warning: LLM response is not a JSON array. Marking batch as no_match.", file=sys.stderr)
        return [
            {"source_name": name, "matched_name": None, "confidence": 0.0, "reasoning": "invalid_format"}
            for name in unmatched_names
        ]

    # Build index from source_name for alignment
    result_map: Dict[str, dict] = {}
    for entry in parsed:
        sname = entry.get("source_name", "")
        matched = entry.get("matched_name")
        conf = entry.get("confidence", 0.0)
        reasoning = entry.get("reasoning", "")

        # Validate matched_name exists in lookup list
        if matched is not None and matched not in lookup_name_set:
            print(
                f"  Warning: LLM hallucinated supplier name '{matched}' "
                f"(not in lookup list). Treating as no_match.",
                file=sys.stderr,
            )
            matched = None
            conf = 0.0
            reasoning = "hallucinated_name"

        # Clamp confidence
        try:
            conf = max(0.0, min(1.0, float(conf)))
        except (TypeError, ValueError):
            conf = 0.0

        result_map[sname] = {
            "source_name": sname,
            "matched_name": matched,
            "confidence": conf,
            "reasoning": reasoning,
        }

    # Align results with input order, fill missing entries
    results = []
    for name in unmatched_names:
        if name in result_map:
            results.append(result_map[name])
        else:
            results.append({
                "source_name": name,
                "matched_name": None,
                "confidence": 0.0,
                "reasoning": "missing_from_response",
            })

    return results


def call_llm_with_retry(
    client: anthropic.Anthropic,
    user_message: str,
    attempt: int = 0,
) -> str:
    """Make API call with exponential backoff retry."""
    try:
        response = client.messages.create(
            model=config.MODEL_ID,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
            temperature=0.0,
            timeout=config.REQUEST_TIMEOUT,
        )
        return response.content[0].text
    except (anthropic.RateLimitError, anthropic.APITimeoutError, anthropic.APIError) as e:
        if attempt >= config.MAX_RETRIES:
            raise
        wait = config.RETRY_BACKOFF_BASE ** attempt
        print(
            f"  API error (attempt {attempt + 1}/{config.MAX_RETRIES}): {e}. "
            f"Retrying in {wait:.0f}s...",
            file=sys.stderr,
        )
        time.sleep(wait)
        return call_llm_with_retry(client, user_message, attempt + 1)


def llm_match_batch(
    client: anthropic.Anthropic,
    unmatched_records: List[SupplierRecord],
    lookup_entries: List[LookupEntry],
    progress_callback: Callable[[int, int], None],
) -> List[MatchResult]:
    """
    Phase 2: LLM fuzzy matching with batching.
    Returns list of MatchResult (method=LLM or method=NONE).
    """
    all_lookup_names = [e.supplier_name for e in lookup_entries]
    lookup_name_set = set(all_lookup_names)
    lookup_by_name: Dict[str, LookupEntry] = {e.supplier_name: e for e in lookup_entries}

    # Chunk into batches
    batches = [
        unmatched_records[i : i + config.LLM_BATCH_SIZE]
        for i in range(0, len(unmatched_records), config.LLM_BATCH_SIZE)
    ]
    total_batches = len(batches)
    results: List[MatchResult] = []

    for batch_idx, batch in enumerate(batches):
        query_names = [r.supplier_name for r in batch]

        # Pre-filter if lookup list is large
        if len(all_lookup_names) > config.MAX_LOOKUP_NAMES_PER_CALL:
            candidates = prefilter_candidates(query_names, all_lookup_names)
        else:
            candidates = all_lookup_names

        user_message = build_matching_prompt(query_names, candidates)

        try:
            response_text = call_llm_with_retry(client, user_message)
            parsed_results = parse_llm_response(response_text, query_names, lookup_name_set)
        except Exception as e:
            print(f"  Error on batch {batch_idx + 1}: {e}. Marking as no_match.", file=sys.stderr)
            parsed_results = [
                {"source_name": name, "matched_name": None, "confidence": 0.0, "reasoning": "api_error"}
                for name in query_names
            ]

        # Map parsed results to MatchResult objects
        for record, parsed in zip(batch, parsed_results):
            if parsed["matched_name"] is not None:
                entry = lookup_by_name.get(parsed["matched_name"])
                if entry:
                    results.append(
                        MatchResult(
                            unique_id=record.unique_id,
                            supplier_name=record.supplier_name,
                            matched_supplier_name=parsed["matched_name"],
                            supplier_code=entry.supplier_code,
                            match_method=MatchMethod.LLM,
                            confidence=parsed["confidence"],
                        )
                    )
                else:
                    results.append(
                        MatchResult(
                            unique_id=record.unique_id,
                            supplier_name=record.supplier_name,
                            matched_supplier_name=None,
                            supplier_code=None,
                            match_method=MatchMethod.NONE,
                            confidence=0.0,
                        )
                    )
            else:
                results.append(
                    MatchResult(
                        unique_id=record.unique_id,
                        supplier_name=record.supplier_name,
                        matched_supplier_name=None,
                        supplier_code=None,
                        match_method=MatchMethod.NONE,
                        confidence=0.0,
                    )
                )

        progress_callback(batch_idx + 1, total_batches)

    return results

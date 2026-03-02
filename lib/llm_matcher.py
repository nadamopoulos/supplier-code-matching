import json
import sys
import time
from difflib import SequenceMatcher
from typing import Callable, Dict, List, Optional

import anthropic

from lib.models import SupplierRecord, LookupEntry, MatchResult, MatchMethod
from lib import config

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


def create_client(api_key: str) -> anthropic.Anthropic:
    """Initialize Anthropic client with the provided API key."""
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
    if text.startswith("```"):
        lines = text.split("\n")
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

    result_map: Dict[str, dict] = {}
    for entry in parsed:
        sname = entry.get("source_name", "")
        matched = entry.get("matched_name")
        conf = entry.get("confidence", 0.0)
        reasoning = entry.get("reasoning", "")

        if matched is not None and matched not in lookup_name_set:
            print(
                f"  Warning: LLM hallucinated supplier name '{matched}' "
                f"(not in lookup list). Treating as no_match.",
                file=sys.stderr,
            )
            matched = None
            conf = 0.0
            reasoning = "hallucinated_name"

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

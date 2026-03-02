import json
import sys
import time
from difflib import SequenceMatcher
from typing import Callable, Dict, List, Optional

import anthropic

from lib.models import SupplierRecord, LookupEntry, MatchResult, MatchMethod
from lib import config

SYSTEM_PROMPT = """You are a supplier name matching specialist. You will be given a list of known supplier names (the "lookup list") and a batch of query strings to match (the "query list"). For each query string, determine if it matches any supplier in the lookup list.

CRITICAL: The query strings are often NOT clean supplier names. They are frequently document titles, contract names, file names, or descriptions that CONTAIN a supplier name embedded within them. Examples:
- "Approach Personnel Contract - Amendment Oct25" contains supplier "Approach Personnel"
- "5Flow_Phoenix_OrderForm_2025" contains supplier "5Flow" or "Phoenix"
- "BCL Phoenix- Amended terms - 2024" contains supplier "BCL Phoenix"
- "CHARTERHOUSE (KONICA MINOLTA) Service Agreement" contains supplier "Charterhouse" or "Konica Minolta"

Your task:
1. First, EXTRACT the likely supplier/company name from each query string by stripping away contract types, dates, file extensions, order numbers, and other non-name parts.
2. Then, match the extracted name against the lookup list.

Matching rules:
- A match means the extracted supplier name and a lookup name refer to the SAME real-world company/supplier.
- Consider: abbreviations (Intl vs International), legal suffixes (Ltd, Inc, Corp, GmbH), punctuation differences, "The" prefix, ampersand vs "and", underscores vs spaces, partial names (e.g. "BCL" matching "BCL Medical"), parent/subsidiary relationships, and typos.
- If multiple lookup entries could match, pick the best one.
- If you are not confident in a match (confidence < 0.6), set matched_name to null.
- Never fabricate a supplier name. The matched_name MUST be copied EXACTLY from the lookup list, character for character.

Respond with ONLY a JSON array. No explanation, no markdown fencing.
Each element must have exactly these fields:
{
  "source_name": "<exact query string as given>",
  "matched_name": "<exact lookup name as given, or null if no match>",
  "confidence": <float 0.0 to 1.0>,
  "reasoning": "<brief explanation of what supplier name you extracted and why it matches>"
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
            max_tokens=8192,
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

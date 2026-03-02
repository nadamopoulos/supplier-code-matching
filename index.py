import sys
import os
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from lib.models import SupplierRecord, LookupEntry, MatchResult, MatchMethod
from lib import config
from lib.exact_matcher import build_lookup_index, exact_match
from lib.llm_matcher import (
    create_client,
    prefilter_candidates,
    build_matching_prompt,
    parse_llm_response,
    call_llm_with_retry,
)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

PUBLIC_DIR = Path(__file__).parent / "public"


# --- Request / Response models ---

class SourceRecordInput(BaseModel):
    unique_id: str
    supplier_name: str


class LookupEntryInput(BaseModel):
    supplier_name: str
    supplier_code: str


class MatchResultOutput(BaseModel):
    unique_id: str
    supplier_name: str
    matched_supplier_name: Optional[str] = None
    supplier_code: Optional[str] = None
    match_method: str
    confidence: float


class ExactMatchRequest(BaseModel):
    source_records: List[SourceRecordInput]
    lookup_entries: List[LookupEntryInput]


class ExactMatchResponse(BaseModel):
    matched: List[MatchResultOutput]
    unmatched: List[SourceRecordInput]
    stats: dict


class LLMBatchRequest(BaseModel):
    api_key: str
    unmatched_records: List[SourceRecordInput]
    lookup_entries: List[LookupEntryInput]


class LLMBatchResponse(BaseModel):
    results: List[MatchResultOutput]


# --- Endpoints ---

@app.post("/api/match-exact", response_model=ExactMatchResponse)
def match_exact(req: ExactMatchRequest):
    records = [
        SupplierRecord(unique_id=r.unique_id, supplier_name=r.supplier_name, raw_row={})
        for r in req.source_records
    ]
    entries = [
        LookupEntry(supplier_name=e.supplier_name, supplier_code=e.supplier_code)
        for e in req.lookup_entries
    ]

    lookup_index = build_lookup_index(entries)
    matched_results, unmatched_records = exact_match(records, lookup_index)

    matched_out = [
        MatchResultOutput(
            unique_id=r.unique_id,
            supplier_name=r.supplier_name,
            matched_supplier_name=r.matched_supplier_name,
            supplier_code=r.supplier_code,
            match_method=r.match_method.value,
            confidence=r.confidence,
        )
        for r in matched_results
    ]

    unmatched_out = [
        SourceRecordInput(unique_id=r.unique_id, supplier_name=r.supplier_name)
        for r in unmatched_records
    ]

    return ExactMatchResponse(
        matched=matched_out,
        unmatched=unmatched_out,
        stats={
            "total": len(records),
            "exact_matches": len(matched_results),
            "unmatched": len(unmatched_records),
        },
    )


@app.post("/api/match-llm-batch", response_model=LLMBatchResponse)
def match_llm_batch(req: LLMBatchRequest):
    if len(req.unmatched_records) > config.LLM_BATCH_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"Batch size exceeds maximum of {config.LLM_BATCH_SIZE}",
        )

    all_lookup_names = [e.supplier_name for e in req.lookup_entries]
    lookup_name_set = set(all_lookup_names)
    lookup_by_name = {e.supplier_name: e for e in req.lookup_entries}

    query_names = [r.supplier_name for r in req.unmatched_records]

    # Pre-filter if lookup list is large
    if len(all_lookup_names) > config.MAX_LOOKUP_NAMES_PER_CALL:
        candidates = prefilter_candidates(query_names, all_lookup_names)
    else:
        candidates = all_lookup_names

    user_message = build_matching_prompt(query_names, candidates)

    client = create_client(req.api_key)

    try:
        response_text = call_llm_with_retry(client, user_message)
        parsed_results = parse_llm_response(response_text, query_names, lookup_name_set)
    except Exception as e:
        print(f"LLM batch error: {e}", file=sys.stderr)
        results = [
            MatchResultOutput(
                unique_id=r.unique_id,
                supplier_name=r.supplier_name,
                match_method="no_match",
                confidence=0.0,
            )
            for r in req.unmatched_records
        ]
        return LLMBatchResponse(results=results)

    results = []
    for record, parsed in zip(req.unmatched_records, parsed_results):
        if parsed["matched_name"] is not None:
            entry = lookup_by_name.get(parsed["matched_name"])
            if entry:
                results.append(MatchResultOutput(
                    unique_id=record.unique_id,
                    supplier_name=record.supplier_name,
                    matched_supplier_name=parsed["matched_name"],
                    supplier_code=entry.supplier_code,
                    match_method="llm",
                    confidence=parsed["confidence"],
                ))
            else:
                results.append(MatchResultOutput(
                    unique_id=record.unique_id,
                    supplier_name=record.supplier_name,
                    match_method="no_match",
                    confidence=0.0,
                ))
        else:
            results.append(MatchResultOutput(
                unique_id=record.unique_id,
                supplier_name=record.supplier_name,
                match_method="no_match",
                confidence=0.0,
            ))

    return LLMBatchResponse(results=results)


# --- Static files & frontend ---

@app.get("/")
def serve_index():
    return FileResponse(PUBLIC_DIR / "index.html")


app.mount("/", StaticFiles(directory=PUBLIC_DIR), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)

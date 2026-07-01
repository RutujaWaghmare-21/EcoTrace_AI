"""
EcoTrace AI - Document Extraction Agent

Takes raw parsed file output (from tools/document_parser.py) and produces
structured supplier/shipment records:

{
  "supplier": "Supplier A",
  "country": "India",
  "transport": "Air",
  "distance_km": 6500,
  "material": "Coffee Beans",
  "weight_tonnes": 2.5,
  "certifications": ["Fairtrade"],
  "renewable_energy_pct": 30
}

Strategy:
  - If the parsed file has tabular rows (CSV/XLSX, or tables found in PDF),
    try direct column-mapping first (fast, free, no LLM call).
  - For anything that doesn't map cleanly, OR for free-text PDFs/TXT with
    no tables, fall back to an LLM extraction call that reads the raw text
    and returns structured JSON.
  - Also pushes the raw text into the vector store (RAG) for later retrieval,
    regardless of whether structured extraction succeeded.
"""
import json
import re
from typing import Any

from llm_client import chat
from tools.vector_store import VectorStore, chunk_text

# Column name aliases -> canonical field, used for direct CSV/XLSX mapping
COLUMN_ALIASES = {
    "supplier": ["supplier", "supplier_name", "vendor", "vendor_name"],
    "country": ["country", "origin_country", "manufacturing_location", "location"],
    "transport": ["transport", "transport_mode", "shipping_method", "freight_mode"],
    "distance_km": ["distance_km", "distance", "shipping_distance_km", "distance(km)"],
    "material": ["material", "product", "product_category", "category", "item"],
    "weight_tonnes": ["weight_tonnes", "weight_t", "weight(tonnes)", "weight"],
    "certifications": ["certifications", "certification", "certs"],
    "renewable_energy_pct": ["renewable_energy_pct", "renewable_pct", "renewable_energy"],
}

EXTRACTION_SYSTEM_PROMPT = """You are the Document Extraction Agent for EcoTrace AI.
Extract supplier and shipment information from the provided document text.

Return ONLY a JSON array (no markdown, no commentary) of objects, each with
these fields (use null if genuinely not present in the text):
- supplier (string)
- country (string)
- transport (string: one of "air", "sea", "road", "rail", "local")
- distance_km (number)
- material (string)
- weight_tonnes (number)
- certifications (array of strings)
- renewable_energy_pct (number, 0-100)

If the document describes multiple suppliers/shipments, return one object
per supplier/shipment. If you truly cannot find any supplier-relevant
information, return an empty array [].
"""


def _is_missing(value: Any) -> bool:
    """True for None, empty string, the literal string 'nan', and float NaN
    (pandas represents empty CSV/XLSX cells as float('nan'), which the
    `in (None, "", "nan")` check does NOT catch since NaN != NaN)."""
    if value is None:
        return True
    if isinstance(value, float) and value != value:  # NaN check, avoids importing math
        return True
    if isinstance(value, str) and value.strip().lower() in ("", "nan", "none"):
        return True
    return False


def _map_row_directly(row: dict) -> dict | None:
    """Try to map a tabular row to canonical fields using column aliases."""
    row_lower = {str(k).strip().lower(): v for k, v in row.items()}
    mapped: dict[str, Any] = {}

    for canonical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in row_lower and not _is_missing(row_lower[alias]):
                mapped[canonical] = row_lower[alias]
                break

    if "supplier" not in mapped:
        return None  # not enough to be useful

    # Light type coercion
    if "distance_km" in mapped:
        mapped["distance_km"] = _to_float(mapped["distance_km"])
    if "weight_tonnes" in mapped:
        mapped["weight_tonnes"] = _to_float(mapped["weight_tonnes"])
    if "renewable_energy_pct" in mapped:
        mapped["renewable_energy_pct"] = _to_float(mapped["renewable_energy_pct"])

    # certifications must always end up as a list[str], regardless of what
    # came out of the spreadsheet (string, NaN float, already-a-list, etc.)
    if "certifications" in mapped:
        raw = mapped["certifications"]
        if isinstance(raw, str):
            mapped["certifications"] = [c.strip() for c in re.split(r"[,;]", raw) if c.strip()]
        elif isinstance(raw, list):
            mapped["certifications"] = [str(c).strip() for c in raw if not _is_missing(c)]
        else:
            # Anything else (NaN float, int, etc.) isn't a usable certification list
            mapped["certifications"] = []

    return mapped


def _to_float(val) -> float | None:
    try:
        return float(re.sub(r"[^\d.\-]", "", str(val)))
    except (ValueError, TypeError):
        return None


def _llm_extract(raw_text: str) -> list[dict]:
    """Fall back to LLM extraction for unstructured text (PDF prose, TXT)."""
    if not raw_text.strip():
        return []

    # Guard against extremely long documents blowing the context window
    truncated = raw_text[:12000]

    try:
        result = chat(
            messages=[
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": truncated},
            ],
            temperature=0,
        )
    except Exception as e:  # noqa: BLE001
        print(f"[document_extraction_agent] LLM extraction failed: {e}")
        return []

    content = result["content"].strip()
    # Strip markdown code fences if the model added them anyway
    content = re.sub(r"^```(json)?|```$", "", content, flags=re.MULTILINE).strip()
    try:
        parsed = json.loads(content)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            return [parsed]
    except json.JSONDecodeError:
        pass
    return []


def extract_from_parsed_file(
    parsed: dict[str, Any], vector_store: VectorStore | None = None
) -> tuple[list[dict], bool]:
    """
    Main entry point. `parsed` is the dict returned by tools.document_parser.parse_file.

    Returns (records, rag_indexed):
      - records: list of structured supplier/shipment records
      - rag_indexed: True if the raw text was successfully embedded into the
        vector store, False if indexing was skipped or failed (e.g. OpenAI
        quota/billing error) - structured extraction still proceeds either way.
    """
    records: list[dict] = []

    # 1. Try direct mapping on tabular rows first (cheap, deterministic)
    for row in parsed.get("rows", []):
        mapped = _map_row_directly(row)
        if mapped:
            mapped["source_file"] = parsed["filename"]
            records.append(mapped)

    # 2. If no tabular rows produced usable records, fall back to LLM extraction
    if not records and parsed.get("raw_text"):
        llm_records = _llm_extract(parsed["raw_text"])
        for r in llm_records:
            r["source_file"] = parsed["filename"]
        records.extend(llm_records)

    # 3. Always try to index the raw text for RAG, regardless of extraction
    # success. This is best-effort: if the embeddings API call fails (e.g.
    # billing/quota issue, network problem), we don't want that to block the
    # user from getting their structured records - RAG just won't have this
    # doc indexed, and we report that back to the caller.
    rag_indexed = False
    if vector_store is not None and parsed.get("raw_text"):
        chunks = chunk_text(parsed["raw_text"])
        try:
            vector_store.add_chunks(chunks, source=parsed["filename"])
            rag_indexed = True
        except Exception as e:  # noqa: BLE001
            print(f"[document_extraction_agent] RAG indexing failed for "
                  f"{parsed['filename']}: {e}")

    return records, rag_indexed

"""
EcoTrace AI - Document Parser Tool

Used by the Document Extraction Agent. Reads raw files (PDF, CSV, XLSX, TXT)
from disk and returns:
  - raw_text: full text content (for PDFs/TXT, used for RAG chunking + LLM extraction)
  - rows: list of dict rows (for CSV/XLSX, used for structured supplier extraction)

This module does NOT call any LLM - it's pure file I/O / parsing. The
Document Extraction Agent decides what to do with the output.
"""
from pathlib import Path
from typing import Any

import pandas as pd
import pdfplumber


def parse_file(filepath: str | Path) -> dict[str, Any]:
    filepath = Path(filepath)
    suffix = filepath.suffix.lower()

    if suffix == ".pdf":
        return _parse_pdf(filepath)
    elif suffix == ".csv":
        return _parse_csv(filepath)
    elif suffix in (".xlsx", ".xls"):
        return _parse_xlsx(filepath)
    elif suffix == ".txt":
        return _parse_txt(filepath)
    else:
        raise ValueError(f"Unsupported file type: {suffix}")


def _parse_pdf(filepath: Path) -> dict[str, Any]:
    text_parts = []
    tables: list[list[list[str]]] = []
    with pdfplumber.open(filepath) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            text_parts.append(page_text)
            for table in page.extract_tables() or []:
                tables.append(table)

    raw_text = "\n\n".join(text_parts).strip()

    # Try to turn any extracted tables into row dicts using the first
    # row as a header, on a best-effort basis.
    rows: list[dict] = []
    for table in tables:
        if not table or len(table) < 2:
            continue
        header = [str(h).strip().lower() if h else f"col_{i}" for i, h in enumerate(table[0])]
        for raw_row in table[1:]:
            row = {header[i]: raw_row[i] for i in range(min(len(header), len(raw_row)))}
            rows.append(row)

    return {
        "filename": filepath.name,
        "file_type": "pdf",
        "raw_text": raw_text,
        "rows": rows,
    }


def _parse_csv(filepath: Path) -> dict[str, Any]:
    df = pd.read_csv(filepath)
    df.columns = [str(c).strip().lower() for c in df.columns]
    rows = df.to_dict(orient="records")
    return {
        "filename": filepath.name,
        "file_type": "csv",
        "raw_text": df.to_string(index=False),
        "rows": rows,
    }


def _parse_xlsx(filepath: Path) -> dict[str, Any]:
    all_rows = []
    text_chunks = []
    xls = pd.ExcelFile(filepath)
    for sheet_name in xls.sheet_names:
        df = xls.parse(sheet_name)
        df.columns = [str(c).strip().lower() for c in df.columns]
        all_rows.extend(df.to_dict(orient="records"))
        text_chunks.append(f"--- Sheet: {sheet_name} ---\n{df.to_string(index=False)}")
    return {
        "filename": filepath.name,
        "file_type": "xlsx",
        "raw_text": "\n\n".join(text_chunks),
        "rows": all_rows,
    }


def _parse_txt(filepath: Path) -> dict[str, Any]:
    raw_text = filepath.read_text(encoding="utf-8", errors="ignore")
    return {
        "filename": filepath.name,
        "file_type": "txt",
        "raw_text": raw_text,
        "rows": [],
    }

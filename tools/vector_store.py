"""
EcoTrace AI - Vector Store (FAISS)

Stores text chunks from uploaded documents as embeddings, supports
similarity search for the RAG pipeline. Persisted to disk so the index
survives across Streamlit reruns/restarts.

Index: a flat L2 FAISS index (simple, exact search - fine at this scale).
Metadata (chunk text, source filename, chunk id) is stored alongside in
a JSON sidecar file, indexed by row position to match FAISS's internal ids.
"""
import json
from pathlib import Path
from typing import Any

import faiss
import numpy as np

import config
from llm_client import embed_texts


class VectorStore:
    def __init__(self):
        self.index_path = config.VECTOR_INDEX_PATH
        self.meta_path = config.VECTOR_META_PATH
        self.dim = config.EMBED_DIM
        self.index: faiss.Index = self._load_index()
        self.metadata: list[dict] = self._load_meta()

    def _load_index(self) -> faiss.Index:
        if self.index_path.exists():
            return faiss.read_index(str(self.index_path))
        return faiss.IndexFlatL2(self.dim)

    def _load_meta(self) -> list[dict]:
        if self.meta_path.exists():
            return json.loads(self.meta_path.read_text())
        return []

    def _save(self):
        faiss.write_index(self.index, str(self.index_path))
        self.meta_path.write_text(json.dumps(self.metadata, default=str, indent=2))

    def add_chunks(self, chunks: list[str], source: str, extra_meta: dict | None = None):
        """Embed and store a list of text chunks tagged with their source filename."""
        if not chunks:
            return
        vectors = embed_texts(chunks)
        arr = np.array(vectors, dtype="float32")
        self.index.add(arr)
        for chunk in chunks:
            entry = {"text": chunk, "source": source}
            if extra_meta:
                entry.update(extra_meta)
            self.metadata.append(entry)
        self._save()

    def search(self, query: str, top_k: int = None) -> list[dict[str, Any]]:
        """Return top_k most similar chunks to the query, with similarity scores."""
        if self.index.ntotal == 0:
            return []
        top_k = top_k or config.RAG_TOP_K
        top_k = min(top_k, self.index.ntotal)

        query_vec = np.array(embed_texts([query]), dtype="float32")
        distances, indices = self.index.search(query_vec, top_k)

        results = []
        for dist, idx in zip(distances[0], indices[0]):
            if idx == -1 or idx >= len(self.metadata):
                continue
            entry = dict(self.metadata[idx])
            entry["score"] = float(dist)  # lower L2 distance = more similar
            results.append(entry)
        return results

    def clear(self):
        self.index = faiss.IndexFlatL2(self.dim)
        self.metadata = []
        self._save()

    def stats(self) -> dict:
        sources = sorted({m["source"] for m in self.metadata})
        return {"total_chunks": len(self.metadata), "sources": sources}


def chunk_text(text: str, chunk_size: int = None, overlap: int = None) -> list[str]:
    """Simple fixed-size character chunking with overlap."""
    chunk_size = chunk_size or config.CHUNK_SIZE_CHARS
    overlap = overlap or config.CHUNK_OVERLAP_CHARS
    text = text.strip()
    if not text:
        return []

    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start = end - overlap
        if start <= 0:
            break
    return [c.strip() for c in chunks if c.strip()]

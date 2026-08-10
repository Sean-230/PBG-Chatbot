"""
rag_stub.py — RAG Retrieval for PBG Chatbot.

This module provides `query_knowledge_base()` which is called by main.py
before every Gemini generation to inject relevant document context into the
system prompt (Retrieval-Augmented Generation).

PREREQUISITES
─────────────
- Run `python3 ingest_data.py` at least once to build the ChromaDB index.
- Ensure GEMINI_API_KEY is set in your .env file.

If Pinecone is not yet initialised or credentials are missing, the function
safely returns an empty string so the chatbot still works without RAG context.
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Configuration — must match the values used in ingest_data.py
# ──────────────────────────────────────────────────────────────────────────────

PINECONE_INDEX_NAME: str = os.environ.get("PINECONE_INDEX_NAME", "pbg-knowledge")
EMBEDDING_MODEL: str = "gemini-embedding-001"

# Number of top-k chunks to retrieve per query.
# Increased to 30 so that when querying a tracking number with many history rows,
# all rows are fetched, allowing the AI to successfully find the newest date.
TOP_K: int = 30

# Minimum cosine similarity score (0.0–1.0) to include a result.
# 0.4 works well for Indonesian regulatory text.
MIN_SIMILARITY: float = 0.40

# ──────────────────────────────────────────────────────────────────────────────
# Lazy-initialised singletons (created once on first call)
# ──────────────────────────────────────────────────────────────────────────────

_pinecone_index = None
_genai_client = None        # google.genai.Client instance


def _get_index():
    """Returns the Pinecone Index, connecting lazily on first call."""
    global _pinecone_index

    if _pinecone_index is not None:
        return _pinecone_index

    pinecone_api_key = os.environ.get("PINECONE_API_KEY", "")
    if not pinecone_api_key:
        logger.info(
            "[RAG] PINECONE_API_KEY not found. "
            "Set it in your .env file to enable RAG."
        )
        return None

    try:
        from pinecone import Pinecone
        pc = Pinecone(api_key=pinecone_api_key)
        _pinecone_index = pc.Index(PINECONE_INDEX_NAME)
        logger.info(
            "[RAG] Connected to Pinecone index '%s'.",
            PINECONE_INDEX_NAME
        )
    except Exception as exc:
        logger.warning("[RAG] Could not connect to Pinecone: %s", exc)
        return None

    return _pinecone_index


def _get_genai_client():
    """Returns the google.genai Client, initialising it lazily on first call."""
    global _genai_client

    if _genai_client is not None:
        return _genai_client

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        logger.warning("[RAG] GEMINI_API_KEY not set — RAG retrieval disabled.")
        return None

    from google import genai
    _genai_client = genai.Client(api_key=api_key)
    return _genai_client


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def query_knowledge_base(question: str) -> str:
    """
    Retrieves relevant context from the PBG knowledge base for a given question.

    Embeds the question with Gemini, queries ChromaDB for similar chunks, filters
    by similarity threshold, and returns a formatted context string ready to be
    injected into the Gemini system prompt.

    Args:
        question: The user's natural-language question (in Indonesian).

    Returns:
        A formatted string of the most relevant document excerpts,
        or an empty string if nothing relevant is found or RAG is unavailable.
    """
    if not question.strip():
        return ""

    # --- Check prerequisites ------------------------------------------------
    index = _get_index()
    if index is None:
        return ""

    client = _get_genai_client()
    if client is None:
        return ""

    # --- Embed the query ----------------------------------------------------
    try:
        from google import genai
        # IMPORTANT: Use RETRIEVAL_QUERY (not RETRIEVAL_DOCUMENT) for live queries.
        # Asymmetric task types improve retrieval precision significantly.
        response = client.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=question,
            config=genai.types.EmbedContentConfig(
                task_type="RETRIEVAL_QUERY",
                output_dimensionality=768,
            ),
        )
        query_vector = response.embeddings[0].values

    except Exception as exc:
        logger.error("[RAG] Failed to embed query: %s", exc)
        return ""

    # --- Query Pinecone -----------------------------------------------------
    try:
        results = index.query(
            vector=query_vector,
            top_k=TOP_K,
            include_metadata=True
        )
    except Exception as exc:
        logger.error("[RAG] Pinecone query failed: %s", exc)
        return ""

    # --- Smart intent detection: which sheet tab is relevant? ------------------
    # Keywords that signal the user is asking about REQUIREMENTS (SYARAT tab)
    SYARAT_KEYWORDS = [
        "syarat", "persyaratan", "dokumen", "mengurus", "cara", "proses",
        "prosedur", "bagaimana", "apa yang dibutuhkan", "apa saja", "ketentuan",
        "persyaratan", "lampiran", "perlu", "dibutuhkan", "dokumen apa",
    ]
    # Keywords that signal the user is asking about STATUS (TRANSAKSI tab)
    TRANSAKSI_KEYWORDS = [
        "status", "nomer", "nomor", "daftar", "cek", "check", "registrasi",
        "berkas", "permohonan saya", "progress", "sedang", "sudah", "kapan selesai",
    ]

    question_lower = question.lower()
    wants_syarat    = any(kw in question_lower for kw in SYARAT_KEYWORDS)
    wants_transaksi = any(kw in question_lower for kw in TRANSAKSI_KEYWORDS)

    # --- Filter and format results ------------------------------------------
    matches = results.get("matches", [])

    if not matches:
        return ""

    # Pinecone returns 'score' which is equivalent to cosine similarity
    # Build two buckets: preferred (matches intent) and fallback (everything else)
    preferred_parts: list[str] = []
    fallback_parts:  list[str] = []

    for match in matches:
        similarity = match.get("score", 0.0)
        if similarity < MIN_SIMILARITY:
            continue
            
        meta = match.get("metadata", {})
        doc_text = meta.get("text", "")
        source = meta.get("source", "Dokumen PBG")
        entry  = f"--- Sumber: {source} (relevansi: {similarity:.0%}) ---\n{doc_text}"

        source_upper = source.upper()
        is_syarat    = "SYARAT"    in source_upper
        is_transaksi = "TRANSAKSI" in source_upper

        # Route to preferred bucket when we can detect intent
        if (wants_syarat and not wants_transaksi and is_syarat):
            preferred_parts.append(entry)
        elif (wants_transaksi and not wants_syarat and is_transaksi):
            preferred_parts.append(entry)
        else:
            fallback_parts.append(entry)

    # Merge: preferred first, then fallback (up to a reasonable cap)
    context_parts = preferred_parts + fallback_parts
    # Limit total context to avoid overloading the model's context window
    context_parts = context_parts[:20]

    if not context_parts:
        logger.info(
            "[RAG] No chunks above threshold %.2f for: '%s'",
            MIN_SIMILARITY, question[:80]
        )
        return ""

    logger.info(
        "[RAG] Retrieved %d chunk(s) (%d preferred, %d fallback) for: '%s'",
        len(context_parts), len(preferred_parts), len(fallback_parts), question[:80],
    )
    return "\n\n".join(context_parts)


def ingest_documents_from_drive(folder_id: str) -> None:
    """Convenience wrapper — run `python3 ingest_data.py` directly for full logs."""
    import subprocess
    import sys

    logger.info("[RAG] Triggering ingestion for folder_id='%s' ...", folder_id)
    result = subprocess.run(
        [sys.executable, str(Path(__file__).parent / "ingest_data.py")],
        env={**os.environ, "GOOGLE_DRIVE_FOLDER_ID": folder_id},
    )
    if result.returncode != 0:
        logger.error("[RAG] Ingestion script exited with code %d.", result.returncode)
    else:
        logger.info("[RAG] Ingestion complete.")

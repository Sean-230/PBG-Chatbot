"""
rag_stub.py — RAG Retrieval for PBG Chatbot.

This module provides `query_knowledge_base()` which is called by main.py
before every Gemini generation to inject relevant document context into the
system prompt (Retrieval-Augmented Generation).

PREREQUISITES
─────────────
- Run `python3 ingest_data.py` at least once to build the ChromaDB index.
- Ensure GEMINI_API_KEY is set in your .env file.

If ChromaDB is not yet initialised (first run before ingestion), the function
safely returns an empty string so the chatbot still works without RAG context.
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Configuration — must match the values used in ingest_data.py
# ──────────────────────────────────────────────────────────────────────────────

CHROMA_DB_PATH: str = str(Path(__file__).parent / "chroma_db")
CHROMA_COLLECTION_NAME: str = "pbg_knowledge"
EMBEDDING_MODEL: str = "gemini-embedding-2"

# Number of top-k chunks to retrieve per query.
TOP_K: int = 5

# Minimum cosine similarity score (0.0–1.0) to include a result.
# 0.4 works well for Indonesian regulatory text.
MIN_SIMILARITY: float = 0.40

# ──────────────────────────────────────────────────────────────────────────────
# Lazy-initialised singletons (created once on first call)
# ──────────────────────────────────────────────────────────────────────────────

_chroma_collection = None
_genai_client = None        # google.genai.Client instance


def _get_collection():
    """Returns the ChromaDB collection, connecting lazily on first call."""
    global _chroma_collection

    if _chroma_collection is not None:
        return _chroma_collection

    if not Path(CHROMA_DB_PATH).exists():
        logger.info(
            "[RAG] ChromaDB not found at '%s'. "
            "Run `python3 ingest_data.py` to build the knowledge base.",
            CHROMA_DB_PATH,
        )
        return None

    try:
        import chromadb
        client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
        _chroma_collection = client.get_collection(name=CHROMA_COLLECTION_NAME)
        logger.info(
            "[RAG] Connected to ChromaDB collection '%s' (%d documents).",
            CHROMA_COLLECTION_NAME,
            _chroma_collection.count(),
        )
    except Exception as exc:
        logger.warning("[RAG] Could not connect to ChromaDB: %s", exc)
        return None

    return _chroma_collection


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
    collection = _get_collection()
    if collection is None:
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
            config=genai.types.EmbedContentConfig(task_type="RETRIEVAL_QUERY"),
        )
        query_vector = response.embeddings[0].values

    except Exception as exc:
        logger.error("[RAG] Failed to embed query: %s", exc)
        return ""

    # --- Query ChromaDB -----------------------------------------------------
    try:
        results = collection.query(
            query_embeddings=[query_vector],
            n_results=min(TOP_K, collection.count()),
            include=["documents", "metadatas", "distances"],
        )
    except Exception as exc:
        logger.error("[RAG] ChromaDB query failed: %s", exc)
        return ""

    # --- Filter and format results ------------------------------------------
    documents = results.get("documents", [[]])[0]
    distances = results.get("distances", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]

    if not documents:
        return ""

    # ChromaDB with cosine space: distance = 1 - cosine_similarity
    context_parts: list[str] = []

    for doc_text, distance, meta in zip(documents, distances, metadatas):
        similarity = 1.0 - distance

        if similarity < MIN_SIMILARITY:
            continue

        source = meta.get("source", "Dokumen PBG")
        context_parts.append(
            f"--- Sumber: {source} (relevansi: {similarity:.0%}) ---\n{doc_text}"
        )

    if not context_parts:
        logger.info(
            "[RAG] No chunks above threshold %.2f for: '%s'",
            MIN_SIMILARITY, question[:80]
        )
        return ""

    logger.info(
        "[RAG] Retrieved %d relevant chunk(s) for: '%s'",
        len(context_parts), question[:80],
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

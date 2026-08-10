"""
ingest_data.py — RAG Ingestion Pipeline for PBG Chatbot.

This script reads documents from a restricted Google Drive folder using a
Service Account (credentials.json) and builds a local ChromaDB vector database
that the `query_knowledge_base()` function in rag_stub.py will query at runtime.

Run this script manually (or on a schedule) to rebuild / refresh the knowledge base:

    python ingest_data.py

──────────────────────────────────────────────────────────────────────────────
REQUIRED DEPENDENCIES — install before running:
──────────────────────────────────────────────────────────────────────────────

    pip install google-api-python-client google-auth pandas gspread \\
                google-generativeai chromadb python-dotenv PyMuPDF \\
                google-generativeai pinecone-client python-dotenv PyMuPDF \\
                python-docx

    # PyMuPDF   → fast PDF text extraction (via `fitz`)
    # python-docx → Word document extraction
    # gspread   → clean Pandas-friendly Google Sheets reader
    # google-api-python-client + google-auth → Drive API (file listing + download)
    # google-genai → Gemini embedding API
    # pinecone-client → cloud vector store

──────────────────────────────────────────────────────────────────────────────
"""

import io
import json
import logging
import os
import re
import time
import hashlib
from pathlib import Path
from typing import Optional

from pinecone import Pinecone
import pymupdf as fitz                     # PyMuPDF — fastest PDF text extractor
import gspread                             # Clean Google Sheets client
from google import genai as google_genai   # google-genai (same SDK used by main.py)
import pandas as pd
from docx import Document as DocxDocument  # python-docx
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# ──────────────────────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────────────────────

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# --- Paths & IDs -------------------------------------------------------------
# The Google Drive folder that contains your PBG regulatory documents.
DRIVE_FOLDER_ID: str = os.environ.get(
    "GOOGLE_DRIVE_FOLDER_ID", "19RYdniy0kVtlqpdslman6Vg_wfB0CUsW"
)

# Path to the Service Account credentials JSON downloaded from Google Cloud.
CREDENTIALS_PATH: str = os.environ.get(
    "GOOGLE_APPLICATION_CREDENTIALS",
    str(Path(__file__).parent / "credentials.json"),
)

# --- Embedding model ---------------------------------------------------------
# Google's recommended embedding model for semantic search tasks.
EMBEDDING_MODEL: str = "gemini-embedding-2"

# The task type tells the embedding model how the vector will be used.
# RETRIEVAL_DOCUMENT → optimise vectors for storage / retrieval.
# RETRIEVAL_QUERY    → use this when embedding a search query at runtime.
EMBEDDING_TASK_TYPE: str = "RETRIEVAL_DOCUMENT"

# Global google-genai client (initialised in main() after reading the API key)
_genai_client = None

# --- Pinecone ----------------------------------------------------------------
# The vector store is hosted on Pinecone for serverless deployment.
PINECONE_INDEX_NAME: str = os.environ.get("PINECONE_INDEX_NAME", "pbg-knowledge")

# --- Chunking ----------------------------------------------------------------
# Sliding-window character-level chunker parameters.
# Overlap lets the LLM see enough context on either side of a chunk boundary.
CHUNK_SIZE: int = 1_000        # characters per chunk
CHUNK_OVERLAP: int = 150       # characters of overlap between adjacent chunks

# --- Rate limiting -----------------------------------------------------------
# Google Embedding API has a Queries-Per-Minute limit on the free tier.
# Add a small delay between batch embedding calls to stay under the limit.
EMBED_DELAY_SECONDS: float = 1.0

# ──────────────────────────────────────────────────────────────────────────────
# Google API MIME type constants
# ──────────────────────────────────────────────────────────────────────────────
MIME_GOOGLE_SHEET = "application/vnd.google-apps.spreadsheet"
MIME_GOOGLE_DOC   = "application/vnd.google-apps.document"
MIME_PDF          = "application/pdf"
MIME_WORD_DOCX    = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MIME_WORD_OLD     = "application/msword"

# Google Workspace files must be exported to a downloadable MIME before fetching.
EXPORT_MIME_MAP: dict[str, str] = {
    MIME_GOOGLE_DOC: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


# ──────────────────────────────────────────────────────────────────────────────
# Authentication helpers
# ──────────────────────────────────────────────────────────────────────────────

# OAuth 2.0 scopes required by this script:
#   Drive  → list files and download binary content
#   Sheets → read spreadsheet cell data
SCOPES: list[str] = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
]


def build_credentials() -> service_account.Credentials:
    """
    Loads Service Account credentials for Drive + Sheets access.

    Priority:
      1. GOOGLE_APPLICATION_CREDENTIALS_JSON env var — a JSON string of the
         full service account key (used in Vercel / CI environments where
         uploading a file is not possible).
      2. Local credentials.json file — used during local development.

    Returns:
        A google.oauth2.service_account.Credentials object scoped for
        Drive and Sheets read access.
    """
    creds_json_str = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON", "")

    if creds_json_str:
        # Load credentials from environment variable (Vercel / CI)
        try:
            creds_info = json.loads(creds_json_str)
            creds = service_account.Credentials.from_service_account_info(
                creds_info, scopes=SCOPES
            )
            logger.info(
                "Service Account credentials loaded from env var — email: %s",
                creds.service_account_email,
            )
            return creds
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(
                "GOOGLE_APPLICATION_CREDENTIALS_JSON env var is set but contains "
                f"invalid JSON: {exc}"
            ) from exc

    # Fallback: load from local credentials.json file
    if not Path(CREDENTIALS_PATH).exists():
        raise FileNotFoundError(
            f"credentials.json not found at '{CREDENTIALS_PATH}'. "
            "Either set the GOOGLE_APPLICATION_CREDENTIALS_JSON environment variable "
            "or download the Service Account key from Google Cloud Console and place "
            "it in the api/ directory."
        )

    creds = service_account.Credentials.from_service_account_file(
        CREDENTIALS_PATH, scopes=SCOPES
    )
    logger.info(
        "Service Account credentials loaded — email: %s",
        creds.service_account_email,
    )
    return creds


# ──────────────────────────────────────────────────────────────────────────────
# Step 1 — List all files in the Drive folder
# ──────────────────────────────────────────────────────────────────────────────

def list_drive_files(drive_service, folder_id: str) -> list[dict]:
    """
    Returns all files (not sub-folders) inside the specified Drive folder.

    Uses pagination to handle folders with more than 1,000 files.

    Args:
        drive_service: An authenticated Google Drive API resource.
        folder_id:     The Drive folder ID to list.

    Returns:
        A list of dicts, each with keys: id, name, mimeType.
    """
    files: list[dict] = []
    page_token: Optional[str] = None

    # Build the query: children of the target folder that are NOT folders themselves.
    query = (
        f"'{folder_id}' in parents "
        f"and mimeType != 'application/vnd.google-apps.folder' "
        f"and trashed = false"
    )

    while True:
        kwargs = {
            "q": query,
            "fields": "nextPageToken, files(id, name, mimeType)",
            "pageSize": 100,
        }
        if page_token:
            kwargs["pageToken"] = page_token

        response = drive_service.files().list(**kwargs).execute()
        files.extend(response.get("files", []))

        page_token = response.get("nextPageToken")
        if not page_token:
            break   # All pages consumed.

    logger.info("Found %d file(s) in folder '%s'.", len(files), folder_id)
    return files


# ──────────────────────────────────────────────────────────────────────────────
# Step 2a — Extract text from standard files (PDF, Docs)
# ──────────────────────────────────────────────────────────────────────────────

def download_file_bytes(drive_service, file_id: str, mime_type: str) -> bytes:
    """
    Downloads or exports a Drive file and returns its raw bytes.

    Google Workspace documents (Docs, Slides, etc.) cannot be downloaded
    directly — they must be exported to a compatible MIME type first.
    Binary files (PDF, DOCX) are downloaded as-is.

    Args:
        drive_service: Authenticated Drive API resource.
        file_id:       Drive file ID.
        mime_type:     The file's native MIME type.

    Returns:
        The file content as a bytes object.
    """
    buffer = io.BytesIO()

    if mime_type in EXPORT_MIME_MAP:
        # Export Google Workspace format to a downloadable format.
        export_mime = EXPORT_MIME_MAP[mime_type]
        request = drive_service.files().export_media(
            fileId=file_id, mimeType=export_mime
        )
    else:
        # Binary file — download directly.
        request = drive_service.files().get_media(fileId=file_id)

    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()

    return buffer.getvalue()


def extract_text_from_pdf(file_bytes: bytes) -> str:
    """
    Extracts all text from a PDF using PyMuPDF (fitz).

    PyMuPDF is significantly faster and more accurate than pdfplumber / pdfminer
    for most regulatory PDF documents.

    Args:
        file_bytes: Raw bytes of the PDF file.

    Returns:
        The full extracted text as a single string.
    """
    text_parts: list[str] = []

    # Open from a bytes buffer — no temp file needed.
    with fitz.open(stream=file_bytes, filetype="pdf") as doc:
        for page_num, page in enumerate(doc, start=1):
            page_text = page.get_text("text")   # plain text, preserves layout
            if page_text.strip():
                # Tag each page so chunks can reference their source page number.
                text_parts.append(f"[Halaman {page_num}]\n{page_text}")

    return "\n\n".join(text_parts)


def extract_text_from_docx(file_bytes: bytes) -> str:
    """
    Extracts all text from a Word (.docx) document using python-docx.

    Args:
        file_bytes: Raw bytes of the DOCX file.

    Returns:
        The full extracted text as a single string.
    """
    buffer = io.BytesIO(file_bytes)
    doc = DocxDocument(buffer)

    paragraphs = [para.text for para in doc.paragraphs if para.text.strip()]
    return "\n\n".join(paragraphs)


# ──────────────────────────────────────────────────────────────────────────────
# Step 2b — Convert Google Sheets into rich structured text chunks
# ──────────────────────────────────────────────────────────────────────────────

def extract_chunks_from_sheet(
    gc: gspread.Client,
    file_id: str,
    file_name: str,
) -> list[dict]:
    """
    Reads all worksheets in a Google Spreadsheet and converts tabular rows into
    semantically rich text chunks that preserve column context for the LLM.

    Instead of dumping all cells as raw CSV (which destroys column meaning),
    this function generates one text block per row in the format:

        **Nama Persyaratan**: Gambar Situasi
        **Keterangan**: Diperlukan untuk menentukan lokasi bangunan
        **Tipe File**: PDF / JPG
        **Wajib**: Ya
        ...

    This ensures the LLM can retrieve "what file type is needed for Gambar
    Situasi?" correctly, rather than getting confused by tabular proximity.

    Args:
        gc:        Authenticated gspread client.
        file_id:   Drive file ID of the spreadsheet.
        file_name: Human-readable file name for metadata tagging.

    Returns:
        A list of chunk dicts: {"text": str, "id": str, "source": str}
    """
    chunks: list[dict] = []

    # Open the spreadsheet by its Drive file ID.
    spreadsheet = gc.open_by_key(file_id)

    for worksheet in spreadsheet.worksheets():
        sheet_name = worksheet.title
        logger.info("   Processing worksheet: '%s' in '%s'", sheet_name, file_name)

        # Fetch all values as a list-of-lists (header row + data rows).
        all_values = worksheet.get_all_values()

        if not all_values or len(all_values) < 2:
            logger.warning("   Worksheet '%s' is empty or has no data rows. Skipping.", sheet_name)
            continue

        # --- Detect header row ---
        # The first non-empty row is treated as the header.
        header_row = all_values[0]
        data_rows  = all_values[1:]

        # Strip whitespace from headers and replace empty header cells with
        # positional labels like "Kolom_1", "Kolom_2" to avoid losing columns.
        headers = [
            h.strip() if h.strip() else f"Kolom_{i+1}"
            for i, h in enumerate(header_row)
        ]

        logger.info(
            "   %d column(s) detected: %s",
            len(headers),
            ", ".join(f"'{h}'" for h in headers),
        )

        # Convert to a Pandas DataFrame for convenient processing.
        df = pd.DataFrame(data_rows, columns=headers)

        # Drop rows where ALL cells are empty (completely blank rows in the sheet).
        df = df.dropna(how="all").reset_index(drop=True)
        df = df[df.apply(lambda row: any(str(v).strip() for v in row), axis=1)]

        sheet_chunks: list[dict] = []

        # --- Generate a text chunk for every meaningful row ---
        for row_idx, (_, row) in enumerate(df.iterrows()):
            # Build the structured key-value block for this row.
            lines: list[str] = [
                f"[Sumber: {file_name} | Sheet: {sheet_name} | Baris {row_idx + 2}]"
            ]

            any_content = False
            for col_name in headers:
                cell_value = str(row.get(col_name, "")).strip()

                # Skip columns where the cell is empty to keep chunks clean.
                if not cell_value or cell_value.lower() in ("nan", "none", ""):
                    continue

                # Format as bold key: value (Markdown-style for readability).
                lines.append(f"**{col_name}**: {cell_value}")
                any_content = True

            # Skip rows that produced no content after filtering blank cells.
            if not any_content:
                continue

            chunk_text = "\n".join(lines)

            # Generate a stable, deterministic ID for this chunk.
            # This allows ChromaDB's `upsert` to overwrite stale chunks on re-run.
            chunk_id = _make_chunk_id(f"{file_id}_{sheet_name}_{row_idx}")

            sheet_chunks.append({
                "id":     chunk_id,
                "text":   chunk_text,
                "source": f"{file_name} | Sheet: {sheet_name}",
                "type":   "sheet_row",
            })

        logger.info(
            "   Generated %d row chunk(s) from worksheet '%s'.",
            len(sheet_chunks),
            sheet_name,
        )
        chunks.extend(sheet_chunks)

    return chunks


# ──────────────────────────────────────────────────────────────────────────────
# Step 3 — Slide-window text chunker for standard documents
# ──────────────────────────────────────────────────────────────────────────────

def chunk_text(
    text: str,
    source: str,
    file_id: str,
    chunk_size: int = CHUNK_SIZE,
    chunk_overlap: int = CHUNK_OVERLAP,
) -> list[dict]:
    """
    Splits a long text string into overlapping chunks using a simple sliding
    character-window algorithm.

    Overlap ensures that sentences split across chunk boundaries are still
    fully retrievable.

    Args:
        text:         The full text to split.
        source:       Human-readable label for metadata (file name).
        file_id:      Drive file ID used to build deterministic chunk IDs.
        chunk_size:   Maximum character length per chunk.
        chunk_overlap: Character overlap between consecutive chunks.

    Returns:
        A list of chunk dicts: {"id": str, "text": str, "source": str, "type": str}
    """
    # Normalise whitespace: collapse multiple blank lines into one.
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    chunks: list[dict] = []
    start = 0
    chunk_idx = 0

    while start < len(text):
        end = start + chunk_size

        # Try to end the chunk at a natural sentence/paragraph boundary so we
        # don't cut mid-word. Walk back from `end` to find a good break point.
        if end < len(text):
            # Look for the last newline or period before the hard cut-off.
            break_pos = text.rfind("\n", start, end)
            if break_pos == -1 or (end - break_pos) > 200:
                # No newline found nearby — fall back to the last space.
                break_pos = text.rfind(" ", start, end)
            if break_pos != -1:
                end = break_pos + 1   # include the whitespace char itself

        chunk_text_val = text[start:end].strip()
        if chunk_text_val:
            chunks.append({
                "id":     _make_chunk_id(f"{file_id}_chunk_{chunk_idx}"),
                "text":   chunk_text_val,
                "source": source,
                "type":   "document_chunk",
            })
            chunk_idx += 1

        # Move the window forward, but step back by `chunk_overlap` characters
        # so the next chunk sees the tail of the current one.
        start = end - chunk_overlap
        if start <= 0:
            break   # Safety guard against infinite loop on very short text.

    return chunks


def _make_chunk_id(raw: str) -> str:
    """
    Creates a short, deterministic, URL-safe ID from any string using SHA-256.

    Using a hash means the same source chunk always gets the same ID, so
    ChromaDB's `upsert()` will overwrite existing vectors on re-run instead
    of duplicating them.
    """
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


# ──────────────────────────────────────────────────────────────────────────────
# Step 4 — Embed chunks using Google Gemini
# ──────────────────────────────────────────────────────────────────────────────

def embed_chunks(chunks: list[dict]) -> list[dict]:
    """
    Calls the Gemini embedding API to generate a vector for each chunk's text.

    Uses the google.genai SDK (same as main.py).
    We embed chunks one by one with a 1-second delay. This guarantees we stay
    under the Gemini API free tier limit of 100 requests per minute.

    Args:
        chunks: List of chunk dicts (must have a "text" key).

    Returns:
        The same list of dicts with an "embedding" key added to each.
    """
    embedded: list[dict] = []

    for i, chunk in enumerate(chunks, start=1):
        if i % 10 == 0 or i == 1 or i == len(chunks):
            logger.info("   Embedding chunk %d / %d ...", i, len(chunks))

        response = _genai_client.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=chunk["text"],
            config=google_genai.types.EmbedContentConfig(
                task_type=EMBEDDING_TASK_TYPE,
            ),
        )
        chunk["embedding"] = response.embeddings[0].values
        embedded.append(chunk)

        # 1 second delay per request = ~60 requests per minute.
        # Free tier allows 100 requests per minute.
        time.sleep(1.0)

    return embedded


# ──────────────────────────────────────────────────────────────────────────────
# Step 5 — Upsert into Pinecone
# ──────────────────────────────────────────────────────────────────────────────

def upsert_to_pinecone(
    index,
    chunks: list[dict],
) -> None:
    """
    Upserts embedded chunks into the Pinecone index.

    Args:
        index:      The target Pinecone Index object.
        chunks:     List of fully-embedded chunk dicts.
    """
    if not chunks:
        logger.warning("   No chunks to upsert.")
        return

    vectors = []
    for c in chunks:
        metadata = {
            "source": c.get("source", "unknown"),
            "type": c.get("type", "unknown"),
            "text": c["text"],  # Pinecone stores the text inside metadata
        }
        vectors.append({
            "id": c["id"],
            "values": c["embedding"],
            "metadata": metadata
        })

    # Pinecone recommends batching upserts in sizes of ~100
    BATCH_SIZE = 100
    for i in range(0, len(vectors), BATCH_SIZE):
        batch = vectors[i:i + BATCH_SIZE]
        index.upsert(vectors=batch)
        time.sleep(0.5)

    logger.info("   Upserted %d chunk(s) into Pinecone index '%s'.", len(chunks), PINECONE_INDEX_NAME)


# ──────────────────────────────────────────────────────────────────────────────
# Main Orchestration
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    """
    Full ingestion pipeline:

        1. Authenticate with Google APIs.
        2. List all files in the target Drive folder.
        3. For each file:
            a. Google Sheets -> row-by-row structured text chunks.
            b. PDF / Docs   -> extracted text -> sliding-window chunks.
        4. Embed all chunks with Gemini.
        5. Upsert into Pinecone.
    """
    logger.info("=" * 70)
    logger.info("PBG RAG Ingestion Pipeline Starting")
    logger.info("=" * 70)

    # -- 1. Authentication ---------------------------------------------------
    creds = build_credentials()

    # Google Drive REST API client.
    drive_service = build("drive", "v3", credentials=creds)

    # gspread client for the Sheets API (uses the same service account creds).
    gc = gspread.authorize(creds)

    # Initialise the google.genai client with your Gemini API key.
    gemini_api_key = os.environ.get("GEMINI_API_KEY", "")
    if not gemini_api_key:
        raise EnvironmentError(
            "GEMINI_API_KEY is not set. "
            "Add it to your .env file or environment variables."
        )
    global _genai_client
    _genai_client = google_genai.Client(api_key=gemini_api_key)
    logger.info("Google APIs authenticated.")

    # -- 2. Pinecone setup ---------------------------------------------------
    pinecone_api_key = os.environ.get("PINECONE_API_KEY", "")
    if not pinecone_api_key:
        raise EnvironmentError(
            "PINECONE_API_KEY is not set. Add it to your .env file."
        )

    pc = Pinecone(api_key=pinecone_api_key)
    index = pc.Index(PINECONE_INDEX_NAME)
    
    # Wait until index is ready before fetching stats
    logger.info("Pinecone index '%s' ready.", PINECONE_INDEX_NAME)

    # -- 3. List Drive files -------------------------------------------------
    files = list_drive_files(drive_service, DRIVE_FOLDER_ID)
    if not files:
        logger.warning("No files found in folder '%s'. Exiting.", DRIVE_FOLDER_ID)
        return

    all_chunks: list[dict] = []

    # -- 4. Process each file ------------------------------------------------
    for file_info in files:
        file_id   = file_info["id"]
        file_name = file_info["name"]
        mime_type = file_info["mimeType"]

        logger.info("")
        logger.info("Processing: '%s'  [%s]", file_name, mime_type)

        try:
            # -- Branch A: Google Sheets -------------------------------------
            if mime_type == MIME_GOOGLE_SHEET:
                logger.info("   Detected as Google Sheet — using structured row extractor.")
                sheet_chunks = extract_chunks_from_sheet(gc, file_id, file_name)
                all_chunks.extend(sheet_chunks)

            # -- Branch B: PDF -----------------------------------------------
            elif mime_type == MIME_PDF:
                logger.info("   Detected as PDF — downloading and extracting text.")
                raw_bytes = download_file_bytes(drive_service, file_id, mime_type)
                text = extract_text_from_pdf(raw_bytes)
                if text.strip():
                    doc_chunks = chunk_text(text, source=file_name, file_id=file_id)
                    logger.info("   Produced %d chunk(s) from PDF.", len(doc_chunks))
                    all_chunks.extend(doc_chunks)
                else:
                    logger.warning("   No text extracted from PDF '%s'. Skipping.", file_name)

            # -- Branch C: Google Docs (exported as DOCX) --------------------
            elif mime_type == MIME_GOOGLE_DOC:
                logger.info("   Detected as Google Doc — exporting as DOCX and extracting text.")
                raw_bytes = download_file_bytes(drive_service, file_id, mime_type)
                text = extract_text_from_docx(raw_bytes)
                if text.strip():
                    doc_chunks = chunk_text(text, source=file_name, file_id=file_id)
                    logger.info("   Produced %d chunk(s) from Google Doc.", len(doc_chunks))
                    all_chunks.extend(doc_chunks)
                else:
                    logger.warning("   No text extracted from Google Doc '%s'. Skipping.", file_name)

            # -- Branch D: Word documents (.docx) ----------------------------
            elif mime_type in (MIME_WORD_DOCX, MIME_WORD_OLD):
                logger.info("   Detected as Word document — downloading and extracting text.")
                raw_bytes = download_file_bytes(drive_service, file_id, mime_type)
                text = extract_text_from_docx(raw_bytes)
                if text.strip():
                    doc_chunks = chunk_text(text, source=file_name, file_id=file_id)
                    logger.info("   Produced %d chunk(s) from Word doc.", len(doc_chunks))
                    all_chunks.extend(doc_chunks)
                else:
                    logger.warning("   No text extracted from Word doc '%s'. Skipping.", file_name)

            else:
                logger.info("   Unsupported MIME type '%s' — skipping.", mime_type)

        except Exception as exc:  # noqa: BLE001
            # Log the error but continue processing remaining files.
            logger.error("   Error processing '%s': %s", file_name, exc, exc_info=True)

    # -- 5. Embed all chunks -------------------------------------------------
    logger.info("")
    logger.info("=" * 70)
    logger.info("Embedding %d total chunk(s) with model '%s' ...", len(all_chunks), EMBEDDING_MODEL)
    logger.info("=" * 70)

    if not all_chunks:
        logger.warning("No chunks collected. Nothing to embed. Exiting.")
        return

    embedded_chunks = embed_chunks(all_chunks)

    # -- 6. Upsert into Pinecone ---------------------------------------------
    logger.info("")
    logger.info("=" * 70)
    logger.info("Upserting %d embedded chunk(s) into Pinecone ...", len(embedded_chunks))
    logger.info("=" * 70)

    upsert_to_pinecone(index, embedded_chunks)

    logger.info("")
    logger.info("=" * 70)
    logger.info("Ingestion complete!")
    
    # Print index stats
    stats = index.describe_index_stats()
    total_vectors = stats.get('total_vector_count', 0)
    
    logger.info(
        "   Total vectors in Pinecone : %d",
        total_vectors,
    )
    logger.info("=" * 70)


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    main()

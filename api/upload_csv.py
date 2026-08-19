"""
upload_csv.py — Upload satu file CSV ke Pinecone dengan embedding Gemini.

Penggunaan:
    python upload_csv.py <path_ke_file.csv>

Contoh:
    python upload_csv.py "/Users/seantandjaja/Documents/PERIZINAN PBG - SYARAT.csv"
"""

import argparse
import csv
import hashlib
import logging
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from google import genai as google_genai
from pinecone import Pinecone

# ─────────────────────────────────────────────────────────────────────────────
# Bootstrap — load .env dari direktori yang sama dengan file ini
# ─────────────────────────────────────────────────────────────────────────────
_HERE = Path(__file__).resolve().parent
load_dotenv(dotenv_path=_HERE / ".env", override=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Konfigurasi
# ─────────────────────────────────────────────────────────────────────────────
PINECONE_INDEX: str = os.environ.get("PINECONE_INDEX_NAME", "pbg-knowledge")
GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "")
PINECONE_API_KEY: str = os.environ.get("PINECONE_API_KEY", "")

EMBEDDING_MODEL: str = "gemini-embedding-001"
EMBEDDING_DIM: int = 768
PINECONE_BATCH: int = 100
EMBED_BATCH_SIZE: int = 50
EMBED_DELAY_SEC: float = 1.5   # Jeda antar batch embedding (rate limit gratis)
RATE_LIMIT_WAIT: float = 30.0  # Jeda jika kena 429


# ─────────────────────────────────────────────────────────────────────────────
# Helper: Hashing
# ─────────────────────────────────────────────────────────────────────────────
def _md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def _make_vector_id(id_raw: str) -> str:
    return hashlib.sha256(id_raw.encode()).hexdigest()[:32]


# ─────────────────────────────────────────────────────────────────────────────
# Cek hash yang sudah ada di Pinecone (untuk skip kalau datanya sama)
# ─────────────────────────────────────────────────────────────────────────────
def fetch_existing_hashes(index, vector_ids: list[str]) -> dict[str, str]:
    existing: dict[str, str] = {}
    FETCH_BATCH = 100
    for start in range(0, len(vector_ids), FETCH_BATCH):
        batch_ids = vector_ids[start: start + FETCH_BATCH]
        for attempt in range(3):
            try:
                result = index.fetch(ids=batch_ids)
                for vid, vec_data in result.vectors.items():
                    meta = vec_data.metadata or {}
                    if "hash" in meta:
                        existing[vid] = meta["hash"]
                    elif "text" in meta:
                        existing[vid] = _md5(meta["text"])
                break
            except Exception as exc:
                if attempt == 2:
                    logger.error("Gagal fetch Pinecone: %s", exc)
                time.sleep(2)
    return existing


# ─────────────────────────────────────────────────────────────────────────────
# Baca CSV → list of dict row
# ─────────────────────────────────────────────────────────────────────────────
def read_csv(filepath: str) -> list[dict]:
    filename = Path(filepath).name
    rows_data: list[dict] = []

    encodings_to_try = ["utf-8-sig", "utf-8", "latin-1", "cp1252"]

    for encoding in encodings_to_try:
        try:
            with open(filepath, mode="r", encoding=encoding) as f:
                reader = csv.reader(f)
                headers = next(reader)
                headers = [h.strip() if h.strip() else f"Kolom_{i+1}" for i, h in enumerate(headers)]

                for row_idx, row in enumerate(reader):
                    lines = [f"[Sumber: {filename} | Baris {row_idx + 2}]"]
                    has_content = False
                    for col_name, val in zip(headers, row):
                        val = val.strip()
                        if val and val.lower() not in ("nan", "none", ""):
                            lines.append(f"**{col_name}**: {val}")
                            has_content = True

                    if not has_content:
                        continue

                    text = "\n".join(lines)
                    id_raw = f"{filename}_{row_idx}"

                    rows_data.append({
                        "id_raw": id_raw,
                        "text": text,
                        "filename": filename,
                        "row_num": row_idx + 2,
                        "vector_id": _make_vector_id(id_raw),
                        "content_hash": _md5(text),
                    })

            logger.info("✅ Berhasil membaca %d baris dari '%s' (encoding: %s).", len(rows_data), filename, encoding)
            return rows_data

        except UnicodeDecodeError:
            logger.warning("⚠️  Encoding '%s' gagal, mencoba berikutnya...", encoding)
            continue
        except Exception as e:
            logger.error("❌ Gagal membaca CSV: %s", e)
            return []

    logger.error("❌ Tidak dapat membaca file CSV dengan encoding apapun: %s", filepath)
    return []


# ─────────────────────────────────────────────────────────────────────────────
# Buat embedding teks dengan Gemini
# ─────────────────────────────────────────────────────────────────────────────
def embed_rows(genai_client, index, rows: list[dict]) -> list[dict]:
    embedded: list[dict] = []
    total = len(rows)

    for start in range(0, total, EMBED_BATCH_SIZE):
        batch = rows[start:start + EMBED_BATCH_SIZE]
        end_idx = min(start + len(batch), total)

        logger.info("  ⚙️  Embedding baris %d–%d dari %d...", start + 1, end_idx, total)
        texts = [r["text"] for r in batch]

        for attempt in range(5):
            try:
                resp = genai_client.models.embed_content(
                    model=EMBEDDING_MODEL,
                    contents=texts,
                    config=google_genai.types.EmbedContentConfig(
                        task_type="RETRIEVAL_DOCUMENT",
                        output_dimensionality=EMBEDDING_DIM,
                    ),
                )
                batch_embedded = []
                for i, r in enumerate(batch):
                    r["embedding"] = resp.embeddings[i].values
                    embedded.append(r)
                    batch_embedded.append(r)
                
                # LANGSUNG UPSERT SEKARANG JUGA
                upsert_rows(index, batch_embedded)
                break

            except Exception as exc:
                if "429" in str(exc) and attempt < 4:
                    wait = RATE_LIMIT_WAIT * (attempt + 1)
                    logger.warning(
                        "  ⏳ Rate limit Gemini — menunggu %.0f detik (percobaan %d/5)...", wait, attempt + 1
                    )
                    time.sleep(wait)
                else:
                    logger.error("  ❌ Gagal embedding batch ini (percobaan %d): %s", attempt + 1, exc)
                    if attempt == 4:
                        logger.error("  ⚠️  Batch %d–%d dilewati karena gagal terus.", start + 1, end_idx)
                    break

        # Jeda antar batch untuk menghindari rate limit gratis
        time.sleep(EMBED_DELAY_SEC)

    return embedded


# ─────────────────────────────────────────────────────────────────────────────
# Upload vector ke Pinecone
# ─────────────────────────────────────────────────────────────────────────────
def upsert_rows(index, embedded_rows: list[dict]) -> None:
    if not embedded_rows:
        return

    vectors = [
        {
            "id": r["vector_id"],
            "values": r["embedding"],
            "metadata": {
                "text": r["text"],
                "hash": r["content_hash"],
                "source": r["filename"],
                "category": "database_resmi",
            },
        }
        for r in embedded_rows
    ]

    for start in range(0, len(vectors), PINECONE_BATCH):
        batch = vectors[start: start + PINECONE_BATCH]
        for attempt in range(3):
            try:
                index.upsert(vectors=batch)
                logger.info(
                    "  📤 Batch %d berhasil diupload (%d vektor).",
                    start // PINECONE_BATCH + 1,
                    len(batch),
                )
                break
            except Exception as exc:
                if attempt == 2:
                    logger.error("  ❌ Gagal upload batch ke Pinecone setelah 3x percobaan: %s", exc)
                else:
                    logger.warning("  ⚠️  Gagal upload, mencoba lagi... (%s)", exc)
                    time.sleep(2)
        time.sleep(0.5)


# ─────────────────────────────────────────────────────────────────────────────
# Proses utama satu CSV
# ─────────────────────────────────────────────────────────────────────────────
def process_csv(filepath: str, force_reupload: bool = False, start_idx: int = 0) -> bool:
    logger.info("=" * 60)
    logger.info("📂 Memproses file: %s", filepath)
    logger.info("=" * 60)

    if not GEMINI_API_KEY:
        logger.error("❌ GEMINI_API_KEY tidak ditemukan di file .env")
        return False
    if not PINECONE_API_KEY:
        logger.error("❌ PINECONE_API_KEY tidak ditemukan di file .env")
        return False

    if not os.path.exists(filepath):
        logger.error("❌ File tidak ditemukan: %s", filepath)
        return False

    rows_data = read_csv(filepath)
    if not rows_data:
        logger.error("❌ Tidak ada data yang berhasil dibaca. Proses dihentikan.")
        return False
        
    end_val = args.end_idx if args.end_idx else len(rows_data)
    rows_data = rows_data[start_idx:end_val]
    logger.info(f"⏭️  Memproses {len(rows_data)} baris (dari {start_idx} sampai {end_val})")
    if args.end_idx is not None:
        logger.info(f"⏭️  Hanya memproses sampai index {args.end_idx}")
        # Karena kita sudah memotong start_idx, end_idx juga bergeser
        # Lebih aman potong berdasarkan index global.
        pass # Diubah di bawah

    # Inisialisasi klien
    genai_client = google_genai.Client(api_key=GEMINI_API_KEY)
    pc = Pinecone(api_key=PINECONE_API_KEY)
    index = pc.Index(PINECONE_INDEX)

    if force_reupload:
        logger.info("🔄 Mode force reupload — semua baris akan diproses ulang.")
        rows_to_process = rows_data
    else:
        logger.info("🔍 Mengecek Pinecone untuk data yang sudah ada...")
        all_vector_ids = [r["vector_id"] for r in rows_data]
        existing_hashes = fetch_existing_hashes(index, all_vector_ids)

        to_add, to_update, to_skip = [], [], []
        for r in rows_data:
            vid = r["vector_id"]
            if vid not in existing_hashes:
                to_add.append(r)
            elif existing_hashes[vid] != r["content_hash"]:
                to_update.append(r)
            else:
                to_skip.append(r)

        logger.info(
            "📊 Hasil cek: %d Baru | %d Diubah | %d Dilewati (tidak ada perubahan).",
            len(to_add), len(to_update), len(to_skip)
        )
        rows_to_process = to_add + to_update

    if rows_to_process:
        logger.info("⚙️  Membuat embedding untuk %d baris...", len(rows_to_process))
        embedded = embed_rows(genai_client, index, rows_to_process)
        logger.info("✅ Selesai! %d vektor berhasil diupload secara bertahap.", len(embedded))
    else:
        logger.info("✅ Tidak ada data baru. Semuanya sudah up-to-date!")

    return True


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Upload CSV ke Pinecone dengan embedding Gemini.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Contoh:
  python upload_csv.py "/Users/seantandjaja/Documents/PERIZINAN PBG - SYARAT.csv"
  python upload_csv.py "/Users/seantandjaja/Documents/PERIZINAN PBG - Transaksi2.csv" --force
        """,
    )
    parser.add_argument("filepath", help="Path lengkap ke file CSV yang ingin diupload.")
    parser.add_argument("--start-idx", type=int, default=0, help="Baris mulai (0-indexed).")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Paksa upload ulang semua baris, meski datanya tidak berubah.",
    )
    args = parser.parse_args()

    success = process_csv(args.filepath, force_reupload=args.force, start_idx=args.start_idx)
    exit(0 if success else 1)

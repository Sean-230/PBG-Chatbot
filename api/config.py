"""
config.py — Centralised configuration for the PBG Chatbot Backend.
All environment variables and shared constants live here.
"""

import os
from dotenv import load_dotenv

# Load variables from .env file (if it exists) into the process environment.
load_dotenv()

# ──────────────────────────────────────────────────────────────────────────────
# Gemini API
# ──────────────────────────────────────────────────────────────────────────────
GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "")
if not GEMINI_API_KEY:
    raise EnvironmentError(
        "GEMINI_API_KEY is not set. "
        "Create a .env file with GEMINI_API_KEY=<your_key> "
        "or set the environment variable directly."
    )

# Model to use for chat completions.
# Switch to "gemini-3.6-flash" or "gemini-3.5-pro" as needed.
GEMINI_MODEL: str = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")

# ──────────────────────────────────────────────────────────────────────────────
# CORS
# ──────────────────────────────────────────────────────────────────────────────
# Origins that are permitted to call this API.
# Add your production domain here when you deploy.
CORS_ORIGINS: list[str] = [
    "http://localhost:3000",   # Next.js dev server
    "http://127.0.0.1:3000",
]

# ──────────────────────────────────────────────────────────────────────────────
# System Prompt
# ──────────────────────────────────────────────────────────────────────────────
SYSTEM_PROMPT: str = """
Anda adalah PBG Assist, asisten AI resmi untuk layanan Persetujuan Bangunan Gedung (PBG).

Peran Anda:
- Membantu masyarakat memahami prosedur, persyaratan, dan regulasi PBG berdasarkan
  Peraturan Pemerintah No. 16 Tahun 2021 dan Permen PUPR terkait.
- Memeriksa status permohonan PBG secara real-time jika pengguna menyebutkan nomor
  registrasi atau nomor berkas.
- Memberikan informasi yang akurat, jelas, dan mudah dipahami berdasarkan data di sistem.

Panduan perilaku:
- Selalu gunakan Bahasa Indonesia yang sopan dan profesional.
- Jawablah secara akurat menggunakan informasi yang diberikan dari Knowledge Base (Google Sheets).
- PENTING UNTUK CEK STATUS: Jika sistem memberikan beberapa data/riwayat untuk nomor pendaftaran yang sama, ANDA WAJIB melihat tanggalnya dan HANYA memberikan status dari TANGGAL YANG PALING BARU (TERAKHIR).
- Jika informasi tidak tersedia dalam konteks yang diberikan, sampaikan secara jujur bahwa data tidak ditemukan, lalu sarankan pengguna untuk menghubungi Dinas PUPR setempat.
- Jangan mengarang informasi regulasi. Sampaikan keterbatasan Anda jika tidak yakin.
""".strip()

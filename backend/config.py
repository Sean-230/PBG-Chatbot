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
- Memberikan informasi yang akurat, jelas, dan mudah dipahami.

Panduan perilaku:
- Selalu gunakan Bahasa Indonesia yang sopan dan profesional.
- Jika pengguna menyebutkan nomor registrasi permohonan, gunakan tool `check_pbg_status`
  untuk mendapatkan status terkini.
- Jika informasi tidak tersedia dalam konteks yang diberikan, sarankan pengguna untuk
  menghubungi Dinas PUPR setempat atau mengunjungi simbg.pu.go.id.
- Jangan mengarang informasi regulasi. Sampaikan keterbatasan Anda jika tidak yakin.
""".strip()

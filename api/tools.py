"""
tools.py — Gemini Tool (Function Calling) definitions for the PBG Chatbot.

This module declares all tools that the Gemini model is allowed to call,
the Python functions that execute them, and a registry for clean dispatch.

──────────────────────────────────────────────────────────────────────────────
HOW TO ADD A NEW TOOL
──────────────────────────────────────────────────────────────────────────────
1. Write a plain Python function that performs the real logic (DB call, HTTP
   request, scraper, etc.).
2. Add a corresponding `types.FunctionDeclaration` entry to TOOL_DECLARATIONS,
   describing the tool's parameters in JSON Schema format.
3. Register the function in TOOL_REGISTRY using the *exact same name* as the
   FunctionDeclaration.
4. Gemini will automatically call it when the user's query warrants it.
──────────────────────────────────────────────────────────────────────────────
"""

import json
from google.genai import types

# ──────────────────────────────────────────────────────────────────────────────
# Tool Implementations
# ──────────────────────────────────────────────────────────────────────────────

def check_pbg_status(registration_id: str) -> str:
    """
    Checks the current status of a PBG application by searching the
    TRANSAKSI data stored in Pinecone for the given registration number.

    Args:
        registration_id: The No. Daftar / berkas number provided by the user.

    Returns:
        A JSON string representing the found records, or a not-found message.
    """
    import json
    from rag_stub import _get_index

    registration_id = registration_id.strip()
    index = _get_index()

    if index is None:
        return json.dumps({
            "status": "error",
            "message": "Koneksi ke database tidak tersedia.",
        }, ensure_ascii=False)

    try:
        # Use a tiny random vector to sample vectors broadly.
        # Pinecone's ANN index returns 0 results for zero vectors.
        import random
        sample_vec = [random.uniform(-0.001, 0.001) for _ in range(768)]

        results = index.query(
            vector=sample_vec,
            top_k=300,
            include_metadata=True,
        )

        matches = results.get("matches", [])

        # Search for the registration number in the text of each chunk
        found_records = []
        for match in matches:
            meta = match.get("metadata", {})
            text = meta.get("text", "")
            source = meta.get("source", "")

            # Only look in TRANSAKSI tab
            if "TRANSAKSI" not in source.upper():
                continue

            # Check if registration_id appears in the text (case-insensitive)
            if registration_id in text or registration_id.lower() in text.lower():
                found_records.append(text)

        if not found_records:
            return json.dumps({
                "registration_id": registration_id,
                "status": "Tidak ditemukan",
                "message": (
                    f"Nomor daftar '{registration_id}' tidak ditemukan dalam database TRANSAKSI. "
                    "Pastikan nomor yang Anda masukkan sudah benar."
                ),
            }, ensure_ascii=False)

        return json.dumps({
            "registration_id": registration_id,
            "status": "Ditemukan",
            "records": found_records,
        }, ensure_ascii=False)

    except Exception as exc:
        return json.dumps({
            "status": "error",
            "message": f"Terjadi kesalahan saat mengakses database: {exc}",
        }, ensure_ascii=False)


# ──────────────────────────────────────────────────────────────────────────────
# Tool Registry — maps function name → callable for dispatcher in main.py
# ──────────────────────────────────────────────────────────────────────────────
TOOL_REGISTRY: dict[str, callable] = {
    "check_pbg_status": check_pbg_status,
}

# ──────────────────────────────────────────────────────────────────────────────
# Tool Declarations — passed to the Gemini model so it knows what's available
# ──────────────────────────────────────────────────────────────────────────────
TOOL_DECLARATIONS = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="check_pbg_status",
            description=(
                "Memeriksa status real-time permohonan Persetujuan Bangunan Gedung (PBG) "
                "berdasarkan nomor registrasi atau nomor berkas yang diberikan pengguna. "
                "Gunakan tool ini setiap kali pengguna menyebutkan nomor berkas, "
                "nomor registrasi, atau meminta informasi status permohonan mereka."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "registration_id": types.Schema(
                        type=types.Type.STRING,
                        description=(
                            "Nomor registrasi atau nomor berkas permohonan PBG. "
                            "Contoh: 'BG123456', 'PBG-2024-001', dll."
                        ),
                    )
                },
                required=["registration_id"],
            ),
        )
    ]
)

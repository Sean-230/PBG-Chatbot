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
    Checks the current status of a PBG (Persetujuan Bangunan Gedung) application.

    In production, replace this stub with a real HTTP call to the SIMBG API
    or a web scraper targeting simbg.pu.go.id.

    Args:
        registration_id: The registration/berkas number provided by the user.

    Returns:
        A JSON string representing the current application status.
    """
    # TODO: Replace this hardcoded mock with a real SIMBG API call or scraper.
    # Example production implementation:
    #   response = httpx.get(f"https://simbg.pu.go.id/api/status/{registration_id}")
    #   return response.text

    mock_statuses = {
        "DEFAULT": {
            "registration_id": registration_id,
            "status": "Menunggu Sidang TPT",
            "stage": "Verifikasi Teknis",
            "submitted_date": "2025-07-14",
            "estimated_completion_days": 14,
            "officer": "Dinas PUPR Kota - Seksi Bangunan Gedung",
            "notes": (
                "Berkas Anda sedang dalam antrian sidang Tim Profesi Ahli (TPA). "
                "Harap menunggu notifikasi melalui email terdaftar."
            ),
        },
        "BG999": {
            "registration_id": "BG999",
            "status": "PBG Diterbitkan",
            "stage": "Selesai",
            "submitted_date": "2025-05-01",
            "issued_date": "2025-06-20",
            "estimated_completion_days": 0,
            "notes": "PBG telah diterbitkan. Silakan unduh dokumen dari portal SIMBG.",
        },
    }

    result = mock_statuses.get(registration_id.upper(), mock_statuses["DEFAULT"])
    return json.dumps(result, ensure_ascii=False)


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

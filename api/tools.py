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
import os
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
        try:
            from rag_stub import _get_genai_client
            client = _get_genai_client()
            if not client:
                raise ValueError("No Gemini client")
            
            from google import genai
            response = client.models.embed_content(
                model="gemini-embedding-001",
                contents=f"Nomor pendaftaran {registration_id}",
                config=genai.types.EmbedContentConfig(
                    task_type="RETRIEVAL_QUERY",
                    output_dimensionality=768,
                ),
            )
            query_vector = response.embeddings[0].values
        except Exception as exc:
            import random
            query_vector = [random.uniform(-0.001, 0.001) for _ in range(768)]

        results = index.query(
            vector=query_vector,
            top_k=30,
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

def check_brangkas(registration_id: str) -> str:
    """
    Mencari dokumen/gambar terkait nomor pendaftaran di folder Google Drive
    dan mengembalikan link untuk ditampilkan kepada user.
    """
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    
    registration_id = registration_id.strip()
    
    import os
    _HERE = os.path.dirname(os.path.abspath(__file__))
    
    # Load credentials
    credentials_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if credentials_path and credentials_path.startswith("./"):
        credentials_path = os.path.join(_HERE, credentials_path[2:])
    elif not credentials_path or not os.path.exists(credentials_path):
        credentials_path = os.path.join(_HERE, "credentials.json")
        
    try:
        creds = service_account.Credentials.from_service_account_file(
            credentials_path, scopes=['https://www.googleapis.com/auth/drive.readonly']
        )
        service = build('drive', 'v3', credentials=creds, cache_discovery=False)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"Gagal autentikasi Google Drive: {e}"})

    parent_folder_id = os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "19RYdniy0kVtlqpdslman6Vg_wfB0CUsW")

    try:
        # Step 1: Find the 'DOKUMEN (NAMA FOLDER BY NO DAFTAR)' subfolder
        query_doc_folder = f"name = 'DOKUMEN (NAMA FOLDER BY NO DAFTAR)' and '{parent_folder_id}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        doc_folder_results = service.files().list(q=query_doc_folder, fields="files(id)").execute()
        doc_folders = doc_folder_results.get('files', [])
        
        search_parent_id = doc_folders[0]['id'] if doc_folders else parent_folder_id

        # Step 2: Search for the specific subfolder (registration_id) inside the parent folder
        query = f"name = '{registration_id}' and '{search_parent_id}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        results = service.files().list(q=query, fields="files(id, name)").execute()
        folders = results.get('files', [])

        if not folders:
            return json.dumps({
                "status": "Tidak ditemukan", 
                "message": f"Brangkas/folder untuk no daftar {registration_id} tidak ditemukan di Google Drive."
            })
        
        folder_id = folders[0]['id']
        
        # Now get all files inside this subfolder
        query_files = f"'{folder_id}' in parents and trashed = false"
        results_files = service.files().list(q=query_files, fields="files(id, name, mimeType, webViewLink, webContentLink, thumbnailLink, hasThumbnail)").execute()
        files = results_files.get('files', [])

        if not files:
            return json.dumps({
                "status": "Kosong",
                "message": f"Brangkas {registration_id} ditemukan, tetapi tidak ada file di dalamnya."
            })

        # Format the result
        file_list = []
        for f in files:
            file_id = f.get('id')
            
            # Extract thumbnail link if available and increase its resolution from s220 to s500 for better preview
            thumb_link = f.get('thumbnailLink', '')
            if thumb_link:
                thumb_link = thumb_link.replace('=s220', '=s500')
                
            embed_link = f"https://drive.google.com/uc?export=view&id={file_id}" if file_id else f.get('webViewLink')

            file_list.append({
                "name": f.get("name"),
                "mimeType": f.get("mimeType"),
                "hasThumbnail": f.get("hasThumbnail", False),
                "thumbnail_link": thumb_link,
                "view_link": f.get("webViewLink"),
                "embed_link": embed_link
            })

        return json.dumps({
            "status": "Ditemukan",
            "registration_id": registration_id,
            "message": f"Ditemukan {len(file_list)} file di brangkas {registration_id}.",
            "files": file_list,
            "instructions_for_ai": "Tampilkan file-file ini kepada user sebagai PREVIEW GAMBAR yang bisa diklik. Format HANYA menggunakan Markdown Image bersarang di dalam Link: `[![Nama File](thumbnail_link)](view_link)`. Jika thumbnail_link kosong, gunakan format link biasa: `[Nama File](view_link)`."
        }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"status": "error", "message": f"Error mengakses Google Drive API: {e}"})


# ──────────────────────────────────────────────────────────────────────────────
# Tool Registry — maps function name → callable for dispatcher in main.py
# ──────────────────────────────────────────────────────────────────────────────
TOOL_REGISTRY: dict[str, callable] = {
    "check_pbg_status": check_pbg_status,
    "check_brangkas": check_brangkas,
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
        ),
        types.FunctionDeclaration(
            name="check_brangkas",
            description=(
                "Mengambil file dan dokumen gambar dari brangkas Google Drive berdasarkan nomor pendaftaran (no daftar). "
                "Gunakan tool ini jika pengguna meminta untuk 'cek brangkas', 'tampilkan dokumen', atau "
                "meminta melihat file terkait suatu nomor pendaftaran (misalnya: 'cek brangkas 6680')."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "registration_id": types.Schema(
                        type=types.Type.STRING,
                        description="Nomor pendaftaran atau nomor berkas (misalnya '6680').",
                    )
                },
                required=["registration_id"],
            ),
        )
    ]
)

import csv
import json

def search_local_csv(query: str) -> str:
    """
    Mencari data transaksi atau pemohon di dalam file database CSV lokal.
    Sangat berguna untuk mencari nama pemohon, nomor pendaftaran, atau NIK.
    
    Args:
        query: Kata kunci pencarian (misalnya: "Budi", "PBG-12345")
        
    Returns:
        String berisi format JSON dari baris-baris yang cocok.
    """
    results = []
    
    # Paths ke file CSV lokal
    transaksi_path = os.path.join(_HERE, "data", "Transaksi2.csv")
    syarat_path = os.path.join(_HERE, "data", "SYARAT.csv")
    
    files_to_search = [
        ("Data Transaksi", transaksi_path),
        ("Persyaratan", syarat_path)
    ]
    
    query_lower = query.lower()
    
    for file_label, file_path in files_to_search:
        if not os.path.exists(file_path):
            continue
            
        try:
            with open(file_path, mode='r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # Gabungkan semua value di row ini menjadi satu string untuk dicari
                    row_content = " ".join([str(v).lower() for v in row.values() if v])
                    if query_lower in row_content:
                        # Clean up row (remove empty keys/values)
                        clean_row = {k: v for k, v in row.items() if k and v and str(v).strip()}
                        clean_row["_source"] = file_label
                        results.append(clean_row)
                        
                        # Batasi hasil agar tidak meledakkan token (max 10 hasil)
                        if len(results) >= 15:
                            return json.dumps(results, indent=2, ensure_ascii=False) + "\n(Ada lebih banyak hasil, tapi dibatasi 15 untuk menghemat memori. Berikan kata kunci lebih spesifik.)"
        except Exception as e:
            pass # ignore errors gracefully
            
    if not results:
        return f"Tidak ditemukan data untuk kata kunci '{query}' di database lokal."
        
    return json.dumps(results, indent=2, ensure_ascii=False)

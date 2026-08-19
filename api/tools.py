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
from pathlib import Path
from google.genai import types

_HERE = str(Path(__file__).resolve().parent)

# ──────────────────────────────────────────────────────────────────────────────
# Tool Implementations
# ──────────────────────────────────────────────────────────────────────────────

def check_pbg_status(registration_id: str) -> str:
    """
    Checks the current status of a PBG application by searching the
    database locally for the given registration number.

    Args:
        registration_id: The No. Daftar / berkas number provided by the user.

    Returns:
        A JSON string representing the found records, or a not-found message.
    """
    try:
        import json
        # Panggil fungsi search_local_csv yang baru kita buat
        hasil_json = search_local_csv(registration_id)
        
        # Jika tidak ditemukan
        if "Tidak ditemukan" in hasil_json:
            return json.dumps({
                "registration_id": registration_id,
                "status": "Tidak ditemukan",
                "message": f"Nomor daftar '{registration_id}' tidak ditemukan dalam database TRANSAKSI lokal."
            }, ensure_ascii=False)
            
        # Kembalikan hasil mentah dari pencarian CSV
        return json.dumps({
            "registration_id": registration_id,
            "status": "Ditemukan",
            "history": json.loads(hasil_json) if hasil_json.startswith("[") else hasil_json
        }, ensure_ascii=False)

    except Exception as exc:
        import json
        return json.dumps({
            "status": "error",
            "message": f"Terjadi kesalahan saat mengakses database lokal: {exc}",
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
    try:
        creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
        if creds_json:
            import json
            creds_dict = json.loads(creds_json)
            creds = service_account.Credentials.from_service_account_info(
                creds_dict, scopes=['https://www.googleapis.com/auth/drive.readonly']
            )
        else:
            credentials_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
            if credentials_path and credentials_path.startswith("./"):
                credentials_path = os.path.join(_HERE, credentials_path[2:])
            elif not credentials_path or not os.path.exists(credentials_path):
                credentials_path = os.path.join(_HERE, "credentials.json")
                
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
                    # Prioritaskan exact match untuk No Daftar jika ada
                    is_match = False
                    if query_lower == str(row.get("No Daftar", "")).lower().strip():
                        is_match = True
                    elif query_lower in row_content:
                        is_match = True
                        
                    if is_match:
                        # Clean up row (remove empty keys/values)
                        clean_row = {k: v for k, v in row.items() if k and v and str(v).strip()}
                        clean_row["_source"] = file_label
                        results.append(clean_row)
                        
                        # Batasi hasil agar tidak meledakkan token (max 50 hasil)
                        if len(results) >= 50:
                            results.append({"_warning": "Ada lebih banyak hasil, tapi dibatasi 50 untuk menghemat memori. Tampilkan yang ada."})
                            return json.dumps(results, indent=2, ensure_ascii=False)
        except Exception as e:
            pass # ignore errors gracefully
            
    if not results:
        return f"Tidak ditemukan data untuk kata kunci '{query}' di database lokal."
        
    # Filter exact matches if they exist
    exact_matches = [r for r in results if str(r.get("No Daftar", "")).lower().strip() == query_lower]
    if exact_matches:
        results = exact_matches
        
    return json.dumps(results, indent=2, ensure_ascii=False)

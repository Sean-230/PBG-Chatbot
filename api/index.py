"""
main.py — FastAPI Application Entry Point for the PBG Chatbot Backend.

Start the server with:
    uvicorn api.index:app --reload --port 8000

The POST /chat endpoint:
  - Accepts a conversation history in OpenAI-style format
  - Passes it to Gemini with tool bindings
  - Manually handles function calls in a loop (for streaming compatibility)
  - Streams the final text response back to the client as plain text
"""

import json
import logging
import asyncio
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from google import genai
from google.genai import types
import firebase_admin
from firebase_admin import credentials, firestore

import sys
import os

# Fix gRPC DNS resolution issues on macOS
os.environ["GRPC_DNS_RESOLVER"] = "ares"

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from .config import (
    GEMINI_API_KEY,
    GEMINI_MODEL,
    CORS_ORIGINS,
    SYSTEM_PROMPT,
)
from .rag_stub import query_knowledge_base
from .tools import TOOL_DECLARATIONS, TOOL_REGISTRY

# ──────────────────────────────────────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# FastAPI App + CORS
# ──────────────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="PBG Assist — AI Backend",
    description="FastAPI backend powering the PBG Customer Support chatbot.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ──────────────────────────────────────────────────────────────────────────────
# Gemini Client (initialised once at startup)
# ──────────────────────────────────────────────────────────────────────────────
client = genai.Client(api_key=GEMINI_API_KEY)

# ──────────────────────────────────────────────────────────────────────────────
# Firebase Initialization
# ──────────────────────────────────────────────────────────────────────────────
_default_cred_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "credentials.json")
FIREBASE_CREDENTIALS_PATH = os.environ.get("FIREBASE_CREDENTIALS_PATH", _default_cred_path)
FIREBASE_SERVICE_ACCOUNT_JSON = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON")

try:
    if not firebase_admin._apps:
        if FIREBASE_SERVICE_ACCOUNT_JSON:
            import json
            cred_dict = json.loads(FIREBASE_SERVICE_ACCOUNT_JSON)
            cred = credentials.Certificate(cred_dict)
        else:
            cred = credentials.Certificate(FIREBASE_CREDENTIALS_PATH)
        firebase_admin.initialize_app(cred)
    db = firestore.client()
except Exception as e:
    logger.error(f"🔴 Firebase Initialization Failed: {e}", exc_info=True)
    db = None

@app.on_event("startup")
async def startup_event():
    if db:
        def test_connection():
            try:
                db.collection("pending_evaluations").limit(1).get()
                logger.info("🟢 Firebase Firestore Connected Successfully")
            except Exception as e:
                logger.error(f"🔴 Firebase connection test failed: {e}")
        
        asyncio.create_task(asyncio.to_thread(test_connection))

# ──────────────────────────────────────────────────────────────────────────────
# Request / Response Models
# ──────────────────────────────────────────────────────────────────────────────
class Message(BaseModel):
    role: str   # "user" or "assistant"
    content: str = ""
    parts: list[dict] = []

    @property
    def get_text(self) -> str:
        if self.content:
            return self.content
        if self.parts:
            return "".join([p.get("text", "") for p in self.parts if p.get("type") == "text"])
        return ""


class ChatRequest(BaseModel):
    messages: list[Message]

class EvaluateRequest(BaseModel):
    doc_id: str
    action: str

# ──────────────────────────────────────────────────────────────────────────────
# AI Interceptor & Smart Deduplication
# ──────────────────────────────────────────────────────────────────────────────
PENDING_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pending.json")

def load_pending_json():
    if os.path.exists(PENDING_FILE):
        with open(PENDING_FILE, "r") as f:
            return json.load(f)
    return {}

def save_pending_json(data):
    with open(PENDING_FILE, "w") as f:
        json.dump(data, f, indent=2)

async def intercept_and_log(user_query: str, ai_response: str):
    """
    Background task to intercept Scenario 2 and Scenario 3 responses,
    generate a topic slug, and save/update.
    """
    ai_response_stripped = ai_response.strip()
    is_scenario_2 = ai_response_stripped.startswith("Berdasarkan informasi dari panduan resmi")
    is_scenario_3 = ai_response_stripped.startswith("Secara umum terkait pedoman perizinan bangunan")

    if not (is_scenario_2 or is_scenario_3):
        return

    scenario_name = "Scenario 2 (External Web)" if is_scenario_2 else "Scenario 3 (General AI Knowledge)"
    logger.info(f"Intercepted {scenario_name} response. Extracting topic...")

    try:
        # Lightweight LLM call
        gen_config = types.GenerateContentConfig(
            system_instruction="You are a text extractor. Extract a 1-3 word topic slug from the user's query. Use lowercase and underscores for spaces (e.g., 'syarat_sirkus'). Do not output anything else.",
            temperature=0.1,
            max_output_tokens=10
        )
        topic_resp = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=user_query,
            config=gen_config
        )
        
        topic_slug = topic_resp.text.strip().lower().replace(" ", "_")
        if not topic_slug:
            topic_slug = "unknown_topic"
            
        logger.info(f"Topic extracted: {topic_slug}")

        # Fallback to local JSON if Firebase is broken
        def fallback_ops():
            import uuid
            import datetime
            data = load_pending_json()
            
            # Find existing
            existing_id = None
            for doc_id, doc in data.items():
                if doc.get("status") == "pending" and doc.get("topic") == topic_slug:
                    existing_id = doc_id
                    break
                    
            if existing_id:
                data[existing_id]["queries"].append(user_query)
                data[existing_id]["count"] += 1
                logger.info(f"Updated local JSON for topic: {topic_slug}")
            else:
                doc_id = str(uuid.uuid4())
                data[doc_id] = {
                    "topic": topic_slug,
                    "queries": [user_query],
                    "ai_response": ai_response,
                    "count": 1,
                    "timestamp": datetime.datetime.now().isoformat(),
                    "status": "pending",
                    "scenario": scenario_name
                }
                logger.info(f"Created local JSON for topic: {topic_slug}")
            save_pending_json(data)

        # Use Firebase if connected, otherwise use JSON fallback
        if db:
            def firestore_ops():
                pending_ref = db.collection("pending_evaluations")
                query = pending_ref.where("status", "==", "pending").where("topic", "==", topic_slug).limit(1)
                docs = query.get()
                
                if docs:
                    doc = docs[0]
                    doc.reference.update({
                        "queries": firestore.ArrayUnion([user_query]),
                        "count": firestore.Increment(1)
                    })
                else:
                    pending_ref.add({
                        "topic": topic_slug,
                        "queries": [user_query],
                        "ai_response": ai_response,
                        "count": 1,
                        "timestamp": firestore.SERVER_TIMESTAMP,
                        "status": "pending",
                        "scenario": scenario_name
                    })
            try:
                await asyncio.to_thread(firestore_ops)
            except Exception as e:
                logger.error(f"Firestore deduplication failed, using JSON fallback: {e}")
                await asyncio.to_thread(fallback_ops)
        else:
            await asyncio.to_thread(fallback_ops)

    except Exception as e:
        logger.error(f"Error in interceptor: {e}")

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────
def build_gemini_history(messages: list[Message]) -> list[types.Content]:
    """
    Converts OpenAI-style message dicts into google.genai Content objects.
    Maps 'assistant' role → 'model' as required by the Gemini SDK.
    """
    history: list[types.Content] = []
    for msg in messages:
        role = "model" if msg.role == "assistant" else "user"
        history.append(
            types.Content(
                role=role,
                parts=[types.Part(text=msg.get_text)],
            )
        )
    return history


def dispatch_tool_call(function_call: types.FunctionCall) -> types.Content:
    """
    Executes a Gemini-requested tool call and returns the result as a
    Content object ready to be sent back to the model.
    """
    name = function_call.name
    args = dict(function_call.args) if function_call.args else {}

    logger.info(f"Tool call requested: {name}({args})")

    handler = TOOL_REGISTRY.get(name)
    if handler is None:
        result_str = json.dumps({"error": f"Unknown tool: {name}"})
    else:
        try:
            result_str = handler(**args)
        except Exception as exc:
            logger.error(f"Tool execution error [{name}]: {exc}")
            result_str = json.dumps({"error": str(exc)})

    logger.info(f"Tool result [{name}]: {result_str}")

    return types.Content(
        role="tool",
        parts=[
            types.Part(
                function_response=types.FunctionResponse(
                    name=name,
                    response={"result": result_str},
                )
            )
        ],
    )


# ──────────────────────────────────────────────────────────────────────────────
# Core Streaming Generator
# ──────────────────────────────────────────────────────────────────────────────
async def generate_stream(messages: list[Message]) -> AsyncGenerator[str, None]:
    """
    Async generator that:
    1. Optionally enriches the last user message with RAG context.
    2. Calls Gemini with the conversation history and tool bindings.
    3. Detects function_call parts and dispatches them synchronously.
    4. Continues the generation loop until a final text response is produced.
    5. Yields text chunks as they arrive for real-time streaming.
    """
    # ── RAG Injection ────────────────────────────────────────────────────────
    # TODO: When rag_stub.py is fully implemented, this will retrieve relevant
    #       PBG regulatory context and prepend it to the system prompt.
    last_user_question = next(
        (m.get_text for m in reversed(messages) if m.role == "user"), ""
    )
    rag_context = query_knowledge_base(last_user_question)

    triage_rules = (
        "=== ATURAN ATRIBUSI SUMBER & CARA MENJAWAB (WAJIB MUTLAK) ===\n\n"
        "WARNING: Falsely claiming general knowledge is from the RAG database is a critical failure. "
        "Anda DILARANG KERAS menggunakan pengetahuan internal Anda dan mengklaimnya sebagai data dari database internal.\n\n"

        "--- SCENARIO 1 (Database Match / Data ada di Sheet) ---\n"
        "Jika data/syarat yang ditanyakan ADA di teks konteks RAG di bawah:\n"
        "Langsung berikan jawabannya dengan natural. JIKA pengguna bertanya tentang syarat secara UMUM (belum menyebutkan jenis bangunannya), "
        "Anda WAJIB memberikan dua hal: (1) Sebutkan kategori-kategori bangunan yang ada di sistem BESERTA PENJELASAN SINGKAT DALAM KURUNG UNTUK SETIAP KATEGORI (misal: 'PBG Rumah Tinggal Sederhana (Untuk skala kecil/1-2 lantai)'), DAN (2) Sebutkan daftar persyaratan UMUM/DASAR yang berlaku untuk hampir semua kategori (seperti KRK, KTP, dll) beserta penjelasan singkatnya.\n"
        "Setelah itu baru tanyakan jenis bangunan mereka untuk memberikan syarat yang lebih spesifik.\n"
        "TIDAK PERLU memakai kalimat pembuka yang kaku (jangan pakai 'Berdasarkan informasi resmi...'). "
        "Gunakan format daftar (bullet points) agar rapi, komprehensif, dan mudah dibaca.\n\n"

        "--- SCENARIO 2 (Web/External Grounding) ---\n"
        "FRASA PEMBUKA WAJIB: \"Berdasarkan informasi dari panduan resmi ([Masukkan Link Di Sini]), ...\"\n"
        "KAPAN DIGUNAKAN: Ketika informasi TIDAK ADA di teks konteks fisik, tetapi Anda menemukan informasi faktual melalui "
        "Google Search atau website eksternal. Wajib menyertakan URL link yang valid. (Harus persis kalimat pembuka ini agar diverifikasi admin).\n\n"

        "--- SCENARIO 3 (General AI Knowledge / Fallback) ---\n"
        "FRASA PEMBUKA WAJIB: \"Secara umum terkait pedoman perizinan bangunan, ...\"\n"
        "KAPAN DIGUNAKAN: Jika Anda HARUS mengandalkan memori internal pelatihan Anda karena potongan database (RAG context) tidak memuat jawabannya. "
        "(Harus persis kalimat pembuka ini agar diverifikasi admin).\n\n"
    )

    if rag_context:
        effective_system_prompt = (
            f"{SYSTEM_PROMPT}\n\n"
            f"{triage_rules}"
            "=== KONTEKS DARI KNOWLEDGE BASE ===\n"
            "Data DITEMUKAN. Berikut adalah kutipan dokumen resmi:\n"
            f"{rag_context}\n"
            "===================================\n"
            "Jika konteks ini tidak relevan, gunakan tool Google Search untuk mencari referensi eksternal, atau gunakan pengetahuan umummu sesuai aturan Skenario."
        )
    else:
        effective_system_prompt = (
            f"{SYSTEM_PROMPT}\n\n"
            f"{triage_rules}"
            "=== KONTEKS DARI KNOWLEDGE BASE ===\n"
            "Data TIDAK DITEMUKAN atau KOSONG untuk pertanyaan ini.\n"
            "===================================\n"
            "Jika konteks ini tidak relevan, gunakan tool Google Search untuk mencari referensi eksternal, atau gunakan pengetahuan umummu sesuai aturan Skenario."
        )

    # ── Build conversation history ───────────────────────────────────────────
    contents = build_gemini_history(messages)

    # ── Generation config ────────────────────────────────────────────────────
    # Only enable Google Search grounding when local RAG has no context.
    # Google Search has a separate (strict) quota — using it on every request
    # will exhaust that quota quickly and block the entire chatbot.
    if rag_context:
        # RAG found relevant documents → no need for web search
        tools_list = [TOOL_DECLARATIONS]
        logger.info("Tools: custom tools only (RAG context available)")
    else:
        # No local context → enable Google Search as fallback
        tools_list = [TOOL_DECLARATIONS, {"google_search": {}}]
        logger.info("Tools: custom tools + Google Search (no RAG context)")

    gen_config = types.GenerateContentConfig(
        system_instruction=effective_system_prompt,
        temperature=0.4,
        max_output_tokens=2048,
        tools=tools_list,
    )

    # ── Agentic loop: keep iterating until no more tool calls ────────────────
    MAX_TOOL_ROUNDS = 5  # Safety limit to prevent infinite loops
    final_response_text = ""
    try:
        for round_num in range(MAX_TOOL_ROUNDS):
            logger.info(f"Gemini generation round {round_num + 1}")

            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=contents,
                config=gen_config,
            )

            candidate = response.candidates[0]
            has_tool_call = False

            for part in candidate.content.parts:
                # ── Function call part → dispatch and loop ───────────────────────
                if part.function_call:
                    has_tool_call = True
                    # Append the model's function_call turn to history
                    contents.append(candidate.content)
                    # Execute the tool and append its result
                    tool_result = dispatch_tool_call(part.function_call)
                    contents.append(tool_result)
                    break  # Restart the loop with updated history

                # ── Text part → stream it out ────────────────────────────────────
                if part.text:
                    final_response_text += part.text
                    # Format as Vercel AI SDK Data Stream protocol
                    yield f'0:{json.dumps(part.text)}\n'

            if not has_tool_call:
                # No tool was called; generation is complete.
                asyncio.create_task(intercept_and_log(last_user_question, final_response_text))
                break
        else:
            msg = "\n[Sistem: Terlalu banyak pemanggilan tool. Silakan coba lagi.]"
            yield f'0:{json.dumps(msg)}\n'

    except Exception as exc:
        error_msg = str(exc)
        logger.error(f"Gemini API error: {error_msg}")

        # ── Fallback: if 429 was caused by google_search quota, retry without it
        if ("429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg) and {"google_search": {}} in tools_list:
            logger.info("Retrying WITHOUT Google Search (grounding quota likely exhausted)")
            try:
                gen_config_fallback = types.GenerateContentConfig(
                    system_instruction=effective_system_prompt,
                    temperature=0.4,
                    max_output_tokens=2048,
                    tools=[TOOL_DECLARATIONS],
                )
                # Reset contents to original (remove any tool call artifacts)
                contents_fallback = build_gemini_history(messages)
                response = client.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=contents_fallback,
                    config=gen_config_fallback,
                )
                has_text = False
                for part in response.candidates[0].content.parts:
                    if part.text:
                        has_text = True
                        final_response_text += part.text
                        yield f'0:{json.dumps(part.text)}\n'
                
                if not has_text:
                    msg = "Mohon maaf, sistem sedang sibuk atau mengalami gangguan saat mencari data (fallback). Silakan coba lagi."
                    yield f'0:{json.dumps(msg)}\n'
                else:
                    asyncio.create_task(intercept_and_log(last_user_question, final_response_text))
                return
            except Exception as fallback_exc:
                error_msg = str(fallback_exc)
                logger.error(f"Fallback also failed: {error_msg}")

        if "API_KEY_INVALID" in error_msg or "INVALID_ARGUMENT" in error_msg or "401" in error_msg:
            msg = (
                "❌ **Konfigurasi API Key tidak valid.**\n\n"
                "Pastikan `GEMINI_API_KEY` di file `.env` adalah kunci Gemini yang benar "
                "(dimulai dengan `AIza...`). Dapatkan kunci di: "
                "https://aistudio.google.com/app/apikey"
            )
            yield f'0:{json.dumps(msg)}\n'
        elif "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
            msg = (
                "❌ **Kuota API Telah Habis (Error 429).**\n\n"
                "Anda telah melewati batas penggunaan API Gemini (rate limit/kuota harian). "
                "Silakan tunggu beberapa saat dan coba lagi, atau tingkatkan tier API Anda "
                "dengan menambahkan informasi billing di Google AI Studio."
            )
            yield f'0:{json.dumps(msg)}\n'
        else:
            msg = f"❌ Terjadi kesalahan pada server AI: {error_msg}"
            yield f'0:{json.dumps(msg)}\n'

# ──────────────────────────────────────────────────────────────────────────────
# API Endpoints
# ──────────────────────────────────────────────────────────────────────────────
@app.get("/", tags=["Health"])
async def health_check():
    """Simple health check endpoint."""
    return {"status": "ok", "service": "PBG Assist Backend", "version": "0.1.0"}


@app.get("/api/health", tags=["Health"])
async def api_health():
    """Health check endpoint accessible via the Next.js /api proxy."""
    return {"status": "ok"}


@app.post("/api/chat", tags=["Chat"])
async def chat(request: ChatRequest):
    """
    Accepts a conversation history and streams a Gemini-powered response.
    """
    if not request.messages:
        raise HTTPException(status_code=400, detail="messages list cannot be empty.")

    logger.info(f"POST /chat — {len(request.messages)} message(s) in history")

    return StreamingResponse(
        generate_stream(request.messages),
        media_type="text/plain",
        headers={
            # Disable buffering in proxies/nginx so chunks arrive immediately
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache",
        },
    )

# ── Admin Endpoints ───────────────────────────────────────────────────────────
@app.get("/api/admin/evaluations", tags=["Admin"])
async def get_evaluations(status: str = "pending"):
    """Fetch evaluations from Firestore by status."""
    try:
        def fetch():
            if not db: return []
            
            def process_docs(docs):
                res = []
                for doc in docs:
                    data = doc.to_dict()
                    data["id"] = doc.id
                    if "timestamp" in data and data["timestamp"]:
                        data["timestamp"] = data["timestamp"].isoformat() if hasattr(data["timestamp"], "isoformat") else str(data["timestamp"])
                    res.append(data)
                return res

            if status == "answered":
                docs_app = db.collection("pending_evaluations").where("status", "==", "approved").order_by("count", direction=firestore.Query.DESCENDING).get()
                docs_rej = db.collection("pending_evaluations").where("status", "==", "rejected").order_by("count", direction=firestore.Query.DESCENDING).get()
                combined = process_docs(docs_app) + process_docs(docs_rej)
                combined.sort(key=lambda x: x.get("count", 0), reverse=True)
                return combined
            else:
                docs = db.collection("pending_evaluations").where("status", "==", status).order_by("count", direction=firestore.Query.DESCENDING).get()
                return process_docs(docs)
                
        result = await asyncio.to_thread(fetch)
        return {"data": result}
    except Exception as e:
        logger.error(f"Error fetching evaluations: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/admin/evaluate/{doc_id}", tags=["Admin"])
async def delete_evaluation(doc_id: str):
    """Permanently delete an evaluation."""
    try:
        def do_delete():
            if db:
                db.collection("pending_evaluations").document(doc_id).delete()
                return True
            return False
        success = await asyncio.to_thread(do_delete)
        if not success:
            raise HTTPException(status_code=404, detail="Document not found.")
        return {"status": "success", "id": doc_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting evaluation: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/admin/evaluate", tags=["Admin"])
async def evaluate_pending(req: EvaluateRequest):
    """Approve or reject a pending evaluation."""
    if req.action not in ["approve", "reject"]:
        raise HTTPException(status_code=400, detail="Invalid action. Use 'approve' or 'reject'.")
        
    try:
        def update():
            if db:
                doc_ref = db.collection("pending_evaluations").document(req.doc_id)
                doc = doc_ref.get()
                if not doc.exists:
                    return False
                new_status = "approved" if req.action == "approve" else "rejected"
                doc_ref.update({"status": new_status})
                return True
            else:
                return False
            
        success = await asyncio.to_thread(update)
        if not success:
            raise HTTPException(status_code=404, detail="Document not found.")
            
        return {"status": "success", "action": f"{req.action}d", "id": req.doc_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating evaluation: {e}")
        raise HTTPException(status_code=500, detail=str(e))

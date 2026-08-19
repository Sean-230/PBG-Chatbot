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
import threading
from typing import AsyncGenerator, Optional

from fastapi import FastAPI, HTTPException, Request
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
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
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
    action: str  # "inject_kb" or "spam"
    admin_note: Optional[str] = None
    official_answer: Optional[str] = None


# ──────────────────────────────────────────────────────────────────────────────
# Golden Chunk Retrieval  (Admin-approved HITL knowledge)
# ──────────────────────────────────────────────────────────────────────────────

# (Golden Chunks are now fetched directly via Pinecone in rag_stub.py)


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

async def intercept_and_log(user_query: str, ai_response: str, golden_chunk_used: bool = False):
    """
    Background task that classifies the AI response into one of 4 scenarios
    and logs Scenario 2, 3, and 4 responses to Firestore for admin review.
    
    If golden_chunk_used is True, this query was already guided by an admin-approved
    instruction, so we skip logging to avoid evaluating the same case repeatedly.

    Classification strategy (order matters — most specific first):
      - Scenario 1 (RAG hit):  Response starts with the definitive RAG prefix.
                               These are correct answers — do NOT log.
      - Scenario 2 (Web):      Response contains a URL / web-grounding phrases.
      - Scenario 3 (Fallback): Response contains general-knowledge fallback phrases.
      - Scenario 4 (OOT):      Response contains apology/refusal phrases.
      - Catch-all:             Anything that isn't Scenario 1 gets logged as
                               Scenario 3 (safe default — better to over-log).
    """
    if golden_chunk_used:
        logger.debug("Interceptor: Golden Chunk used — skipping log.")
        return

    ai_response_stripped = ai_response.strip()
    response_lower = ai_response_stripped.lower()

    # ── Scenario 1 anchors — exact prefixes the prompt instructs the LLM to use
    # If ANY of these match, the LLM answered from the RAG DB → skip logging.
    SCENARIO_1_PREFIXES = (
        "berdasarkan informasi resmi dari database",
        "berdasarkan data resmi",
        "berdasarkan dokumen resmi",
        "berdasarkan ketentuan resmi",
        "berdasarkan persyaratan resmi",
    )
    is_scenario_1 = any(
        response_lower.startswith(prefix) for prefix in SCENARIO_1_PREFIXES
    )

    if is_scenario_1:
        logger.debug("Interceptor: Scenario 1 (RAG hit) — skipping log.")
        return

    # ── Scenario 2 signals — web/external grounding
    SCENARIO_2_SIGNALS = (
        "berdasarkan informasi dari panduan resmi",
        "berdasarkan informasi dari situs",
        "berdasarkan informasi dari website",
        "berdasarkan sumber eksternal",
        "http://",
        "https://",
        "www.",
    )
    is_scenario_2 = any(signal in response_lower for signal in SCENARIO_2_SIGNALS)

    # ── Scenario 3 signals — general AI knowledge fallback
    SCENARIO_3_SIGNALS = (
        "secara umum terkait pedoman perizinan",
        "secara umum,",
        "secara umum ",
        "berdasarkan pengetahuan umum",
        "berdasarkan informasi umum",
    )
    is_scenario_3 = (not is_scenario_2) and any(
        signal in response_lower for signal in SCENARIO_3_SIGNALS
    )

    # ── Scenario 4 signals — OOT / refusal / apology
    # Catches variations like "Mohon maaf, sebagai PBG Assist..."
    #                      and "Mohon maaf, saya adalah PBG Assist..."
    SCENARIO_4_SIGNALS = (
        "mohon maaf",
        "maaf, sebagai",
        "maaf, saya",
        "pertanyaan anda di luar",
        "pertanyaan ini di luar",
        "di luar cakupan",
        "tidak dapat membantu",
        "bukan dalam lingkup",
    )
    is_scenario_4 = (not is_scenario_2 and not is_scenario_3) and any(
        signal in response_lower for signal in SCENARIO_4_SIGNALS
    )

    # ── Catch-all: anything not Scenario 1 gets logged ───────────────────────
    # This is the safety net recommended in the brief: if it didn't match the
    # RAG prefix, it's an uncertain / improvised response that needs admin review.
    if not (is_scenario_2 or is_scenario_3 or is_scenario_4):
        is_scenario_3 = True  # Default to Scenario 3 — safest label for unknowns

    # ── Resolve scenario name ────────────────────────────────────────────────
    if is_scenario_2:
        scenario_name = "Scenario 2 (External Web)"
    elif is_scenario_4:
        scenario_name = "Scenario 4 (OOT)"
    else:
        scenario_name = "Scenario 3 (General AI Knowledge)"

    logger.info(f"Interceptor: classified as {scenario_name}. Extracting topic...")

    try:
        # Fetch existing pending topics to help LLM deduplicate
        existing_topics = []
        if db:
            try:
                # We do this synchronously in a thread
                def get_topics():
                    return [d.to_dict().get("topic") for d in db.collection("pending_evaluations").where("status", "==", "pending").get()]
                existing_topics = await asyncio.to_thread(get_topics)
                existing_topics = list(set([t for t in existing_topics if t]))
            except Exception as e:
                logger.warning(f"Could not fetch existing topics for dedup: {e}")

        topics_str = ", ".join(existing_topics) if existing_topics else "None"
        
        # Lightweight LLM call to extract a topic slug, guiding it to reuse existing ones
        gen_config = types.GenerateContentConfig(
            system_instruction=(
                "You are a deduplication router. The user has a query.\n"
                f"Existing pending topics: [{topics_str}]\n\n"
                "If the user's query semantically matches an existing topic (e.g. 'cara urus akte lahir' matches 'pembuatan_akte_lahir'), "
                "you MUST output exactly that existing topic.\n"
                "If it does NOT match, generate a NEW 1-3 word topic slug using lowercase and underscores (e.g., 'syarat_sirkus').\n"
                "Output ONLY the topic string and nothing else."
            ),
            temperature=0.1,
            max_output_tokens=15
        )
        topic_resp = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=user_query,
            config=gen_config
        )

        topic_slug = topic_resp.text.strip().lower().replace(" ", "_")
        if not topic_slug:
            topic_slug = "unknown_topic"

        logger.info(f"Topic extracted: {topic_slug} (from existing: {topic_slug in existing_topics})")

        # ── Firestore write (with JSON fallback) ─────────────────────────────
        def fallback_ops():
            import uuid
            import datetime
            data = load_pending_json()

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

        if db:
            def firestore_ops():
                pending_ref = db.collection("pending_evaluations")
                query = (
                    pending_ref
                    .where("status", "==", "pending")
                    .where("topic", "==", topic_slug)
                    .limit(1)
                )
                docs = query.get()

                if docs:
                    doc = docs[0]
                    doc.reference.update({
                        "queries": firestore.ArrayUnion([user_query]),
                        "count": firestore.Increment(1)
                    })
                    logger.info(f"Firestore: incremented count for topic '{topic_slug}'")
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
                    logger.info(f"Firestore: created new doc for topic '{topic_slug}'")
            try:
                await asyncio.to_thread(firestore_ops)
            except Exception as e:
                logger.error(f"Firestore write failed, using JSON fallback: {e}")
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


async def dispatch_tool_call(function_call: types.FunctionCall) -> types.Content:
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
            def _run_tool():
                return handler(**args)
            result_str = await asyncio.to_thread(_run_tool)
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
    
    # query_knowledge_base now returns both the RAG text and intercepted Golden Chunks from Pinecone!
    def fetch_rag():
        return query_knowledge_base(last_user_question)
        
    rag_context, golden_chunks = await asyncio.to_thread(fetch_rag)

    # Build the golden-chunk prompt block if any matches were found
    if golden_chunks:
        golden_block_lines = [
            "=== INSTRUKSI KHUSUS DARI ADMIN (PRIORITAS TERTINGGI) ===",
            "Berikut adalah instruksi dari admin untuk pertanyaan serupa dengan yang ditanyakan user.",
            "IKUTI instruksi ini dengan KETAT saat menyusun jawabanmu.\n",
        ]
        for i, chunk in enumerate(golden_chunks, 1):
            golden_block_lines.append(f"[Instruksi Admin #{i} — Topik: {chunk['topic']}]")
            if chunk["admin_note"]:
                golden_block_lines.append(f"📋 Instruksi: {chunk['admin_note']}")
            golden_block_lines.append("")
        golden_block_lines.append("=" * 60)
        golden_prompt_block = "\n".join(golden_block_lines) + "\n\n"
    else:
        golden_prompt_block = ""

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
            f"{golden_prompt_block}"
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
            f"{golden_prompt_block}"
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
    
    for round_num in range(MAX_TOOL_ROUNDS):
        try:
            logger.info(f"Gemini generation round {round_num + 1}")

            def _gen():
                return client.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=contents,
                    config=gen_config,
                )
            response = await asyncio.to_thread(_gen)

            candidate = response.candidates[0]
            has_tool_call = False

            for part in candidate.content.parts:
                # ── Function call part → dispatch and loop ───────────────────────
                if part.function_call:
                    has_tool_call = True
                    # Append the model's function_call turn to history
                    contents.append(candidate.content)
                    # Execute the tool and append its result
                    tool_result = await dispatch_tool_call(part.function_call)
                    contents.append(tool_result)
                    break  # Restart the loop with updated history

                # ── Text part → stream it out ────────────────────────────────────
                if part.text:
                    final_response_text += part.text
                    # Format as Vercel AI SDK Data Stream protocol
                    yield f'0:{json.dumps(part.text)}\n'

            if not has_tool_call:
                # No tool was called; generation is complete.
                asyncio.create_task(intercept_and_log(last_user_question, final_response_text, bool(golden_chunks)))
                return # Exit successfully
                
        except Exception as exc:
            error_msg = str(exc)
            logger.error(f"Gemini API error: {error_msg}")

            # ── Fallback: if 429 was caused by google_search quota, retry without it
            if ("429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg) and {"google_search": {}} in tools_list:
                logger.info("Retrying WITHOUT Google Search (grounding quota likely exhausted)")
                tools_list = [TOOL_DECLARATIONS]
                gen_config = types.GenerateContentConfig(
                    system_instruction=effective_system_prompt,
                    temperature=0.4,
                    max_output_tokens=2048,
                    tools=tools_list,
                )
                continue # Restart the loop without Google search!
                
            # If it's another error, stop the stream and report it
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
            
            return # Ensure generator stops after yielding error

# ──────────────────────────────────────────────────────────────────────────────


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
            success = False
            if db:
                db.collection("pending_evaluations").document(doc_id).delete()
                success = True
            
            # Delete from Pinecone
            from .rag_stub import _get_index
            index = _get_index()
            if index:
                try:
                    index.delete(ids=[f"golden_{doc_id}"])
                    logger.info(f"[GoldenChunk] Deleted vector golden_{doc_id} from Pinecone.")
                except Exception as e:
                    logger.warning(f"[GoldenChunk] Failed to delete vector from Pinecone: {e}")
                    
            return success
            
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
    """Resolve a pending evaluation by coaching the AI or marking as spam."""
    if req.action not in ["inject_kb", "spam"]:
        raise HTTPException(status_code=400, detail="Invalid action. Use 'inject_kb' or 'spam'.")

    try:
        def update():
            if not db:
                return None
            doc_ref = db.collection("pending_evaluations").document(req.doc_id)
            doc = doc_ref.get()
            if not doc.exists:
                return False

            if req.action == "spam":
                doc_ref.update({"status": "rejected"})
                return {"action": "spam"}

            # ── inject_kb ──────────────────────────────────────────────────────
            data = doc.to_dict() or {}
            user_queries: list = data.get("queries", [])
            topic: str = data.get("topic", "Topik Khusus")

            golden_chunk = (
                f"PERTANYAAN: {' / '.join(user_queries)} | "
                f"INSTRUKSI ADMIN: {req.admin_note or ''} | "
                f"JAWABAN RESMI: {req.official_answer or ''}"
            )

            # Upload to Pinecone for Vector Search RAG
            from .rag_stub import _get_index, _get_genai_client, EMBEDDING_MODEL
            from google import genai
            
            client = _get_genai_client()
            index = _get_index()
            
            if client and index:
                try:
                    response = client.models.embed_content(
                        model=EMBEDDING_MODEL,
                        contents=golden_chunk,
                        config=genai.types.EmbedContentConfig(
                            task_type="RETRIEVAL_DOCUMENT",
                            output_dimensionality=768,
                        ),
                    )
                    vector = response.embeddings[0].values
                    
                    index.upsert(vectors=[
                        {
                            "id": f"golden_{req.doc_id}",
                            "values": vector,
                            "metadata": {
                                "type": "golden_chunk",
                                "topic": topic,
                                "text": golden_chunk,
                                "doc_id": req.doc_id
                            }
                        }
                    ])
                    logger.info(f"[GoldenChunk] Upserted vector golden_{req.doc_id} to Pinecone.")
                except Exception as e:
                    logger.error(f"[GoldenChunk] Failed to upload vector to Pinecone: {e}")

            doc_ref.update({
                "status": "approved",
                "admin_note": req.admin_note,
                "official_answer": req.official_answer,
                "golden_chunk": golden_chunk,
            })

            return {"action": "inject_kb", "golden_chunk": golden_chunk}

        result = await asyncio.to_thread(update)
        if result is False:
            raise HTTPException(status_code=404, detail="Document not found.")
        if result is None:
            raise HTTPException(status_code=503, detail="Database unavailable.")

        return {"status": "success", **result, "id": req.doc_id}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating evaluation: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/health", tags=["Health"])
def health_check():
    return {"status": "ok"}

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
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from google import genai
from google.genai import types

import sys
import os
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
        "=== ATURAN ATRIBUSI SUMBER & ISOLASI KONTEKS (WAJIB MUTLAK) ===\n\n"
        "Setiap respons yang Anda berikan HARUS dimulai dengan TEPAT SATU dari tiga frasa pembuka di bawah ini "
        "sebagai KALIMAT PERTAMA. Tidak ada pengecualian.\n"
        "WARNING: Falsely claiming general knowledge is from the RAG database is a critical failure. "
        "Anda DILARANG KERAS menggunakan pengetahuan internal Anda (seperti PP 16/2021) dan mengklaimnya sebagai data dari database internal.\n\n"

        "--- SCENARIO 1 (Database Match & Functional Similarity) ---\n"
        "FRASA PEMBUKA WAJIB: \"Berdasarkan informasi resmi dari database peraturan PBG kami, ...\"\n"
        "ATURAN EMAS ISOLASI KONTEKS (CONTEXT ISOLATION): Anda HANYA BOLEH menggunakan Scenario 1 jika detail spesifik, nama kategori, dan persyaratan "
        "yang akan Anda sebutkan BENAR-BENAR ADA TERTULIS SECARA FISIK di dalam teks konteks RAG yang disuntikkan di bawah. "
        "Jika konteks kosong, tidak relevan, atau tidak memuat detail tersebut, Anda DILARANG KERAS menggunakan Scenario 1.\n"
        "ATURAN FUNCTIONAL SIMILARITY: Jika pengguna bertanya tentang bangunan spesifik (misal \"Restaurant\" atau \"Sirkus\") dan tidak ada di konteks, "
        "Anda HANYA boleh memetakannya ke salah satu daftar di bagian === MASTER KATEGORI PBG === (di atas). "
        "Anda DILARANG mengarang/menciptakan nama kategori berdasarkan hukum atau pengetahuan umum Indonesia. "
        "Jika Anda tidak dapat menemukan kecocokan fisik di dalam teks konteks untuk Master Kategori yang Anda pilih, Anda WAJIB turun ke Scenario 3.\n"
        "FORMAT ASUMSI JIKA DITEMUKAN DI KONTEKS: \"Berdasarkan informasi resmi dari database peraturan PBG kami, detail spesifik untuk '[Kata Kunci User]' "
        "perlu klarifikasi lebih lanjut oleh dinas Pemkot setempat. Namun, berdasarkan fungsinya, pengajuan ini sangat mirip dan dapat mengacu pada kategori "
        "**[Nama Master Kategori PBG]**. Berikut persyaratannya: ...\"\n\n"

        "--- SCENARIO 2 (Web/External Grounding) ---\n"
        "FRASA PEMBUKA WAJIB: \"Berdasarkan informasi dari panduan resmi ([Masukkan Link Di Sini]), ...\"\n"
        "KAPAN DIGUNAKAN: Ketika informasi TIDAK ADA di teks konteks fisik, tetapi Anda dapat menemukan informasi faktual melalui "
        "Google Search atau website eksternal. Wajib menyertakan URL link yang valid.\n\n"

        "--- SCENARIO 3 (General AI Knowledge / Fallback) ---\n"
        "FRASA PEMBUKA WAJIB: \"Secara umum terkait pedoman perizinan bangunan, ...\"\n"
        "KAPAN DIGUNAKAN: Jika Anda HARUS mengandalkan memori internal pelatihan Anda (seperti menjelaskan PP 16/2021, pedoman umum, atau logika arsitektur) "
        "karena potongan database (RAG context) tidak memuat jawabannya secara fisik, Anda WAJIB menggunakan Scenario 3. "
        "Ingatkan pengguna bahwa ini adalah saran umum dan mereka harus memverifikasinya secara resmi.\n\n"

        "=== PENGINGAT KERAS ===\n"
        "Kegagalan untuk mematuhi batas ketat antara 'Konteks RAG yang Disuntikkan' dan 'Memori Internal LLM' adalah pelanggaran fatal.\n\n"
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
                    # Format as Vercel AI SDK Data Stream protocol
                    yield f'0:{json.dumps(part.text)}\n'

            if not has_tool_call:
                # No tool was called; generation is complete.
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
                        yield f'0:{json.dumps(part.text)}\n'
                
                if not has_text:
                    msg = "Mohon maaf, sistem sedang sibuk atau mengalami gangguan saat mencari data (fallback). Silakan coba lagi."
                    yield f'0:{json.dumps(msg)}\n'
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

    Request body:
        {
            "messages": [
                {"role": "user", "content": "Apa syarat pengajuan PBG?"}
            ]
        }

    Response:
        A plain-text streaming response. The Next.js frontend must use
        `streamProtocol: "text"` in the useChat hook to consume this correctly.
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


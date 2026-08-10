"""
main.py — FastAPI Application Entry Point for the PBG Chatbot Backend.

Start the server with:
    uvicorn main:app --reload --port 8000

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

from config import (
    GEMINI_API_KEY,
    GEMINI_MODEL,
    CORS_ORIGINS,
    SYSTEM_PROMPT,
)
from tools import TOOL_DECLARATIONS, TOOL_REGISTRY
from rag_stub import query_knowledge_base

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

    effective_system_prompt = SYSTEM_PROMPT
    if rag_context:
        effective_system_prompt = (
            f"{SYSTEM_PROMPT}\n\n"
            "── Konteks dari Knowledge Base ──\n"
            f"{rag_context}\n"
            "── Akhir Konteks ──"
        )

    # ── Build conversation history ───────────────────────────────────────────
    contents = build_gemini_history(messages)

    # ── Generation config ────────────────────────────────────────────────────
    gen_config = types.GenerateContentConfig(
        system_instruction=effective_system_prompt,
        tools=[TOOL_DECLARATIONS],
        temperature=0.4,
        max_output_tokens=2048,
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
        if "API_KEY_INVALID" in error_msg or "INVALID_ARGUMENT" in error_msg or "401" in error_msg:
            msg = (
                "❌ **Konfigurasi API Key tidak valid.**\n\n"
                "Pastikan `GEMINI_API_KEY` di file `.env` adalah kunci Gemini yang benar "
                "(dimulai dengan `AIza...`). Dapatkan kunci di: "
                "https://aistudio.google.com/app/apikey"
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


@app.post("/chat", tags=["Chat"])
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

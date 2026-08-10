/**
 * app/api/chat/route.ts
 *
 * Next.js Route Handler that proxies POST /api/chat → FastAPI backend.
 *
 * - Locally:  forwards to http://localhost:8000/chat
 * - Vercel:   forwards to the Python serverless function at /api/index.py
 *             (same-origin, so we use the BACKEND_URL env var or relative path)
 *
 * The frontend (page.tsx) sends requests to /api/chat.
 * This handler streams the FastAPI response back to the browser unchanged.
 */

export const runtime = "nodejs";

const BACKEND_URL =
  process.env.BACKEND_URL ?? "http://localhost:8000";

export async function POST(request: Request) {
  const body = await request.json();

  const response = await fetch(`${BACKEND_URL}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  // Stream the FastAPI response directly back to the browser
  return new Response(response.body, {
    status: response.status,
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
      "X-Accel-Buffering": "no",
      "Cache-Control": "no-cache",
    },
  });
}

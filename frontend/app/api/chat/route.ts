// =============================================================================
// The frontend is now pointing directly to the Python FastAPI server 
// (http://localhost:8000/chat) in app/page.tsx.
// This local mock route is no longer used, but kept as a simple text endpoint 
// to prevent Next.js build errors.
// =============================================================================

export async function POST() {
  return new Response("This is a mock endpoint. Use FastAPI instead.", {
    status: 200,
  });
}

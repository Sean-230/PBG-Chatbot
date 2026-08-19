import { adminDb } from "../../../../lib/firebase-admin";

export async function POST(request: Request) {
  try {
    const { doc_id, action, admin_note, official_answer } = await request.json();

    if (!doc_id || !["inject_kb", "spam"].includes(action)) {
      return Response.json({ error: "Invalid request. action must be 'inject_kb' or 'spam'." }, { status: 400 });
    }

    const ref = adminDb.collection("pending_evaluations").doc(doc_id);
    const snap = await ref.get();

    if (!snap.exists) {
      return Response.json({ error: "Document not found" }, { status: 404 });
    }

    if (action === "spam") {
      await ref.update({ status: "rejected" });
      return Response.json({ status: "success", action: "spam", id: doc_id });
    }

    // action === "inject_kb"
    const data = snap.data()!;
    const userQueries: string[] = data.queries ?? [];

    const golden_chunk =
      `PERTANYAAN: ${userQueries.join(" / ")} | ` +
      `INSTRUKSI ADMIN: ${admin_note ?? ""} | ` +
      `JAWABAN RESMI: ${official_answer ?? ""}`;

    // Log the golden chunk (ChromaDB injection will be wired in the next phase)
    console.log("\n[GOLDEN CHUNK READY FOR RAG INJECTION]");
    console.log(golden_chunk);
    console.log("");

    await ref.update({
      status: "approved",
      admin_note: admin_note ?? null,
      official_answer: official_answer ?? null,
      golden_chunk,
      resolved_at: new Date().toISOString(),
    });

    return Response.json({ status: "success", action: "inject_kb", id: doc_id, golden_chunk });
  } catch (err: any) {
    console.error("[admin/evaluate POST]", err);
    return Response.json({ error: err.message }, { status: 500 });
  }
}

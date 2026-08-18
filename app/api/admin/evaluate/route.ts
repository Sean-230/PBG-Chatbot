import { adminDb } from "../../../../lib/firebase-admin";

export async function POST(request: Request) {
  try {
    const { doc_id, action } = await request.json();

    if (!doc_id || !["approve", "reject"].includes(action)) {
      return Response.json({ error: "Invalid request" }, { status: 400 });
    }

    const newStatus = action === "approve" ? "approved" : "rejected";
    const ref = adminDb.collection("pending_evaluations").doc(doc_id);
    const snap = await ref.get();

    if (!snap.exists) {
      return Response.json({ error: "Document not found" }, { status: 404 });
    }

    await ref.update({ status: newStatus });

    return Response.json({ status: "success", action: `${action}d`, id: doc_id });
  } catch (err: any) {
    console.error("[admin/evaluate POST]", err);
    return Response.json({ error: err.message }, { status: 500 });
  }
}

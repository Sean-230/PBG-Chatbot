import { adminDb } from "../../../../../lib/firebase-admin";

export async function DELETE(
  _request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const { id } = await params;
    const ref = adminDb.collection("pending_evaluations").doc(id);
    const snap = await ref.get();

    if (!snap.exists) {
      return Response.json({ error: "Document not found" }, { status: 404 });
    }

    await ref.delete();

    return Response.json({ status: "success", id });
  } catch (err: any) {
    console.error("[admin/evaluate DELETE]", err);
    return Response.json({ error: err.message }, { status: 500 });
  }
}

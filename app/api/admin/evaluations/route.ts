import type { NextRequest } from "next/server";
import { adminDb } from "../../../../lib/firebase-admin";

export async function GET(request: NextRequest) {
  try {
    const status = request.nextUrl.searchParams.get("status") || "pending";

    let docs: FirebaseFirestore.QueryDocumentSnapshot[] = [];

    if (status === "answered") {
      const [snapApp, snapRej] = await Promise.all([
        adminDb
          .collection("pending_evaluations")
          .where("status", "==", "approved")
          .orderBy("count", "desc")
          .get(),
        adminDb
          .collection("pending_evaluations")
          .where("status", "==", "rejected")
          .orderBy("count", "desc")
          .get(),
      ]);
      docs = [...snapApp.docs, ...snapRej.docs];
    } else {
      const snap = await adminDb
        .collection("pending_evaluations")
        .where("status", "==", status)
        .orderBy("count", "desc")
        .get();
      docs = snap.docs;
    }

    const data = docs
      .map((d) => {
        const raw = d.data();
        return {
          ...raw,
          id: d.id,
          timestamp:
            raw.timestamp?.toDate?.()?.toISOString?.() ??
            String(raw.timestamp ?? ""),
        } as any;
      })
      .sort((a, b) => (b.count ?? 0) - (a.count ?? 0));

    return Response.json({ data });
  } catch (err: any) {
    console.error("[admin/evaluations GET]", err);
    return Response.json({ error: err.message }, { status: 500 });
  }
}

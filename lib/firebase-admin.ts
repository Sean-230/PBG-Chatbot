import { initializeApp, getApps, cert, App } from "firebase-admin/app";
import { getFirestore } from "firebase-admin/firestore";
import path from "path";

// Initialise only once (Next.js hot-reload safe)
let app: App;

if (!getApps().length) {
  const serviceAccountJson = process.env.FIREBASE_SERVICE_ACCOUNT_JSON || process.env.GOOGLE_CREDENTIALS_JSON;

  if (serviceAccountJson) {
    // Vercel / production: credentials stored as env var
    app = initializeApp({
      credential: cert(JSON.parse(serviceAccountJson)),
    });
  } else {
    // Local dev: read from the same credentials.json the Python backend uses
    const credPath = path.join(process.cwd(), "api", "credentials.json");
    app = initializeApp({
      credential: cert(credPath),
    });
  }
} else {
  app = getApps()[0];
}

export const adminDb = getFirestore(app);

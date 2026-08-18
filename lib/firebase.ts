import { initializeApp, getApps, getApp } from "firebase/app";
import { getAuth } from "firebase/auth";
import { getFirestore } from "firebase/firestore";

const firebaseConfig = {
  projectId: "pbg-chatbot",
  appId: "1:573452542291:web:fc73ad2da7969f24b3c1a6",
  storageBucket: "pbg-chatbot.firebasestorage.app",
  apiKey: "AIzaSyCTTqoDL-W6auMVGcICz9NHT9wcrkWJmt8",
  authDomain: "pbg-chatbot.firebaseapp.com",
  messagingSenderId: "573452542291",
  measurementId: "G-DJ8H8PGYT0",
};

const app = !getApps().length ? initializeApp(firebaseConfig) : getApp();
const auth = getAuth(app);
const db = getFirestore(app);

export { app, auth, db };

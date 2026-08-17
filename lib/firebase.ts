// Import the functions you need from the SDKs you need
import { initializeApp } from "firebase/app";
import { getAnalytics } from "firebase/analytics";

// Your web app's Firebase configuration
const firebaseConfig = {
  apiKey: "AIzaSyCTTqoDL-W6auMVGcICz9NHT9wcrkWJmt8",
  authDomain: "pbg-chatbot.firebaseapp.com",
  projectId: "pbg-chatbot",
  storageBucket: "pbg-chatbot.firebasestorage.app",
  messagingSenderId: "573452542291",
  appId: "1:573452542291:web:fc73ad2da7969f24b3c1a6",
  measurementId: "G-DJ8H8PGYT0"
};

// Initialize Firebase
const app = initializeApp(firebaseConfig);

// Initialize analytics only on the client side
let analytics;
if (typeof window !== "undefined") {
  analytics = getAnalytics(app);
}

export { app, analytics };

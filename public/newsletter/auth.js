// auth.js — shared Firebase Auth helpers loaded as an ES module on every page.
//
// SETUP: Replace the placeholder values in firebaseConfig with the real values
// from Firebase Console → Project Settings → General → Your apps → SDK snippet.

import { initializeApp } from 'https://www.gstatic.com/firebasejs/10.14.1/firebase-app.js';
import { getAuth, onAuthStateChanged, signOut } from 'https://www.gstatic.com/firebasejs/10.14.1/firebase-auth.js';

export const firebaseConfig = {
  apiKey:            "AIzaSyD-Nx1nExmGPY7QFegBlZVAXOd56t2V0Wg",
  authDomain:        "ai-news-letter-497720.firebaseapp.com",
  projectId:         "ai-news-letter-497720",
  storageBucket:     "ai-news-letter-497720.firebasestorage.app",
  messagingSenderId: "26202086206",
  appId:             "1:26202086206:web:19cc12c87c252091dfaa6f",
  measurementId:     "G-YEJM5TGS8L",
};

const app = initializeApp(firebaseConfig);
export const auth = getAuth(app);
export { onAuthStateChanged, signOut };

export async function getIdToken() {
  const user = auth.currentUser;
  if (!user) return null;
  return user.getIdToken();
}

// Wraps fetch with an Authorization: Bearer header using the current user's ID token.
export async function authFetch(url, options = {}) {
  const token = await getIdToken();
  if (!token) throw new Error('not_authenticated');
  const { headers: extraHeaders, ...rest } = options;
  return fetch(url, {
    ...rest,
    headers: {
      'Content-Type': 'application/json',
      ...extraHeaders,
      'Authorization': `Bearer ${token}`,
    },
  });
}

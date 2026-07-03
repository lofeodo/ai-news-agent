// auth.js — shared Firebase Auth helpers loaded as an ES module on every page.
//
// Firebase Hosting automatically serves the project config at /__/firebase/init.json
// so no API key is needed in source. Use `firebase serve` for local development
// (plain HTTP servers don't serve that endpoint).

import { initializeApp } from 'https://www.gstatic.com/firebasejs/10.14.1/firebase-app.js';
import {
  getAuth,
  onAuthStateChanged,
  signOut,
  GoogleAuthProvider,
  signInWithPopup,
  signInWithRedirect,
  getRedirectResult,
} from 'https://www.gstatic.com/firebasejs/10.14.1/firebase-auth.js';

const _r = await fetch('/__/firebase/init.json');
if (!_r.ok) throw new Error('[auth.js] Firebase config not available. Run `firebase serve` locally.');
const _cfg = await _r.json();
const app = initializeApp(_cfg);

export const auth = getAuth(app);
export { onAuthStateChanged, signOut, getRedirectResult };

const _googleProvider = new GoogleAuthProvider();

// Popup-first sign-in: works on all platforms including iOS/iPadOS Safari.
// Firebase SDK v10 opens the popup synchronously within the click-event user-gesture
// window, so Safari allows it (renders as a new tab on iOS). The credential comes
// back via postMessage — no cross-origin storage, so Safari ITP is not a problem.
// Falls back to redirect only if the browser explicitly blocks the popup.
export async function signInWithGoogle() {
  try {
    return await signInWithPopup(auth, _googleProvider);
  } catch (e) {
    if (e.code === 'auth/popup-blocked') {
      await signInWithRedirect(auth, _googleProvider);
      return; // page navigates away
    }
    throw e; // caller handles auth/popup-closed-by-user and real errors
  }
}

export async function getIdToken() {
  const user = auth.currentUser;
  if (!user) return null;
  return user.getIdToken();
}

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

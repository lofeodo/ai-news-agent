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
// init.json always returns the Firebase-assigned authDomain. Override it with
// the actual hostname on production so Google's OAuth popup shows the custom
// domain instead of "ai-news-letter-497720.firebaseapp.com".
const _host = window.location.hostname;
if (_host !== 'localhost' && _host !== '127.0.0.1') _cfg.authDomain = _host;
const app = initializeApp(_cfg);

export const auth = getAuth(app);
export { onAuthStateChanged, signOut, getRedirectResult };

const _googleProvider = new GoogleAuthProvider();

// authDomain is set to the serving hostname above, so the popup/redirect handler
// is same-origin with the app. This is required for Safari: when the handler is
// cross-origin (e.g. latentspacemail.firebaseapp.com), Safari's third-party storage
// restrictions silently drop the credential on return.
export async function signInWithGoogle() {
  try {
    return await signInWithPopup(auth, _googleProvider);
  } catch (e) {
    if (e.code === 'auth/popup-blocked') {
      await signInWithRedirect(auth, _googleProvider);
      return; // page navigates away
    }
    throw e;
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

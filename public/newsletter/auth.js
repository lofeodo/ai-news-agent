// auth.js — shared Firebase Auth helpers loaded as an ES module on every page.
//
// Firebase Hosting automatically serves the project config at /__/firebase/init.json
// so no API key is needed in source. Use `firebase serve` for local development
// (plain HTTP servers don't serve that endpoint).

import { initializeApp } from 'https://www.gstatic.com/firebasejs/10.14.1/firebase-app.js';
import {
  initializeAuth,
  browserLocalPersistence,
  browserPopupRedirectResolver,
  onAuthStateChanged,
  signOut,
  GoogleAuthProvider,
  signInWithRedirect,
  getRedirectResult,
} from 'https://www.gstatic.com/firebasejs/10.14.1/firebase-auth.js';

const _r = await fetch('/__/firebase/init.json');
if (!_r.ok) throw new Error('[auth.js] Firebase config not available. Run `firebase serve` locally.');
const _cfg = await _r.json();
// Override authDomain with the serving hostname so the OAuth handler is same-origin.
// Required for Safari ITP: cross-origin authDomain silently drops credentials on return.
const _host = window.location.hostname;
if (_host !== 'localhost' && _host !== '127.0.0.1') _cfg.authDomain = _host;
const app = initializeApp(_cfg);

// initializeAuth with explicit localStorage persistence and redirect resolver.
// - browserLocalPersistence: tells Firebase to use localStorage from the start,
//   eliminating the getAuth() → IndexedDB → setPersistence() migration race that
//   caused a spurious null on mobile Safari before the real auth state was read.
// - browserPopupRedirectResolver: required for signInWithRedirect and getRedirectResult;
//   initializeAuth does NOT bundle it automatically (unlike getAuth).
export const auth = initializeAuth(app, {
  persistence: browserLocalPersistence,
  popupRedirectResolver: browserPopupRedirectResolver,
});

export { onAuthStateChanged, signOut, getRedirectResult };

const _googleProvider = new GoogleAuthProvider();

// authDomain is set to the serving hostname above, so the redirect handler is
// same-origin with the app. This is required for Safari: when the handler is
// cross-origin (e.g. latentspacemail.firebaseapp.com), Safari's ITP silently
// drops the credential on return.
//
// signInWithPopup is NOT used: on iOS Safari, window.open() opens a new full
// tab and window.opener is null, so the postMessage back to the app is lost and
// the popup promise resolves with nothing — silent failure. signInWithRedirect
// avoids the cross-tab postMessage entirely.
export async function signInWithGoogle() {
  await signInWithRedirect(auth, _googleProvider);
  // Page navigates away. getRedirectResult() on the return trip handles the result.
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

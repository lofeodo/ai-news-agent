// auth.js — shared Firebase Auth helpers loaded as an ES module on every page.
//
// Firebase Hosting automatically serves the project config at /__/firebase/init.json
// so no API key is needed in source. Use `firebase serve` for local development
// (plain HTTP servers don't serve that endpoint).

import { initializeApp } from 'https://www.gstatic.com/firebasejs/10.14.1/firebase-app.js';
import {
  initializeAuth,
  indexedDBLocalPersistence,
  browserLocalPersistence,
  inMemoryPersistence,
  onAuthStateChanged,
  signOut,
  signInWithCustomToken,
} from 'https://www.gstatic.com/firebasejs/10.14.1/firebase-auth.js';

const _r = await fetch('/__/firebase/init.json');
if (!_r.ok) throw new Error('[auth.js] Firebase config not available. Run `firebase serve` locally.');
const _cfg = await _r.json();
// Override authDomain with the serving hostname so Firebase's hosted auth
// action pages (password reset / email verification links) are same-origin.
// Required for Safari ITP: cross-origin authDomain silently drops credentials
// on return from those flows.
const _host = window.location.hostname;
if (_host !== 'localhost' && _host !== '127.0.0.1') _cfg.authDomain = _host;
const app = initializeApp(_cfg);

// Explicit persistence set from the start avoids the getAuth() → IndexedDB →
// setPersistence() migration race that caused a spurious null on mobile
// Safari before the real auth state was read.
//
// persistence is an ordered fallback list, not a single value: Safari Private
// Browsing can throw when Firebase's persistence layer tries to write during
// initializeAuth() -- and since this is a top-level module statement, an
// uncaught throw here kills auth.js entirely, which kills every page that
// imports it (no click handlers wired anywhere -- indistinguishable from
// "the button does nothing"). inMemoryPersistence never touches storage, so
// it can't fail the same way; it's the last resort so a private-browsing
// session still gets a working (if not cross-reload-persistent) sign-in
// rather than a dead page.
//
// No popupRedirectResolver here: Google Sign-In no longer uses
// signInWithPopup/signInWithRedirect/getRedirectResult at all (see
// auth-callback.html) — it's a server-side OAuth Authorization Code flow, so
// there's no popup/redirect operation left that would need a resolver.
export const auth = initializeAuth(app, {
  persistence: [indexedDBLocalPersistence, browserLocalPersistence, inMemoryPersistence],
});

export { onAuthStateChanged, signOut, signInWithCustomToken };

// return_to/returnUrl round-trips through the browser and Google's redirect,
// so it's attacker-visible/craftable. Allowlist known pages instead of trying
// to validate arbitrary relative-URL syntax (an easy place to get an open
// redirect wrong) — shared by login.html and auth-callback.html.
const ALLOWED_RETURN_PAGES = ['index.html', 'preferences.html', 'sections.html'];
export function sanitizeReturnPage(value) {
  return ALLOWED_RETURN_PAGES.includes(value) ? value : 'preferences.html';
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

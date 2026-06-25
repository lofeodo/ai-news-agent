// auth.js — shared Firebase Auth helpers loaded as an ES module on every page.
//
// Firebase Hosting automatically serves the project config at /__/firebase/init.json
// so no API key is needed in source. Use `firebase serve` for local development
// (plain HTTP servers don't serve that endpoint).

import { initializeApp } from 'https://www.gstatic.com/firebasejs/10.14.1/firebase-app.js';
import { getAuth, onAuthStateChanged, signOut } from 'https://www.gstatic.com/firebasejs/10.14.1/firebase-auth.js';

const _r = await fetch('/__/firebase/init.json');
if (!_r.ok) throw new Error('[auth.js] Firebase config not available. Run `firebase serve` locally.');
const _cfg = await _r.json();
// Firebase Hosting's init.json always returns the Firebase-assigned authDomain
// (e.g. ai-news-letter-497720.firebaseapp.com). When running on the custom domain,
// override it so Google's OAuth popup shows "newsletter.lofeodo.com" instead.
const _host = window.location.hostname;
if (_host !== 'localhost' && _host !== '127.0.0.1') _cfg.authDomain = _host;
const app = initializeApp(_cfg);

export const auth = getAuth(app);
export { onAuthStateChanged, signOut };

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

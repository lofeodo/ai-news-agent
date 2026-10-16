// Shared by newsletters.html (the hub) and newsletter.html (one newsletter's page): the catalog from
// GET /newsletters, who is signed in, and the subscribe calls. Hidden (not yet launched) newsletters only come
// back for admins, so the catalog is fetched with the sign-in token when there is one.
import { auth, onAuthStateChanged, authFetch } from './auth.js';

export const API = (() => {
  const h = location.hostname;
  if (h === 'localhost' || h === '127.0.0.1') return 'http://localhost:8000';
  return 'https://agent-subscriptions-zozrn33sna-nn.a.run.app';
})();

export const esc = (t) => String(t ?? '').replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

// Same content policy the public issue response sends, for an inline (srcdoc) copy.
export const ISSUE_POLICY = `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src https: data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">`;

// Resolves once Firebase has settled: the verified user, or null.
export function signedInUser() {
  return new Promise((resolve) => {
    let done = false;
    const finish = (u) => { if (!done) { done = true; resolve(u && u.emailVerified ? u : null); } };
    onAuthStateChanged(auth, finish);
    auth.authStateReady().then(() => finish(auth.currentUser));
    setTimeout(() => finish(null), 8000);
  });
}

export async function loadCatalog(user) {
  const res = user ? await authFetch(`${API}/newsletters`) : await fetch(`${API}/newsletters`);
  if (!res.ok) throw new Error(`newsletters ${res.status}`);
  return res.json();   // {newsletters, consent_text, consent_text_version}
}

// {newsletters: [...ids], is_admin} for a signed-in reader.
export async function loadMe() {
  const res = await authFetch(`${API}/auth/me`);
  if (!res.ok) throw new Error(`me ${res.status}`);
  return res.json();
}

export async function setSubscribed(id, on, consentVersion, sendLatest = false) {
  const res = on
    ? await authFetch(`${API}/auth/subscribe`, { method: 'POST', body: JSON.stringify({
        newsletter: id, consent_text_version: consentVersion, send_latest: sendLatest }) })
    : await authFetch(`${API}/auth/unsubscribe`, { method: 'POST', body: JSON.stringify({ newsletter: id }) });
  if (!res.ok) throw Object.assign(new Error(`subscribe ${res.status}`), { status: res.status });
}

// The double opt-in form: POST /subscribe always answers the same for any address (the confirmation goes by mail).
export async function subscribeByEmail(email, ids, consentVersion, sendLatest) {
  return fetch(`${API}/subscribe`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, newsletters: ids, consent_text_version: consentVersion, send_latest: sendLatest }),
  });
}

export function emailFormMessage(status) {
  if (status === 200) return ["Check your inbox: we've sent a link to confirm. Nothing is sent until you press it.", 'success'];
  if (status === 422) return ["That doesn't look like a valid email address.", 'error'];
  if (status === 404) return ['One of those newsletters is not available. Reload the page and try again.', 'error'];
  if (status === 503) return ['Subscriber limit reached. No spots are open right now.', 'error'];
  if (status === 429) return ['Too many requests. Wait a minute and try again.', 'error'];
  return ['Something went wrong. Try again.', 'error'];
}

export function sectionsHtml(n) {
  const items = n.sections.map((s) => `<li><span aria-hidden="true">${esc(s.icon)}</span> ${esc(s.name)}</li>`);
  if (n.spotlight === 'papers') items.unshift('<li><span aria-hidden="true">📄</span> Paper of the week</li>');
  return `<ul class="nl-sections">${items.join('')}</ul>`;
}

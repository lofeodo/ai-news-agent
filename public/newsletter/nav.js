import { auth, onAuthStateChanged, signOut, authFetch } from './auth.js';

const API = (() => {
  const h = location.hostname;
  if (h === 'localhost' || h === '127.0.0.1') return 'http://localhost:8000';
  return 'https://agent-subscriptions-zozrn33sna-nn.a.run.app';
})();
const CACHE_KEY = 'lsm_nav_auth';

// Mark the active nav link based on current path
const path = location.pathname;

// unsubscribe.html's whole purpose is to let a signed-in user leave -- the
// auto-subscribe call below must never fire there, or it would silently
// re-subscribe someone mid-unsubscribe.
const onUnsubscribePage = /unsubscribe(\.html)?$/.test(path);
document.querySelectorAll('.topbar__nav-link[data-href]').forEach(a => {
  const href = a.dataset.href;
  const target = new URL(href, location.origin).pathname;
  if (path === target || path === target.replace(/\.html$/, '')) {
    a.classList.add('active');
  }
});

const authEl     = document.getElementById('topbar-auth');
const signinEl   = document.getElementById('topbar-signin');
const emailEl    = document.getElementById('topbar-email');
const tierEl     = document.getElementById('topbar-tier');
const signoutBtn = document.getElementById('topbar-signout');

function showSignedIn(email, tier) {
  if (signinEl) signinEl.style.display = 'none';
  if (authEl)   authEl.style.display   = 'flex';
  if (emailEl)  emailEl.textContent    = email;
  if (tierEl)   tierEl.style.display   = tier === 'premium' ? 'inline-block' : 'none';
}

function showSignedOut() {
  if (authEl)   authEl.style.display   = 'none';
  if (signinEl) signinEl.style.display = 'flex';
}

// Restore last-known auth state instantly from cache (no Firebase wait)
let cached = null;
try { cached = JSON.parse(localStorage.getItem(CACHE_KEY)); } catch {}
if (cached?.email) showSignedIn(cached.email, cached.tier || 'free');

// Guards against calling /auth/subscribe more than once per page load (e.g.
// if onAuthStateChanged fires again later in the same tab, such as a token
// refresh). /auth/subscribe is idempotent server-side regardless
// ({"status": "already_subscribed"}) -- this just avoids redundant calls
// within one tab. It does NOT protect multiple simultaneously-open tabs;
// that's an accepted, low-severity edge case.
let autoSubscribeAttempted = false;

// Then confirm/update with Firebase (source of truth)
onAuthStateChanged(auth, async (user) => {
  if (!user || !user.emailVerified) {
    localStorage.removeItem(CACHE_KEY);
    showSignedOut();
    return;
  }

  // Show immediately with known email (tier may update below)
  showSignedIn(user.email, cached?.tier || 'free');

  try {
    const res = await authFetch(`${API}/auth/me`);
    if (!res.ok) return;
    const me   = await res.json();
    const tier = me.tier || 'free';
    localStorage.setItem(CACHE_KEY, JSON.stringify({ email: user.email, tier }));
    cached = { email: user.email, tier };
    showSignedIn(user.email, tier);

    // Auto-subscribe verified users who signed in but never subscribed --
    // sign-in and subscribing used to be two fully decoupled steps, which
    // left users with a working account and no newsletter. /auth/subscribe
    // is idempotent, so it's safe to attempt on every page load until it
    // succeeds; this self-heals across page views if one attempt fails
    // (rate limit, transient network/5xx). send_latest is always false here
    // (background action, no explicit user intent) -- the manual Subscribe
    // button/checkbox on preferences.html stays as the deliberate-intent
    // path and the visible fallback if this fails.
    if (!me.subscribed && !autoSubscribeAttempted && !onUnsubscribePage) {
      autoSubscribeAttempted = true;
      try {
        const subRes = await authFetch(`${API}/auth/subscribe`, {
          method: 'POST',
          body: JSON.stringify({ send_latest: false }),
        });
        if (!subRes.ok) console.error('[nav] auto-subscribe failed:', subRes.status);
      } catch (e) {
        console.error('[nav] auto-subscribe failed:', e);
      }
    }
  } catch (e) {
    console.error('[nav] /auth/me failed:', e);
  }
});

if (signoutBtn) {
  signoutBtn.addEventListener('click', async () => {
    localStorage.removeItem(CACHE_KEY);
    await signOut(auth);
    location.href = '/';
  });
}

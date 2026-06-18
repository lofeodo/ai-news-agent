import { auth, onAuthStateChanged, signOut, authFetch } from './auth.js';

const API = 'https://agent-subscriptions-zozrn33sna-nn.a.run.app';
const CACHE_KEY = 'lsm_nav_auth';

// Mark the active nav link based on current path
const path = location.pathname;
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
    if (res.ok) {
      const tier = (await res.json()).tier || 'free';
      localStorage.setItem(CACHE_KEY, JSON.stringify({ email: user.email, tier }));
      cached = { email: user.email, tier };
      showSignedIn(user.email, tier);
    }
  } catch {}
});

if (signoutBtn) {
  signoutBtn.addEventListener('click', async () => {
    localStorage.removeItem(CACHE_KEY);
    await signOut(auth);
    location.href = '/';
  });
}

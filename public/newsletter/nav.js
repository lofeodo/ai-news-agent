import { auth, onAuthStateChanged, signOut, authFetch, currentReturnPage } from './auth.js';

const API = (() => {
  const h = location.hostname;
  if (h === 'localhost' || h === '127.0.0.1') return 'http://localhost:8000';
  return 'https://agent-subscriptions-zozrn33sna-nn.a.run.app';
})();
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

const returnPage = currentReturnPage();
if (signinEl && returnPage) signinEl.href = `/login.html?returnUrl=${returnPage}`;

function showSignedIn(email, tier) {
  if (signinEl) signinEl.style.display = 'none';
  if (authEl)   authEl.style.display   = 'flex';
  if (emailEl)  emailEl.textContent    = email;
  if (tierEl)   tierEl.style.display   = tier === 'premium' ? 'inline-block' : 'none';
}

// Admins get an extra link to the preview page in debug mode (/preview.html?debug=1).
function showAdminLink(isAdmin) {
  const nav = document.querySelector('.topbar__nav');
  if (!nav) return;
  let link = document.getElementById('topbar-debug');
  if (!isAdmin) { link?.remove(); return; }
  if (link || document.getElementById('debug-toggle')) return;  // preview.html has its own toggle
  link = document.createElement('a');
  link.id = 'topbar-debug';
  link.className = 'topbar__nav-link';
  link.href = '/preview.html?debug=1';
  link.textContent = 'Debug run';
  nav.appendChild(link);
}

function showSignedOut() {
  if (authEl)   authEl.style.display   = 'none';
  if (signinEl) signinEl.style.display = 'flex';
}

// Restore last-known auth state instantly from cache (no Firebase wait)
let cached = null;
try { cached = JSON.parse(localStorage.getItem(CACHE_KEY)); } catch {}
if (cached?.email) { showSignedIn(cached.email, cached.tier || 'free'); showAdminLink(!!cached.isAdmin); }

// Then confirm/update with Firebase (source of truth)
onAuthStateChanged(auth, async (user) => {
  if (!user || !user.emailVerified) {
    localStorage.removeItem(CACHE_KEY);
    showSignedOut();
    showAdminLink(false);
    return;
  }

  // Show immediately with known email (tier may update below)
  showSignedIn(user.email, cached?.tier || 'free');

  try {
    const res = await authFetch(`${API}/auth/me`);
    if (!res.ok) return;
    const me   = await res.json();
    const tier = me.tier || 'free';
    const isAdmin = !!me.is_admin;
    localStorage.setItem(CACHE_KEY, JSON.stringify({ email: user.email, tier, isAdmin }));
    cached = { email: user.email, tier, isAdmin };
    showSignedIn(user.email, tier);
    showAdminLink(isAdmin);
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

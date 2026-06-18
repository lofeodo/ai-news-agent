import { auth, onAuthStateChanged, signOut, authFetch } from './auth.js';

const API = 'https://agent-subscriptions-zozrn33sna-nn.a.run.app';

// Mark the active nav link based on current path
const path = location.pathname;
document.querySelectorAll('.topbar__nav-link[data-href]').forEach(a => {
  const href = a.dataset.href;
  const target = new URL(href, location.origin).pathname;
  if (path === target || path === target.replace(/\.html$/, '')) {
    a.classList.add('active');
  }
});

// Auth state
const authEl      = document.getElementById('topbar-auth');
const signinEl    = document.getElementById('topbar-signin');
const emailEl     = document.getElementById('topbar-email');
const tierEl      = document.getElementById('topbar-tier');
const sectionsEl  = document.getElementById('topbar-sections');
const signoutBtn  = document.getElementById('topbar-signout');

let resolved = false;
const timeout = setTimeout(() => {
  if (!resolved) { resolved = true; showSignedOut(); }
}, 2500);

onAuthStateChanged(auth, async (user) => {
  if (resolved) return;
  resolved = true;
  clearTimeout(timeout);

  if (!user || !user.emailVerified) { showSignedOut(); return; }

  let tier = 'free';
  try {
    const res = await authFetch(`${API}/auth/me`);
    if (res.ok) tier = (await res.json()).tier || 'free';
  } catch {}

  showSignedIn(user, tier);
});

function showSignedOut() {
  if (authEl)   authEl.style.display   = 'none';
  if (signinEl) signinEl.style.display = 'flex';
}

function showSignedIn(user, tier) {
  if (signinEl) signinEl.style.display  = 'none';
  if (authEl)   authEl.style.display    = 'flex';
  if (emailEl)  emailEl.textContent     = user.email;
  if (tierEl && tier === 'premium') tierEl.style.display = 'inline-block';
  if (sectionsEl && tier === 'premium') sectionsEl.style.display = 'block';
}

if (signoutBtn) {
  signoutBtn.addEventListener('click', async () => {
    await signOut(auth);
    location.href = '/';
  });
}

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
const authEl     = document.getElementById('topbar-auth');
const signinEl   = document.getElementById('topbar-signin');
const emailEl    = document.getElementById('topbar-email');
const tierEl     = document.getElementById('topbar-tier');
const signoutBtn = document.getElementById('topbar-signout');

onAuthStateChanged(auth, async (user) => {
  if (!user || !user.emailVerified) return; // sign-in button already visible by default

  // Phase 1: show email + sign-out immediately, without waiting for tier fetch
  if (signinEl) signinEl.style.display = 'none';
  if (authEl)   authEl.style.display   = 'flex';
  if (emailEl)  emailEl.textContent    = user.email;

  // Phase 2: fetch tier and show premium badge if applicable
  try {
    const res = await authFetch(`${API}/auth/me`);
    if (res.ok) {
      const tier = (await res.json()).tier || 'free';
      if (tier === 'premium' && tierEl) tierEl.style.display = 'inline-block';
    }
  } catch {}
});

if (signoutBtn) {
  signoutBtn.addEventListener('click', async () => {
    await signOut(auth);
    location.href = '/';
  });
}

import { auth, onAuthStateChanged, signOut, authFetch } from './auth.js';

const API = 'https://agent-subscriptions-zozrn33sna-nn.a.run.app';
const path = location.pathname;

function isActive(href) {
  const p = new URL(href, location.origin).pathname;
  return path === p || path === p.replace(/\.html$/, '');
}

const GITHUB_SVG = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12"/></svg>`;

const LOGO_SVG = `<img src="/images/logo-mark.svg" alt="Latent SpaceMail" height="26" width="26">`;

const NAV_LINKS = [
  { href: '/preview.html', label: 'Latest Issue' },
  { href: '/preferences.html', label: 'Preferences' },
];

function buildNav(user, tier) {
  const links = [...NAV_LINKS];
  if (user && tier === 'premium') {
    links.push({ href: '/sections.html', label: 'Sections' });
  }

  const navHtml = links.map(({ href, label }) =>
    `<a href="${href}" class="topbar__nav-link${isActive(href) ? ' active' : ''}">${label}</a>`
  ).join('');

  let authHtml;
  if (user) {
    authHtml = `
      <div class="topbar__auth" id="topbar-auth">
        <span class="topbar__auth-dot"></span>
        <span class="topbar__auth-email">${user.email}</span>
        ${tier === 'premium' ? '<span class="topbar__auth-tier">◆ Premium</span>' : ''}
        <button type="button" class="topbar__signout" id="topbar-signout">Sign out</button>
      </div>`;
  } else {
    authHtml = `<a href="/login.html?returnUrl=${encodeURIComponent(path)}" class="topbar__signin">Sign in →</a>`;
  }

  const header = document.createElement('header');
  header.className = 'topbar';
  header.innerHTML = `
    <a href="/" class="topbar__logo" aria-label="Latent SpaceMail home">${LOGO_SVG}</a>
    <nav class="topbar__nav" aria-label="Main navigation">${navHtml}</nav>
    <div class="topbar__right">
      ${authHtml}
      <a href="https://github.com/lofeodo/ai-news-agent" target="_blank" rel="noopener" class="topbar__github" aria-label="View source on GitHub">${GITHUB_SVG}</a>
    </div>`;

  document.body.prepend(header);

  const signoutBtn = document.getElementById('topbar-signout');
  if (signoutBtn) {
    signoutBtn.addEventListener('click', async () => {
      await signOut(auth);
      location.href = '/';
    });
  }
}

let resolved = false;
const timeout = setTimeout(() => {
  if (!resolved) { resolved = true; buildNav(null, 'free'); }
}, 2500);

onAuthStateChanged(auth, async (user) => {
  if (resolved) return;
  resolved = true;
  clearTimeout(timeout);

  if (!user || !user.emailVerified) {
    buildNav(null, 'free');
    return;
  }

  let tier = 'free';
  try {
    const res = await authFetch(`${API}/auth/me`);
    if (res.ok) {
      const data = await res.json();
      tier = data.tier || 'free';
    }
  } catch {}

  buildNav(user, tier);
});

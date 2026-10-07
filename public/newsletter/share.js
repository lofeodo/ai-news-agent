// share.js — shared by index.html (topbar button) and share.html (email landing page).
// Native share sheet where the browser has one (phones); otherwise a small menu.
(function () {
  const URL_TO_SHARE = 'https://newsletter.lofeodo.com/?ref=share';
  const TITLE = 'Latent SpaceMail';
  const TEXT = 'Latent SpaceMail: a weekly briefing on AI research and news.';
  const enc = encodeURIComponent;

  const OPTIONS = [
    { id: 'whatsapp', label: 'WhatsApp', href: `https://wa.me/?text=${enc(TEXT + ' ' + URL_TO_SHARE)}` },
    { id: 'x',        label: 'X',        href: `https://twitter.com/intent/tweet?text=${enc(TEXT)}&url=${enc(URL_TO_SHARE)}` },
    { id: 'linkedin', label: 'LinkedIn', href: `https://www.linkedin.com/sharing/share-offsite/?url=${enc(URL_TO_SHARE)}` },
    { id: 'email',    label: 'Email',    href: `mailto:?subject=${enc(TITLE)}&body=${enc(TEXT + '\n\n' + URL_TO_SHARE)}` },
  ];

  async function copyLink(btn) {
    const done = () => {
      const old = btn.textContent;
      btn.textContent = 'Copied ✓';
      setTimeout(() => { btn.textContent = old; }, 1800);
    };
    try {
      await navigator.clipboard.writeText(URL_TO_SHARE);
      done();
    } catch {
      const ta = document.createElement('textarea');
      ta.value = URL_TO_SHARE;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand('copy'); done(); } catch {}
      ta.remove();
    }
  }

  // Fills `container` with Copy link + the social options.
  function renderOptions(container) {
    container.textContent = '';
    const copy = document.createElement('button');
    copy.type = 'button';
    copy.className = 'share-opt';
    copy.textContent = 'Copy link';
    copy.addEventListener('click', () => copyLink(copy));
    container.appendChild(copy);
    OPTIONS.forEach((o) => {
      const a = document.createElement('a');
      a.className = 'share-opt';
      a.href = o.href;
      a.textContent = o.label;
      if (!o.href.startsWith('mailto:')) { a.target = '_blank'; a.rel = 'noopener'; }
      container.appendChild(a);
    });
  }

  async function nativeShare() {
    if (!navigator.share) return false;
    try {
      await navigator.share({ title: TITLE, text: TEXT, url: URL_TO_SHARE });
    } catch (e) {
      if (e && e.name !== 'AbortError') return false;
    }
    return true;
  }

  // Topbar button: share sheet if available, else toggle a popover under the button.
  function attachButton(btn) {
    const pop = document.createElement('div');
    pop.className = 'share-pop';
    pop.hidden = true;
    renderOptions(pop);
    btn.parentNode.insertBefore(pop, btn.nextSibling);

    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      if (await nativeShare()) return;
      pop.hidden = !pop.hidden;
    });
    document.addEventListener('click', (e) => {
      if (!pop.hidden && !pop.contains(e.target)) pop.hidden = true;
    });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') pop.hidden = true; });
  }

  window.LSMShare = { attachButton, renderOptions, nativeShare, url: URL_TO_SHARE };
})();

// studio/app.js — the newsletter studio page (studio.html): who may use it, which API it talks to, the shared
// state, and the layout (three columns on desktop, two on tablets, Build/Preview tabs on phones).
//
// Modes:
//   premium    full studio
//   free       the upgrade gate over a read-only sample newsletter (mock content)
//   signedout  a short pitch and "Sign in" over the same sample
// The page never decides the tier: it reads `tier` from /auth/me; the server enforces it on every route.
// With ?mock=1 the page talks to mock-api.js and the mode comes from ?as=premium|free|signedout (default
// premium), so every state can be opened and checked without a backend.

import { auth, onAuthStateChanged, authFetch } from '../auth.js';
import { API_BASE, ApiError, connect } from './api.js';
import * as M from './model.js';
import { h, mount, debounce } from './dom.js';
import { initLibrary, renderLibrary } from './library.js';
import { initCanvas, renderCanvas, focusBlock } from './canvas.js';
import { initPreview, requestPreview, layoutPreview } from './preview.js';

const params = new URLSearchParams(location.search);
const FORCE_MOCK = params.get('mock') === '1';
const MOCK_AS = ['premium', 'free', 'signedout'].includes(params.get('as')) ? params.get('as') : 'premium';
const DEMO_URL = new URL('./fixtures/definitions/monday-brief.json', import.meta.url);

const $ = id => document.getElementById(id);
const studio = $('studio');

/** Everything the library, canvas and preview modules share. */
export const ctx = {
  api: null,
  catalog: { topics: [], widgets: [] },
  mock: false,
  mode: 'loading',
  readOnly: true,
  email: '',
  state: M.emptyState(),
  saved: null,            // {id, state} of the newsletter as last saved, or null for a new one
  newsletters: [],
  limit: 3,
  started: false,         // false: show the "start from" choices instead of the blocks

  /** Replace the state, re-render, and refresh the preview. `render: false` skips rebuilding the page (a text
   *  field being typed into keeps its caret); the counts, dirty mark and preview still update. */
  set(next, { preview = true, focus = null, render = true } = {}) {
    if (ctx.readOnly || next === ctx.state) return;
    ctx.state = next;
    ctx.started = true;
    if (render) renderAll();
    else { applyAccent(ctx.state.accent_color); ctx.renderCounts?.(); ctx.onChange?.(); }
    if (focus) focusBlock(focus);
    if (preview) requestPreview();
  },
  liveErrors: new Map(),
  isDirty() {
    if (!ctx.started) return false;
    return ctx.saved ? !M.sameDefinition(ctx.saved.state, ctx.state) : ctx.state.blocks.length > 0;
  },
  toast,
  announce,
  openLibrary,
  closeLibrary,
  setView,
  openPreview,
  render: () => renderAll(),
};

// ── Status and toasts ────────────────────────────────────────────────────────

let toastTimer = null;
/** A short message at the bottom; `action` = {label, run} adds a button (Undo). */
function toast(message, { action = null, tone = 'info', ms = 6000 } = {}) {
  const el = $('toast');
  clearTimeout(toastTimer);
  const children = [h('span', { text: message })];
  if (action) {
    children.push(h('button', {
      type: 'button', class: 'btn btn--quiet', text: action.label,
      onclick: () => { hideToast(); action.run(); },
    }));
  }
  mount(el, ...children);
  el.dataset.tone = tone;
  el.hidden = false;
  toastTimer = setTimeout(hideToast, ms);
}

function hideToast() {
  clearTimeout(toastTimer);
  $('toast').hidden = true;
}

/** Read out by screen readers without moving focus. */
function announce(message) {
  const el = $('sr-status');
  el.textContent = '';
  setTimeout(() => { el.textContent = message; }, 30);
}

// ── Layout: views, sheet, slide-over ─────────────────────────────────────────

const phone = window.matchMedia('(max-width: 699px)');
const tablet = window.matchMedia('(max-width: 1099px)');
let returnFocus = null;

function setView(view) {
  studio.dataset.view = view;
  for (const b of document.querySelectorAll('.tabbar__btn')) b.setAttribute('aria-pressed', String(b.dataset.view === view));
  if (view === 'preview') layoutPreview();
}

function openLibrary() {
  if (!phone.matches) { $('lib-search').focus(); return; }
  returnFocus = document.activeElement;
  studio.dataset.libraryOpen = 'true';
  $('scrim').hidden = false;
  // The heading, not the search box: focusing a field would open the keyboard over the sheet.
  setTimeout(() => $('lib-title').focus({ preventScroll: true }), 50);
}

function closeLibrary() {
  if (studio.dataset.libraryOpen !== 'true') return;
  studio.dataset.libraryOpen = 'false';
  if (studio.dataset.previewOpen !== 'true') $('scrim').hidden = true;
  returnFocus?.focus?.();
}

function openPreview() {
  if (phone.matches) { setView('preview'); return; }
  if (!tablet.matches) return;
  returnFocus = document.activeElement;
  studio.dataset.previewOpen = 'true';
  $('scrim').hidden = false;
  layoutPreview();
  setTimeout(() => $('pv-close').focus(), 50);
}

function closePreview() {
  if (studio.dataset.previewOpen !== 'true') return;
  studio.dataset.previewOpen = 'false';
  if (studio.dataset.libraryOpen !== 'true') $('scrim').hidden = true;
  returnFocus?.focus?.();
}

function wireLayout() {
  for (const b of document.querySelectorAll('.tabbar__btn')) b.addEventListener('click', () => setView(b.dataset.view));
  $('lib-close').addEventListener('click', closeLibrary);
  $('pv-close').addEventListener('click', closePreview);
  $('add-open').addEventListener('click', openLibrary);
  $('scrim').addEventListener('click', () => { closeLibrary(); closePreview(); });
  document.addEventListener('keydown', e => {
    if (e.key !== 'Escape') return;
    if (studio.dataset.libraryOpen === 'true') closeLibrary();
    else if (studio.dataset.previewOpen === 'true') closePreview();
  });
  const onResize = () => {
    if (!phone.matches) { closeLibrary(); if (studio.dataset.view === 'preview') setView('build'); }
    if (!tablet.matches) closePreview();
    layoutPreview();
  };
  phone.addEventListener('change', onResize);
  tablet.addEventListener('change', onResize);
  window.addEventListener('resize', debounce(layoutPreview, 80));
}

/** The member's accent drives --nl (and a text colour readable on it). */
function applyAccent(hex) {
  const c = M.normalizeHex(hex) || M.DEFAULT_ACCENT;
  const r = parseInt(c.slice(1, 3), 16), g = parseInt(c.slice(3, 5), 16), b = parseInt(c.slice(5, 7), 16);
  const root = document.body.style;
  root.setProperty('--nl', c);
  root.setProperty('--nl-soft', `rgba(${r},${g},${b},0.16)`);
  root.setProperty('--nl-ink', M.contrast(c, '#0f0f0f') >= 4.5 ? '#0f0f0f' : '#ffffff');
}

function showNotice(text) {
  const el = $('studio-notice');
  if (!text) { el.hidden = true; document.body.style.setProperty('--st-notice', '0px'); return; }
  mount(el, h('strong', { text: 'Sample mode.' }), ' ', text);
  el.hidden = false;
  requestAnimationFrame(() => document.body.style.setProperty('--st-notice', `${el.offsetHeight}px`));
}

// ── Rendering ────────────────────────────────────────────────────────────────

function renderAll() {
  applyAccent(ctx.state.accent_color);
  renderLibrary();
  renderCanvas();
  renderEmpty();
  ctx.onChange?.();
}

function renderEmpty() {
  const el = $('empty');
  const show = !ctx.started && !ctx.readOnly;
  studio.dataset.started = String(!show);
  el.hidden = !show;
  $('blocks').hidden = show;
  $('add-open').hidden = show;
  if (!show) return;
  const starter = t => h('li', {},
    h('button', {
      type: 'button', class: 'starter', style: { '--dot': t.accent_color },
      onclick: () => startFrom(M.starterFor(t, ctx.catalog), `Started from ${t.name}.`),
    },
    h('span', { class: 'starter__dot', 'aria-hidden': 'true' }),
    h('span', {}, h('span', { class: 'starter__name', text: t.name }),
      h('span', { class: 'starter__meta', text: `${t.sections.length} sections · ${t.tagline}` })),
    h('span', { class: 'starter__go', text: 'Start from this' })));
  mount(el,
    h('h3', { text: 'Start from a ready-made newsletter' }),
    h('p', { text: 'Copy one of our newsletters and make it yours: drop the sections you skip, add others from any topic, rename it and pick its colour. Or start with nothing.' }),
    h('ul', { class: 'starters' },
      ctx.catalog.topics.map(starter),
      h('li', {}, h('button', {
        type: 'button', class: 'starter starter--blank',
        onclick: () => startFrom(M.emptyState(), 'Started an empty newsletter. Add sections from the library.'),
      },
      h('span', { class: 'starter__dot', 'aria-hidden': 'true' }),
      h('span', {}, h('span', { class: 'starter__name', text: 'Start empty' }),
        h('span', { class: 'starter__meta', text: 'Pick every section yourself' })),
      h('span', { class: 'starter__go', text: 'Start empty' })))));
}

function startFrom(state, message) {
  ctx.state = state;
  ctx.started = true;
  renderAll();
  requestPreview({ now: true });
  announce(message);
  // On phones, focusing the name would pop the keyboard over the newsletter just started.
  if (phone.matches) $('blocks-title').focus();
  else { $('nl-name').focus(); $('nl-name').select(); }
}

// ── Gate ─────────────────────────────────────────────────────────────────────

const GOOGLE_PATH = 'M21.35 11.1H12v2.98h5.35c-.23 1.48-1.62 4.34-5.35 4.34a5.98 5.98 0 0 1 0-11.96c1.84 0 3.07.78 3.78 1.46l2.58-2.49C16.72 3.9 14.6 3 12 3a9 9 0 0 0 0 18c5.2 0 8.64-3.65 8.64-8.8 0-.6-.07-1.04-.15-1.5z';

function showGate(kind, detail = '') {
  const card = $('gate-card');
  const benefits = h('ul', { class: 'gate__list' },
    h('li', { text: 'Pick sections from every topic we cover: AI, markets, video games, board games, 3D printing.' }),
    h('li', { text: 'Add widgets like the paper of the week or your own stock watchlist.' }),
    h('li', { text: 'Describe a custom section in your own words, like “SpaceX launches”.' }),
    h('li', { text: 'Name it, colour it, and see the exact email before it is sent.' }));
  let content;
  if (kind === 'signedout') {
    content = [
      h('h2', { class: 'gate__title', id: 'gate-title', text: 'Build your own Monday newsletter' }),
      h('p', { class: 'gate__lead', text: 'The studio assembles one email from the stories you care about, every Monday morning. Behind this card is a sample.' }),
      benefits,
      h('div', { class: 'gate__actions' },
        h('a', { class: 'btn btn--primary', href: '/login.html?returnUrl=studio.html' }, iconSvg(GOOGLE_PATH), 'Sign in with Google'),
        h('button', { type: 'button', class: 'btn btn--quiet', text: 'Look at the sample', onclick: hideGate })),
      h('p', { class: 'gate__fine', text: 'The studio is part of Premium, open by invitation while we test it.' }),
    ];
  } else if (kind === 'free') {
    const soon = h('p', { class: 'upgrade-gate__soon', text: 'Upgrade plans are coming soon. The studio is open by invitation while we test it.' });
    content = [
      h('div', { class: 'upgrade-gate' },
        h('div', { class: 'upgrade-gate__badge', text: '◆ Premium' }),
        h('h2', { class: 'upgrade-gate__headline', id: 'gate-title', text: 'Build your own newsletter' }),
        h('ul', { class: 'upgrade-gate__benefits' },
          h('li', { text: 'Mix sections from every topic' }),
          h('li', { text: 'Add widgets and custom sections' }),
          h('li', { text: 'Your name, your colour, a live preview' })),
        h('button', { type: 'button', class: 'upgrade-gate__btn', text: 'Upgrade to Premium', onclick: () => soon.classList.add('visible') }),
        soon),
      h('div', { class: 'gate__actions', style: { marginTop: '12px' } },
        h('button', { type: 'button', class: 'btn btn--quiet', text: 'Look at the sample', onclick: hideGate })),
    ];
  } else {
    content = [
      h('h2', { class: 'gate__title', id: 'gate-title', text: 'The studio did not load' }),
      h('p', { class: 'gate__lead', text: detail || 'Check your connection, then try again.' }),
      h('div', { class: 'gate__actions' },
        h('button', { type: 'button', class: 'btn btn--primary', text: 'Try again', onclick: () => location.reload() }),
        h('a', { class: 'btn btn--quiet', href: '?mock=1', text: 'Open the sample studio instead' })),
    ];
  }
  mount(card, ...content);
  card.classList.toggle('gate__card--bare', kind === 'free');
  $('gate').hidden = false;
  studio.inert = true;
  studio.setAttribute('aria-hidden', 'true');
  card.querySelector('a, button')?.focus({ preventScroll: true });
}

/** Look at the sample: the gate folds into a bar, the studio stays read-only. */
function hideGate() {
  $('gate').hidden = true;
  studio.inert = false;
  studio.removeAttribute('aria-hidden');
  const bar = $('canvas-bar');
  const reopen = h('button', { type: 'button', class: 'btn btn--primary', onclick: () => showGate(ctx.mode) },
    ctx.mode === 'free' ? 'Upgrade to build your own' : 'Sign in to build your own');
  mount(bar, h('p', { class: 'readonly-note', text: 'Sample newsletter, read-only.' }), reopen);
  reopen.focus();
}

function iconSvg(d) {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('width', '16');
  svg.setAttribute('height', '16');
  svg.setAttribute('aria-hidden', 'true');
  const p = document.createElementNS(ns, 'path');
  p.setAttribute('d', d);
  p.setAttribute('fill', 'currentColor');
  svg.append(p);
  return svg;
}

// ── Start-up ─────────────────────────────────────────────────────────────────

/** Resolves to {mode, email}: who is looking, from Firebase and /auth/me (or ?as= in mock mode). */
function whoIsThis() {
  if (FORCE_MOCK) return Promise.resolve({ mode: MOCK_AS, email: MOCK_AS === 'signedout' ? '' : 'you@example.com' });
  return new Promise(resolve => {
    let done = false;
    const finish = v => { if (!done) { done = true; resolve(v); } };
    setTimeout(() => finish({ mode: 'signedout', email: '' }), 4000);
    onAuthStateChanged(auth, async user => {
      if (!user || !user.emailVerified) { finish({ mode: 'signedout', email: '' }); return; }
      try {
        const res = await authFetch(`${API_BASE}/auth/me`);
        if (!res.ok) throw new ApiError('Could not check your account.', { status: res.status });
        const me = await res.json();
        finish({ mode: me.tier === 'premium' ? 'premium' : 'free', email: user.email });
      } catch {
        finish({ mode: 'error', email: user.email, detail: 'We could not check your account. Check your connection, then try again.' });
      }
    });
  });
}

async function loadDemo() {
  const { api, catalog } = await connect({ forceMock: true });
  ctx.api = api;
  ctx.catalog = catalog;
  ctx.mock = true;
  const def = await (await fetch(DEMO_URL)).json();
  ctx.state = M.fromDefinition(def);
  ctx.started = true;
}

async function loadStudio() {
  const { api, catalog, reason } = await connect({ forceMock: FORCE_MOCK });
  ctx.api = api;
  ctx.catalog = catalog;
  ctx.mock = reason !== 'real';
  if (reason === 'forced') showNotice('Sections show made-up stories, and saved newsletters stay in this browser.');
  if (reason === 'not-ready') showNotice('The studio is not open yet: sections show made-up stories, and saved newsletters stay in this browser.');
  const { newsletters, limit } = await api.listNewsletters();
  ctx.newsletters = newsletters;
  ctx.limit = limit;
  const latest = newsletters.slice().sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at)))[0];
  if (latest) openSaved(latest);
}

/** Load a saved newsletter into the editor. */
export function openSaved(item) {
  ctx.state = M.fromDefinition(item.definition);
  ctx.saved = { id: item.id, state: ctx.state };
  ctx.started = true;
}

async function main() {
  wireLayout();
  initLibrary(ctx);
  initCanvas(ctx);
  initPreview(ctx);

  const who = await whoIsThis();
  ctx.mode = who.mode;
  ctx.email = who.email;
  try {
    if (who.mode === 'premium') {
      ctx.readOnly = false;
      await loadStudio();
    } else if (who.mode === 'error') {
      await loadDemo();
    } else {
      await loadDemo();
    }
  } catch (e) {
    console.error('[studio] load failed:', e);
    ctx.mode = 'error';
    ctx.readOnly = true;
    studio.removeAttribute('aria-busy');
    showGate('error', e instanceof ApiError ? e.message : '');
    return;
  }
  studio.removeAttribute('aria-busy');
  renderAll();
  requestPreview({ now: true });
  if (ctx.mode !== 'premium') showGate(ctx.mode, who.detail);
}

main();

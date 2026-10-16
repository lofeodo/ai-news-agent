// studio/preview.js — the live preview: the email exactly as it will arrive, refreshed 600 ms after the last
// change (POST /auth/newsletters/preview, or the mock). Shown in a sandboxed iframe through srcdoc with the same
// content policy as the archive (no script, https images, inline styles only); links open in a new tab.
// An error keeps the last good preview on screen. The frame is drawn at the email's real width (600 px desktop,
// 375 px phone) and scaled down to fit the column; the email scrolls inside it.

import * as M from './model.js';
import { h, mount, debounce } from './dom.js';

const DELAY_MS = 600;
const CLIP_BYTES = 102_000;       // Gmail clips an email around here (contract C7)
const WARN_BYTES = 90_000;
// Same policy issues.html applies to its srcdoc copy (agents/agent_subscriptions.py PREVIEW_CSP).
// The style only hides the frame's own scrollbars (phones show none; a desktop scrollbar would steal 15 px of
// the phone width). It does not touch the email.
const POLICY = `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src https: data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">`
  + '<style>html{scrollbar-width:none}html::-webkit-scrollbar{display:none}</style>';

let ctx = null;
let width = 600;
let controller = null;
let seq = 0;
let lastGood = null;      // {html, size_bytes, warnings, json}
const $ = id => document.getElementById(id);

function setWidth(w) {
  width = w;
  for (const x of document.querySelectorAll('.seg__btn')) x.setAttribute('aria-pressed', String(Number(x.dataset.width) === w));
  layoutPreview();
}

export function initPreview(c) {
  ctx = c;
  let chosen = false;
  for (const b of document.querySelectorAll('.seg__btn')) {
    b.addEventListener('click', () => { chosen = true; setWidth(Number(b.dataset.width)); });
  }
  // Until the member picks one, preview at the size of the device they are on.
  const phone = window.matchMedia('(max-width: 699px)');
  const follow = () => { if (!chosen) setWidth(phone.matches ? 375 : 600); };
  phone.addEventListener('change', follow);
  follow();
  if ('ResizeObserver' in window) new ResizeObserver(() => layoutPreview()).observe($('pv-stage'));
  layoutPreview();
}

/** Scale the frame so the email's real width fits the stage. */
export function layoutPreview() {
  const stage = $('pv-stage');
  const frame = $('pv-frame');
  if (!stage || !stage.clientWidth) return;
  const pad = stage.clientWidth < 480 ? 12 : 20;
  const availW = stage.clientWidth - pad * 2;
  const availH = stage.clientHeight - pad;
  const scale = Math.min(1, availW / width);
  frame.style.top = `${pad}px`;
  frame.style.width = `${width}px`;
  frame.style.height = `${Math.max(200, availH / scale)}px`;
  frame.style.transform = `translateX(-50%) scale(${scale})`;
  frame.dataset.scale = scale.toFixed(2);
}

function setState(state, status) {
  $('preview').dataset.state = state;
  $('pv-status').textContent = status || '';
}

function renderMeter(bytes) {
  const el = $('meter');
  if (bytes == null) { mount(el); return; }
  const kb = Math.round(bytes / 1000);
  const pct = Math.min(100, (bytes / CLIP_BYTES) * 100);
  const level = bytes > CLIP_BYTES ? 'over' : bytes > WARN_BYTES ? 'warn' : 'ok';
  el.dataset.level = level;
  mount(el,
    h('div', { class: 'meter__row' },
      h('span', { class: 'meter__label', text: 'Email size' }),
      h('span', { class: 'meter__value', text: `${kb} KB of about 102 KB` })),
    h('div', {
      class: 'meter__track', role: 'meter', 'aria-label': 'Email size', 'aria-valuemin': '0',
      'aria-valuemax': String(CLIP_BYTES), 'aria-valuenow': String(Math.min(bytes, CLIP_BYTES)),
      'aria-valuetext': `${kb} kilobytes; Gmail cuts off emails over about 102`,
    },
    h('span', { class: 'meter__fill', style: { width: `${pct}%` } }),
    h('span', { class: 'meter__mark', style: { left: `${(WARN_BYTES / CLIP_BYTES) * 100}%` }, 'aria-hidden': 'true' })),
    level !== 'ok' ? h('p', { class: 'meter__note', text: level === 'over'
      ? 'Gmail will cut this email off. Remove a section or show fewer stories.'
      : 'Close to the size where Gmail cuts emails off.' }) : null);
}

function renderWarnings(list) {
  mount($('pv-warnings'), (list || []).map(w => h('li', { text: plainWarning(w) })));
}

/** The renderer's warnings are written for logs ("section finance/'Canada' left out: ..."); soften the
 *  prefix for readers but keep the reason. */
function plainWarning(w) {
  return String(w).replace(/^section (\w+)\/'([^']+)'/, (m, t, s) => `${s} (${t})`).replace(/^custom section '([^']+)'/, 'Custom section “$1”');
}

function show(result) {
  const frame = $('pv-iframe');
  frame.srcdoc = POLICY + result.html;
  renderMeter(result.size_bytes);
  renderWarnings(result.warnings);
}

function placeholder(text) {
  $('pv-iframe').srcdoc = POLICY + `<body style="margin:0;display:grid;place-items:center;height:100vh;background:#0b0b0b;color:#6f6f6f;font:14px/1.6 Menlo,Consolas,monospace;text-align:center;padding:24px;box-sizing:border-box">${text}</body>`;
  renderMeter(null);
  renderWarnings([]);
}

async function run() {
  if (!ctx.api) return;
  const state = ctx.state;
  if (!state.blocks.length) {
    lastGood = null;
    placeholder('Add a section to see your email.');
    setState('empty', ctx.started ? 'Your email appears here as you add sections.' : 'Pick a starting point to see your email.');
    return;
  }
  const problems = M.validate(state, ctx.catalog);
  if (problems.length) {
    setState('stale', lastGood ? 'Fix the marked items to update the preview. Showing the last version.' : 'Fix the marked items to see the preview.');
    return;
  }
  const definition = M.serialize(state);
  const json = JSON.stringify(definition);
  if (lastGood?.json === json) { setState('ready', statusText()); return; }

  controller?.abort();
  controller = new AbortController();
  const mine = ++seq;
  setState('loading', 'Updating the preview…');
  try {
    const result = await ctx.api.previewNewsletter(definition, { signal: controller.signal });
    if (mine !== seq) return;
    lastGood = { ...result, json };
    show(result);
    setState('ready', statusText());
  } catch (e) {
    if (e?.name === 'AbortError' || mine !== seq) return;
    const msg = e?.message || 'The preview did not load.';
    setState('error', lastGood ? `${msg} Showing the last good preview.` : msg);
    ctx.announce(`Preview not updated: ${msg}`);
  }
}

function statusText() {
  return ctx.mock ? 'Up to date · sample stories, not this week’s news' : 'Up to date · this week’s stories';
}

const debounced = debounce(run, DELAY_MS);

/** Refresh soon (or now). Safe to call on every change. */
export function requestPreview({ now = false } = {}) {
  if (!ctx) return;
  if (now) debounced.flush(); else { setState(lastGood ? 'pending' : 'loading', 'Updating the preview…'); debounced(); }
}

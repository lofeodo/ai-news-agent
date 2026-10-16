// studio/mock-api.js — a stand-in for the studio API (C6) until roadmap E builds it. Same functions as api.js.
//
// - The catalog is fixtures/catalog.json (built from topics/*.yaml by tests/make_studio_fixtures.py).
// - Saved newsletters live in this browser's localStorage (every access wrapped: private windows may refuse it).
// - The preview is assembled here from fixtures/fragments/, which the real block renderer (agents/render/) drew
//   with placeholder values; assemble() repeats engine.render()'s block loop so the result looks like a real
//   custom newsletter, filled with the made-up articles in fixtures/sample-articles.json.
// - Refine is simulated. Every call waits LATENCY_MS so loading states show as they will for real.

import { ApiError } from './api.js';
import * as M from './model.js';

export const isMock = true;
const LATENCY_MS = 300;
const LIMIT = 3;
const STORE_KEY = 'lsm_studio_mock_v1';
const BASE = new URL('./fixtures/', import.meta.url);

const FRAGMENTS = ['page', 'intro', 'toc-cell', 'section', 'section-mixed', 'card', 'card-last', 'card-hn',
  'card-hn-last', 'empty', 'share', 'disclaimer', 'widget-row-up', 'widget-row-down'];

let _fixtures = null;

async function fetchText(path) {
  const res = await fetch(new URL(path, BASE));
  if (!res.ok) throw new ApiError('Could not load the sample content.', { status: res.status });
  return res.text();
}

function loadFixtures() {
  _fixtures ??= (async () => {
    const [catalog, content, samples] = await Promise.all(
      ['catalog.json', 'mock-content.json', 'sample-articles.json'].map(async p => JSON.parse(await fetchText(p))));
    const frags = {};
    const ids = [...FRAGMENTS, ...catalog.widgets.map(w => `widget-${w.id}`)];
    await Promise.all(ids.map(async id => { frags[id] = await fetchText(`fragments/${id}.html`); }));
    return { catalog, content, samples, frags };
  })().catch(e => { _fixtures = null; throw e; });
  return _fixtures;
}

function wait(ms, signal) {
  return new Promise((resolve, reject) => {
    const t = setTimeout(resolve, ms);
    signal?.addEventListener('abort', () => { clearTimeout(t); reject(new DOMException('Aborted', 'AbortError')); }, { once: true });
  });
}

const clone = v => JSON.parse(JSON.stringify(v));

// ── Storage ──────────────────────────────────────────────────────────────────

let _memory = { newsletters: [] };   // used when localStorage is unavailable

function readStore() {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    if (raw) _memory = JSON.parse(raw);
  } catch { /* private mode or corrupt: keep the in-memory copy */ }
  if (!Array.isArray(_memory?.newsletters)) _memory = { newsletters: [] };
  return _memory;
}

function writeStore(store) {
  _memory = store;
  try { localStorage.setItem(STORE_KEY, JSON.stringify(store)); } catch { /* in-memory only */ }
}

function newId() {
  const abc = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_';
  const bytes = crypto.getRandomValues(new Uint8Array(20));
  return 'c_' + Array.from(bytes, b => abc[b % 64]).join('');
}

// ── C6 functions ─────────────────────────────────────────────────────────────

export async function getCatalog() {
  const { catalog } = await loadFixtures();
  await wait(LATENCY_MS);
  return clone(catalog);
}

export async function listNewsletters() {
  await wait(LATENCY_MS);
  return { newsletters: clone(readStore().newsletters), limit: LIMIT };
}

/** The validator's messages in agents/newsletter_def.py's shape ("block 3 (section): ..."), so the page's
 *  mapping of server errors onto blocks is exercised by the mock too. */
function serverErrors(definition, catalog) {
  const state = M.fromDefinition(definition);
  return M.validate(state, catalog).map(e => {
    const i = e.key ? state.blocks.findIndex(b => b.key === e.key) : -1;
    return i >= 0 ? `block ${i + 1} (${state.blocks[i].type}): ${e.message}` : e.message;
  });
}

export async function saveNewsletter({ id, definition }) {
  const { catalog } = await loadFixtures();
  await wait(LATENCY_MS);
  const errors = serverErrors(definition, catalog);
  if (errors.length) throw new ApiError('Some blocks need a fix before saving.', { status: 400, errors });
  const store = readStore();
  const now = new Date().toISOString();
  let item = id ? store.newsletters.find(n => n.id === id) : null;
  if (id && !item) throw new ApiError('That newsletter no longer exists.', { status: 404 });
  if (!item) {
    if (store.newsletters.length >= LIMIT) {
      throw new ApiError('You already have the most newsletters you can keep. Delete one first.', { status: 409, code: 'limit_reached' });
    }
    item = { id: newId(), subscribed: false };
    store.newsletters.push(item);
  }
  item.definition = clone(definition);
  item.updated_at = now;
  writeStore(store);
  return { id: item.id, definition: clone(item.definition) };
}

export async function deleteNewsletter(id) {
  await wait(LATENCY_MS);
  const store = readStore();
  const before = store.newsletters.length;
  store.newsletters = store.newsletters.filter(n => n.id !== id);
  if (store.newsletters.length === before) throw new ApiError('That newsletter no longer exists.', { status: 404 });
  writeStore(store);
  return { deleted: true };
}

export async function setSubscribed(id, subscribed) {
  await wait(LATENCY_MS);
  const store = readStore();
  const item = store.newsletters.find(n => n.id === id);
  if (!item) throw new ApiError('That newsletter no longer exists.', { status: 404 });
  item.subscribed = !!subscribed;
  writeStore(store);
  return { subscribed: item.subscribed };
}

export async function refineTopic(rawTopic) {
  await wait(LATENCY_MS * 2);
  const t = String(rawTopic || '').trim().replace(/\s+/g, ' ');
  if (!t) throw new ApiError('Something went wrong. Please try again.', { status: 422 });
  const head = t.charAt(0).toUpperCase() + t.slice(1);
  return { refined_topic: `${head}: news, launches and notable developments`.slice(0, 200) };
}

export async function previewNewsletter(definition, { signal } = {}) {
  const fx = await loadFixtures();
  await wait(LATENCY_MS, signal);
  return assemble(definition, fx);
}

// ── Assembling the email (mirrors agents/render/engine.py render()) ─────────────

/** agents/render/text.esc: HTML-escape and break up {{ }} so text can never form a mail-merge placeholder. */
export function esc(text) {
  return String(text ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#x27;')
    .replace(/\{\{/g, '&#123;&#123;').replace(/\}\}/g, '&#125;&#125;');
}

/** One pass over __KEY__ placeholders, so a value that happens to contain "__NAME__" is never re-filled. */
function fill(tpl, values) {
  return tpl.replace(/__([A-Z0-9]+)__/g, (m, k) => (k in values ? values[k] : m));
}

function colours(tpl, ph, accent, topicAccent) {
  let out = tpl.split(ph.accent).join(accent).split(ph.link).join(M.darken(accent));
  if (topicAccent) out = out.split(ph.topic_accent).join(topicAccent);
  return out;
}

function nextMonday(from = new Date()) {
  const d = new Date(from);
  d.setDate(d.getDate() + (((8 - d.getDay()) % 7) || 7));
  return d;
}

/** Python's "%B %d, %Y": "October 05, 2026". */
function weekLabel(d) {
  const month = d.toLocaleString('en-US', { month: 'long' });
  return `${month} ${String(d.getDate()).padStart(2, '0')}, ${d.getFullYear()}`;
}

function joinParts(parts) {
  if (parts.length <= 2) return parts.join(' and ');
  return parts.slice(0, -1).join(', ') + ', and ' + parts[parts.length - 1];
}

/** engine.templated_intro */
function templatedIntro(shown) {
  const parts = [];
  let first = true;
  for (const [kind, name, n] of shown) {
    if (n <= 0) continue;
    if (kind === 'custom') parts.push(`your custom section on ${name}`);
    else if (first) { parts.push(`${n} ${n === 1 ? 'story' : 'stories'} in ${name}`); first = false; }
    else parts.push(`${n} in ${name}`);
  }
  return parts.length ? `This week: ${joinParts(parts)}.` : 'Your newsletter for this week.';
}

function tocLabel(name) {
  return name.split(' &')[0].split(',')[0].toUpperCase();
}

function customArticles(block) {
  const q = block.query;
  return Array.from({ length: 5 }, (_, i) => ({
    title: `Sample story ${i + 1} about ${block.title}`,
    summary: `A made-up summary standing in for a real article that matches “${q}”. Real stories appear once custom sections are live.`,
    points: i === 0 ? 120 : null, points_source: i === 0 ? 'hn' : null,
  }));
}

/** A stable made-up quote per ticker, so the watchlist looks alive without pretending to be real. */
function fakeQuote(ticker) {
  let h = 0;
  for (const ch of ticker) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  const price = 20 + (h % 48000) / 100;
  const change = ((h >> 8) % 900) / 100 - 4.5;
  return { price: `$${price.toFixed(2)}`, up: change >= 0, change: `${change >= 0 ? '+' : '−'}${Math.abs(change).toFixed(1)}%` };
}

function cards(articles, fx, accent, path) {
  const ph = fx.content.placeholders;
  return articles.map((a, j) => {
    const last = j === articles.length - 1;
    const hn = a.points_source === 'hn' && a.points;
    const tpl = fx.frags[`card${hn ? '-hn' : ''}${last ? '-last' : ''}`];
    const url = `https://example.com/sample/${path}/${j + 1}`;
    return fill(colours(tpl, ph, accent).split(ph.url).join(esc(url)), {
      TITLE: esc(a.title), SUMMARY: esc(a.summary), HN: hn ? String(a.points) : '',
    });
  }).join('');
}

function widgetHtml(block, fx, accent) {
  const ph = fx.content.placeholders;
  let html = colours(fx.frags[`widget-${block.widget}`] || '', ph, accent);
  if (block.widget === 'stock_watchlist') {
    const rows = (block.config?.tickers || []).map(t => {
      const q = fakeQuote(t);
      return fill(fx.frags[q.up ? 'widget-row-up' : 'widget-row-down'], {
        LABEL: esc(t), VALUE: esc(q.price), CHANGE: esc(q.change),
      });
    }).join('');
    html = fill(html, { ROWS: rows });
  }
  return html;
}

/** Build {html, size_bytes, warnings} the way render_from_editions does, from sample content. */
export function assemble(definition, fx, today = new Date()) {
  const { content, samples, catalog } = fx;
  const ph = content.placeholders;
  const accent = definition.accent_color;
  const topicIds = [];
  for (const b of definition.blocks) if (b.topic && !topicIds.includes(b.topic)) topicIds.push(b.topic);
  const mixed = topicIds.length + (definition.blocks.some(b => b.type === 'custom_section') ? 1 : 0) > 1;
  const warnings = [];
  const lead = [], rest = [], toc = [], shown = [], used = [];
  let hasIntro = false, number = 0;
  const use = t => { if (t && !used.includes(t)) used.push(t); };

  for (const block of definition.blocks) {
    if (block.type === 'intro') { hasIntro = true; continue; }
    let fragment = '';
    if (block.type === 'widget') {
      fragment = widgetHtml(block, fx, accent);
      if (!fragment) warnings.push(`widget '${block.widget}' left out: no renderer for it`);
      else {
        use(block.topic);
        if (content.widget_toc[block.widget]) toc.push(content.widget_toc[block.widget]);
      }
      if (!rest.length) { lead.push(fragment); continue; }
    } else if (block.type === 'section') {
      const topic = catalog.topics.find(t => t.id === block.topic);
      const sec = topic?.sections.find(s => s.name === block.section);
      const articles = (samples[block.topic]?.[block.section] || []).slice(0, block.max_items);
      if (!articles.length && block.if_empty === 'hide') continue;
      number += 1;
      const num = String(number).padStart(2, '0');
      const body = articles.length ? cards(articles, fx, accent, block.topic) : fill(fx.frags.empty, { TEXT: content.empty_text });
      const tpl = colours(fx.frags[mixed ? 'section-mixed' : 'section'], ph, accent, topic?.accent_color || accent);
      fragment = fill(tpl, {
        // Section names come from the topic profiles and go in unescaped, exactly as the engine does.
        NAME: block.section, NUM: num, ICON: sec?.icon || content.default_icon, BODY: body,
        MARKER: mixed ? `${block.topic}:${block.section}` : block.section,
        TOPIC: esc(content.topic_labels[block.topic] || topic?.name || block.topic),
      });
      toc.push([num, tocLabel(block.section)]);
      shown.push(['section', block.section, articles.length]);
      use(block.topic);
    } else if (block.type === 'custom_section') {
      const articles = customArticles(block).slice(0, block.max_items);
      number += 1;
      const num = String(number).padStart(2, '0');
      const title = esc(block.title);
      const tpl = colours(fx.frags[mixed ? 'section-mixed' : 'section'], ph, accent, accent);
      // Custom sections carry the plain "Custom" label in the newsletter's own accent (engine: no topic_accent).
      fragment = fill(tpl, {
        NAME: title, NUM: num, ICON: content.custom_icon, BODY: cards(articles, fx, accent, 'custom'),
        MARKER: `custom:${title}`, TOPIC: content.custom_label,
      });
      toc.push([num, esc(tocLabel(block.title)).slice(0, content.toc_label_max)]);
      shown.push(['custom', block.title, articles.length]);
    } else if (block.type === 'share') {
      fragment = fx.frags.share;
    }
    rest.push(fragment);
  }

  const week = weekLabel(nextMonday(today));
  const intro = templatedIntro(shown);
  const introHtml = hasIntro ? fill(fx.frags.intro, { INTRO: esc(intro), WEEK: esc(week), LABEL: content.intro_label }) : '';
  const disclaimers = [];
  for (const t of used) {
    const d = content.disclaimers[t];
    if (d && !disclaimers.includes(d)) disclaimers.push(d);
  }

  const cells = toc.slice();
  while (cells.length % 4 !== 0) cells.push(['', '']);
  const cell = ([n, s]) => fill(colours(fx.frags['toc-cell'], ph, accent), { NUM: n, LABEL: s });
  const name = esc(definition.name);
  let html = fill(colours(fx.frags.page, ph, accent), {
    NAME: name, TAGLINE: esc(content.tagline), WEEK: esc(week), PREHEADER: esc(intro.slice(0, 200)),
    TOC1: cells.slice(0, 4).map(cell).join(''), TOC2: cells.slice(4).map(cell).join(''),
    INTRO: introHtml, LEAD: lead.join(''), REST: rest.join(''),
    DISCLAIMERS: disclaimers.map(d => fill(fx.frags.disclaimer, { TEXT: esc(d) })).join(''),
  });
  // The preview is not a real send: there is no subscriber to unsubscribe.
  html = html.replace(/\{\{(UNSUBSCRIBE|PREFERENCES)_URL\}\}/g, '#');
  const size = new TextEncoder().encode(html).length;
  if (size > content.size_warning_bytes) {
    warnings.push(`this email is ${Math.round(size / 1000)} KB; Gmail cuts off emails over about 102 KB, so remove a section or lower max_items`);
  }
  return { html, size_bytes: size, warnings };
}

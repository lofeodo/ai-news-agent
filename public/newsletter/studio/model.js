// studio/model.js — the newsletter being edited, and every change to it, as pure functions (no DOM).
//
// A definition follows contract C3 (docs/plans/newsletter-platform.md; validator agents/newsletter_def.py).
// While editing, each block also carries a `key` (stable id for the page: focus, drag, undo); serialize() drops it.
// Every operation returns a new state and never mutates its input, so undo is just keeping the old value.

export const VERSION = 1;
export const LIMITS = Object.freeze({
  blocks: 12, sections: 8, custom: 3, widgets: 4,
  minItems: 1, maxItems: 5, name: 60, query: 200, tickers: 8,
});
export const DEFAULT_ITEMS = 3;
export const DEFAULT_ACCENT = '#c8b89a';
export const ACCENT_PRESETS = ['#c8b89a', '#7fd1a0', '#b79cff', '#f2a65a', '#5ec8e5', '#ef7d8f', '#e8d26a', '#9db4c8'];
const IF_EMPTY = ['note', 'hide'];
const COLOR_RE = /^#[0-9a-f]{6}$/i;
const TICKER_RE = /^[A-Z0-9][A-Z0-9.\-]{0,9}$/;

let _seq = 0;
export function newKey() {
  _seq += 1;
  return `b${Date.now().toString(36)}${_seq.toString(36)}`;
}

// ── Building states ──────────────────────────────────────────────────────────

export function emptyState(name = 'My newsletter', accent = DEFAULT_ACCENT) {
  return { version: VERSION, name, accent_color: accent, blocks: [] };
}

/** A saved C3 definition -> an editable state (fresh keys, normalized fields). */
export function fromDefinition(def) {
  const blocks = (def?.blocks || []).map(b => ({ ...clone(b), key: newKey() }));
  return { version: VERSION, name: def?.name ?? '', accent_color: def?.accent_color ?? DEFAULT_ACCENT, blocks };
}

export function sectionBlock(topic, section, maxItems = DEFAULT_ITEMS) {
  return { type: 'section', topic, section, max_items: maxItems };
}

export function customBlock(title, query, maxItems = DEFAULT_ITEMS) {
  return { type: 'custom_section', title, query, max_items: maxItems };
}

/** A widget block with each config field at its default. `topic` is the edition that supplies the data
 *  (omitted for personal widgets; for a widget available in any topic, the caller picks one). */
export function widgetBlock(widget, topic) {
  const config = {};
  for (const f of widget.config_fields || []) config[f.name] = clone(f.default);
  const b = { type: 'widget', widget: widget.id, config };
  if (widget.scope !== 'personal') b.topic = topic ?? (widget.topics.includes('*') ? 'ai' : widget.topics[0]);
  return b;
}

// Widgets a ready-made newsletter leads with (newsletter_def.from_topic: the AI paper spotlight).
const STARTER_WIDGETS = { ai: ['paper_spotlight'] };

/** A starting point copied from a ready-made newsletter: intro, its lead widget, every section (up to the limit,
 *  DEFAULT_ITEMS each) and the share strip after the first section, as from_topic builds it. */
export function starterFor(topic, catalog) {
  const s = emptyState(`My ${topic.name}`.slice(0, LIMITS.name), topic.accent_color);
  const blocks = [{ type: 'intro' }];
  for (const wid of STARTER_WIDGETS[topic.id] || []) {
    const w = widgetById(catalog, wid);
    if (w) blocks.push(widgetBlock(w, topic.id));
  }
  topic.sections.slice(0, LIMITS.sections).forEach((sec, i) => {
    blocks.push(sectionBlock(topic.id, sec.name));
    if (i === 0) blocks.push({ type: 'share' });
  });
  return { ...s, blocks: blocks.slice(0, LIMITS.blocks).map(b => ({ ...b, key: newKey() })) };
}

// ── Reading ──────────────────────────────────────────────────────────────────

export function counts(state) {
  const c = { blocks: state.blocks.length, section: 0, custom_section: 0, widget: 0, intro: 0, share: 0 };
  for (const b of state.blocks) c[b.type] = (c[b.type] || 0) + 1;
  return c;
}

export function indexOf(state, key) {
  return state.blocks.findIndex(b => b.key === key);
}

export function topicById(catalog, id) {
  return catalog?.topics?.find(t => t.id === id) || null;
}

export function widgetById(catalog, id) {
  return catalog?.widgets?.find(w => w.id === id) || null;
}

/** Same section, same widget from the same edition, same custom query: adding it twice is never useful. */
function identity(b) {
  switch (b.type) {
    case 'section': return `s|${b.topic}|${b.section}`;
    case 'widget': return `w|${b.widget}|${b.topic ?? ''}`;
    case 'custom_section': return `c|${normQuery(b.query)}`;
    default: return b.type;
  }
}

function normQuery(q) {
  return String(q || '').toLowerCase().split(/\s+/).filter(Boolean).join(' ');
}

export function contains(state, block) {
  const id = identity(block);
  return state.blocks.some(b => identity(b) === id);
}

/** Why `block` cannot be added (a plain sentence), or null if it can. */
export function cannotAdd(state, block) {
  const c = counts(state);
  if (block.type === 'intro' && c.intro) return 'The intro is already in your newsletter.';
  if (block.type === 'share' && c.share) return 'The share strip is already in your newsletter.';
  if (contains(state, block) && block.type !== 'intro' && block.type !== 'share') return 'That is already in your newsletter.';
  if (c.blocks >= LIMITS.blocks) return `Up to ${LIMITS.blocks} blocks in one newsletter.`;
  if (block.type === 'section' && c.section >= LIMITS.sections) return `Up to ${LIMITS.sections} sections.`;
  if (block.type === 'custom_section' && c.custom_section >= LIMITS.custom) return `Up to ${LIMITS.custom} custom sections.`;
  if (block.type === 'widget' && c.widget >= LIMITS.widgets) return `Up to ${LIMITS.widgets} widgets.`;
  return null;
}

// ── Changing ─────────────────────────────────────────────────────────────────

/** Add `block` at `index` (default: the end). The intro always goes first; nothing goes above the intro.
 *  Returns {state, key} or {state, error} with the unchanged state. */
export function addBlock(state, block, index = state.blocks.length) {
  const error = cannotAdd(state, block);
  if (error) return { state, error };
  const key = newKey();
  const blocks = state.blocks.slice();
  const b = { ...clone(block), key };
  if (b.type === 'intro') {
    blocks.unshift(b);
  } else {
    const floor = blocks[0]?.type === 'intro' ? 1 : 0;
    blocks.splice(clampInt(index, floor, blocks.length), 0, b);
  }
  return { state: { ...state, blocks }, key };
}

/** Returns {state, removed: {block, index}} so the caller can offer Undo with restoreBlock. */
export function removeBlock(state, key) {
  const index = indexOf(state, key);
  if (index < 0) return { state, removed: null };
  const blocks = state.blocks.slice();
  const [block] = blocks.splice(index, 1);
  return { state: { ...state, blocks }, removed: { block, index } };
}

export function restoreBlock(state, removed) {
  if (!removed || indexOf(state, removed.block.key) >= 0) return state;
  const blocks = state.blocks.slice();
  blocks.splice(clampInt(removed.index, 0, blocks.length), 0, removed.block);
  return { ...state, blocks };
}

/** The positions a block may take: the intro is pinned first, everything else stays below it. */
export function moveBounds(state, key) {
  const i = indexOf(state, key);
  if (i < 0 || state.blocks[i].type === 'intro') return { min: i, max: i };
  const floor = state.blocks[0]?.type === 'intro' ? 1 : 0;
  return { min: floor, max: state.blocks.length - 1 };
}

export function moveBlock(state, key, toIndex) {
  const from = indexOf(state, key);
  if (from < 0) return state;
  const { min, max } = moveBounds(state, key);
  const to = clampInt(toIndex, min, max);
  if (to === from) return state;
  const blocks = state.blocks.slice();
  const [b] = blocks.splice(from, 1);
  blocks.splice(to, 0, b);
  return { ...state, blocks };
}

export function moveBy(state, key, delta) {
  return moveBlock(state, key, indexOf(state, key) + delta);
}

function updateBlock(state, key, fn) {
  const i = indexOf(state, key);
  if (i < 0) return state;
  const blocks = state.blocks.slice();
  blocks[i] = fn({ ...blocks[i] });
  return { ...state, blocks };
}

export function setItems(state, key, n) {
  return updateBlock(state, key, b => ('max_items' in b ? { ...b, max_items: clampInt(n, LIMITS.minItems, LIMITS.maxItems) } : b));
}

export function setIfEmpty(state, key, value) {
  if (!IF_EMPTY.includes(value)) return state;
  return updateBlock(state, key, b => {
    if (b.type !== 'section') return b;
    const out = { ...b };
    if (value === 'note') delete out.if_empty; else out.if_empty = value;
    return out;
  });
}

export function setConfig(state, key, field, value) {
  return updateBlock(state, key, b => (b.type === 'widget' ? { ...b, config: { ...b.config, [field]: clone(value) } } : b));
}

export function setWidgetTopic(state, key, topic) {
  return updateBlock(state, key, b => (b.type === 'widget' && 'topic' in b ? { ...b, topic } : b));
}

export function setCustom(state, key, title, query) {
  return updateBlock(state, key, b => (b.type === 'custom_section' ? { ...b, title, query } : b));
}

export function rename(state, name) {
  return { ...state, name: String(name ?? '') };
}

export function recolor(state, color) {
  const c = normalizeHex(color);
  return c ? { ...state, accent_color: c } : state;
}

// ── Checking ─────────────────────────────────────────────────────────────────

function textOk(v, max) {
  return typeof v === 'string' && v.trim().length >= 1 && v.trim().length <= max && !/[<>]/.test(v);
}

/** Every rule of C3 (plus sane widget settings) the state breaks: [{key, message}], key null for the
 *  newsletter as a whole. Empty when it can be saved. Messages are plain sentences for the page. */
export function validate(state, catalog) {
  const errors = [];
  const add = (key, message) => errors.push({ key, message });
  if (!textOk(state.name, LIMITS.name)) {
    add(null, String(state.name ?? '').trim()
      ? `The name must be at most ${LIMITS.name} characters, without < or >.`
      : 'Give your newsletter a name.');
  }
  if (!COLOR_RE.test(state.accent_color || '')) add(null, 'The colour must look like #7fd1a0.');
  if (!state.blocks.length) add(null, 'Add at least one section or widget.');

  const c = counts(state);
  if (c.blocks > LIMITS.blocks) add(null, `Up to ${LIMITS.blocks} blocks in one newsletter (you have ${c.blocks}).`);
  if (c.section > LIMITS.sections) add(null, `Up to ${LIMITS.sections} sections (you have ${c.section}).`);
  if (c.custom_section > LIMITS.custom) add(null, `Up to ${LIMITS.custom} custom sections (you have ${c.custom_section}).`);
  if (c.widget > LIMITS.widgets) add(null, `Up to ${LIMITS.widgets} widgets (you have ${c.widget}).`);
  if (c.share > 1) add(null, 'The share strip can appear only once.');
  if (c.intro > 1) add(null, 'The intro can appear only once.');
  if (c.intro && state.blocks[0].type !== 'intro') add(state.blocks.find(b => b.type === 'intro').key, 'The intro must come first.');

  for (const b of state.blocks) {
    if ('max_items' in b && !(Number.isInteger(b.max_items) && b.max_items >= LIMITS.minItems && b.max_items <= LIMITS.maxItems)) {
      add(b.key, `Choose from ${LIMITS.minItems} to ${LIMITS.maxItems} stories.`);
    }
    if (b.type === 'section') {
      const t = topicById(catalog, b.topic);
      if (!t) add(b.key, 'This section’s topic is no longer available.');
      else if (!t.sections.some(s => s.name === b.section)) add(b.key, `${t.name} no longer has this section.`);
    } else if (b.type === 'custom_section') {
      if (!textOk(b.title, LIMITS.name)) add(b.key, `The title must be 1 to ${LIMITS.name} characters, without < or >.`);
      if (!textOk(b.query, LIMITS.query)) add(b.key, `The description must be 1 to ${LIMITS.query} characters, without < or >.`);
    } else if (b.type === 'widget') {
      const w = widgetById(catalog, b.widget);
      if (!w) { add(b.key, 'This widget is no longer available.'); continue; }
      if (w.scope !== 'personal') {
        const allowed = w.topics.includes('*') ? (catalog.topics || []).map(t => t.id) : w.topics;
        if (!allowed.includes(b.topic)) add(b.key, 'Pick which newsletter this widget draws from.');
      }
      for (const f of w.config_fields || []) {
        const msg = fieldError(f, b.config?.[f.name]);
        if (msg) add(b.key, msg);
      }
    }
  }
  return errors;
}

/** A plain message if `value` is not valid for widget config field `f`, else null. */
export function fieldError(f, value) {
  switch (f.type) {
    case 'tickers': {
      const list = Array.isArray(value) ? value : [];
      if (!list.length) return `${f.label}: add at least one.`;
      if (list.length > (f.max || LIMITS.tickers)) return `${f.label}: up to ${f.max || LIMITS.tickers}.`;
      const bad = list.find(t => !TICKER_RE.test(t));
      return bad ? `${f.label}: “${bad}” is not a ticker symbol.` : null;
    }
    case 'int':
      return Number.isInteger(value) && value >= 1 && value <= (f.max ?? 99) ? null : `${f.label}: a whole number from 1 to ${f.max ?? 99}.`;
    case 'choice':
      return (f.options || []).includes(value) ? null : `${f.label}: pick one of the options.`;
    case 'bool':
      return typeof value === 'boolean' ? null : `${f.label}: on or off.`;
    default:
      return null;
  }
}

/** "shop, ry  aapl" -> ["SHOP", "RY", "AAPL"] (deduplicated, order kept). */
export function parseTickers(text) {
  const out = [];
  for (const t of String(text || '').toUpperCase().split(/[\s,;]+/)) if (t && !out.includes(t)) out.push(t);
  return out;
}

// ── Saving ───────────────────────────────────────────────────────────────────

/** The C3 JSON for the API: no keys, trimmed text, fields in the validator's order. */
export function serialize(state) {
  const blocks = state.blocks.map(b => {
    switch (b.type) {
      case 'section': {
        const d = { type: 'section', topic: b.topic, section: b.section, max_items: b.max_items };
        if (b.if_empty && b.if_empty !== 'note') d.if_empty = b.if_empty;
        return d;
      }
      case 'custom_section':
        return { type: 'custom_section', title: String(b.title).trim(), query: String(b.query).trim(), max_items: b.max_items };
      case 'widget': {
        const d = { type: 'widget', widget: b.widget };
        if (b.topic != null) d.topic = b.topic;
        d.config = clone(b.config || {});
        return d;
      }
      default:
        return { type: b.type };
    }
  });
  return { version: VERSION, name: String(state.name ?? '').trim(), accent_color: state.accent_color, blocks };
}

/** True when two states would save as the same definition. */
export function sameDefinition(a, b) {
  return JSON.stringify(serialize(a)) === JSON.stringify(serialize(b));
}

// ── Colours ──────────────────────────────────────────────────────────────────

/** "#ABC", "abc", "#aabbcc", " AABBCC " -> "#aabbcc"; anything else -> null. */
export function normalizeHex(input) {
  let s = String(input ?? '').trim().replace(/^#/, '').toLowerCase();
  if (/^[0-9a-f]{3}$/.test(s)) s = s.split('').map(ch => ch + ch).join('');
  return /^[0-9a-f]{6}$/.test(s) ? `#${s}` : null;
}

/** agents/render/palette.darken: the link colour on white cards. */
export function darken(hex, factor = 0.5) {
  const c = normalizeHex(hex) || DEFAULT_ACCENT;
  const parts = [1, 3, 5].map(i => Math.trunc(parseInt(c.slice(i, i + 2), 16) * factor));
  return '#' + parts.map(n => n.toString(16).padStart(2, '0')).join('');
}

/** WCAG relative luminance contrast between two #rrggbb colours (1 to 21). */
export function contrast(a, b) {
  const lum = hex => {
    const [r, g, bl] = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255)
      .map(v => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
    return 0.2126 * r + 0.7152 * g + 0.0722 * bl;
  };
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function clampInt(n, min, max) {
  const v = Math.round(Number(n));
  return Number.isFinite(v) ? Math.min(max, Math.max(min, v)) : min;
}

function clone(v) {
  return v === undefined ? undefined : JSON.parse(JSON.stringify(v));
}

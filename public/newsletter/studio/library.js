// studio/library.js — everything that can be added: each topic's sections, widgets, the intro and share strip,
// and a custom section described in the member's own words (refined by the existing /auth/sections/refine).
//
// The list is built once per catalog; renderLibrary() only updates which items are already in the newsletter
// and what the search hides, so focus and the half-typed custom section survive every change.

import * as M from './model.js';
import { h, icon, ICONS, mount } from './dom.js';

let ctx = null;
let builtFor = null;
let items = [];            // [{li, button, kind, block(), text, group}]
let groups = [];           // [{id, el, toggle, list, items}]
let widgetFilter = 'all';
const open = new Set();

const $ = id => document.getElementById(id);

export function initLibrary(c) {
  ctx = c;
  $('lib-search').addEventListener('input', applySearch);
  $('lib-search').addEventListener('keydown', e => {
    if (e.key === 'Escape' && e.target.value) { e.stopPropagation(); e.target.value = ''; applySearch(); }
  });
}

/** The block a library item would add; widget blocks depend on the topic filter for "any topic" widgets. */
function topicLabel(id) {
  const t = M.topicById(ctx.catalog, id);
  return ({ ai: 'AI', finance: 'Finance', gaming: 'Video games', boardgames: 'Board games', printing3d: '3D printing' })[id] || t?.name || id;
}

function widgetTopic(w) {
  if (w.scope === 'personal') return undefined;
  if (!w.topics.includes('*')) return w.topics.includes(widgetFilter) ? widgetFilter : w.topics[0];
  if (widgetFilter !== 'all') return widgetFilter;
  return ctx.state.blocks.find(b => b.topic)?.topic || ctx.catalog.topics[0]?.id;
}

// ── Building ─────────────────────────────────────────────────────────────────

function itemButton({ id, glyph, name, detail, badges = [], label }) {
  const plus = h('span', { class: 'lib-item__state', 'aria-hidden': 'true' }, icon(ICONS.plus));
  const button = h('button', {
    type: 'button', class: 'lib-item__btn', 'data-lib-id': id, 'aria-label': label,
  },
  h('span', { class: 'lib-item__glyph', 'aria-hidden': 'true', text: glyph }),
  h('span', { class: 'lib-item__text' },
    h('span', { class: 'lib-item__name', text: name }),
    detail ? h('span', { class: 'lib-item__detail', text: detail }) : null,
    badges.length ? h('span', { class: 'lib-item__badges' }, badges.map(b => h('span', { class: `badge badge--${b.kind}`, text: b.text }))) : null),
  plus);
  return button;
}

function addItem(list, group, spec, kind, block) {
  const button = itemButton(spec);
  const li = h('li', { class: 'lib-item' }, button);
  const item = { li, button, kind, block, text: spec.search.toLowerCase(), group, name: spec.name };
  button.addEventListener('click', () => add(item));
  button.dataset.dragKind = 'library';
  list.append(li);
  items.push(item);
  group.items.push(item);
  return item;
}

function makeGroup(id, title, { dot = null, meta = '', tag = '' } = {}) {
  const list = h('ul', { class: 'lib-group__list', id: `lib-g-${id}`, role: 'list' });
  const toggle = h('button', {
    type: 'button', class: 'lib-group__toggle', 'aria-expanded': 'false', 'aria-controls': list.id,
    onclick: () => { setOpen(group, !open.has(id)); },
  },
  dot ? h('span', { class: 'lib-group__dot', style: { '--dot': dot }, 'aria-hidden': 'true' }) : null,
  h('span', { class: 'lib-group__title' }, h('span', { class: 'lib-group__name', text: title }),
    meta ? h('span', { class: 'lib-group__meta', text: meta }) : null),
  tag ? h('span', { class: 'badge badge--muted', text: tag }) : null,
  h('span', { class: 'lib-group__chev', 'aria-hidden': 'true' }, icon(ICONS.chevron)));
  const el = h('section', { class: 'lib-group', 'data-group': id }, h('h3', { class: 'lib-group__head' }, toggle), list);
  const group = { id, el, toggle, list, items: [] };
  groups.push(group);
  return group;
}

function setOpen(group, isOpen) {
  if (isOpen) open.add(group.id); else open.delete(group.id);
  group.toggle.setAttribute('aria-expanded', String(isOpen));
  group.el.classList.toggle('is-open', isOpen);
}

function build() {
  items = [];
  groups = [];
  const body = $('lib-body');
  const sections = [];

  // Topics and their sections.
  sections.push(h('h3', { class: 'lib-heading', text: 'Sections by topic' }));
  for (const t of ctx.catalog.topics) {
    const g = makeGroup(`t-${t.id}`, t.name, { dot: t.accent_color, meta: `${t.sections.length} sections`, tag: t.public ? '' : 'Not launched' });
    for (const s of t.sections) {
      addItem(g.list, g, {
        id: `s|${t.id}|${s.name}`, glyph: s.icon, name: s.name, detail: s.definition,
        label: `Add ${s.name} from ${t.name}`, search: `${t.name} ${topicLabel(t.id)} ${s.name} ${s.definition}`,
      }, 'section', () => M.sectionBlock(t.id, s.name));
    }
    sections.push(g.el);
  }

  // Widgets, with a topic filter.
  const wTopics = ['all', ...ctx.catalog.topics.map(t => t.id).filter(id => ctx.catalog.widgets.some(w => w.topics.includes(id)))];
  const chips = h('div', { class: 'chips', role: 'group', 'aria-label': 'Show widgets for' },
    wTopics.map(id => h('button', {
      type: 'button', class: 'chip', 'data-filter': id, 'aria-pressed': String(id === widgetFilter),
      text: id === 'all' ? 'All' : topicLabel(id),
      onclick: () => { widgetFilter = id; renderLibrary(); },
    })));
  const wg = { id: 'widgets', items: [], list: h('ul', { class: 'lib-flat', role: 'list' }) };
  for (const w of ctx.catalog.widgets) {
    const badges = [];
    if (w.scope === 'personal') badges.push({ kind: 'personal', text: 'Personal' });
    if (w.premium) badges.push({ kind: 'premium', text: '◆ Premium' });
    badges.push({ kind: 'muted', text: w.topics.includes('*') ? 'Any topic' : w.topics.map(topicLabel).join(', ') });
    const it = addItem(wg.list, wg, {
      id: `w|${w.id}`, glyph: '◆', name: w.name, detail: w.description, badges,
      label: `Add the ${w.name} widget`, search: `widget ${w.name} ${w.description} ${w.topics.map(topicLabel).join(' ')}`,
    }, 'widget', () => M.widgetBlock(w, widgetTopic(w)));
    it.widget = w;
  }
  sections.push(h('section', { class: 'lib-widgets' },
    h('h3', { class: 'lib-heading', text: 'Widgets' }),
    h('p', { class: 'lib-help', text: 'Small blocks with their own data, like this week’s numbers.' }),
    chips, wg.list));

  // Intro and share strip.
  const lg = { id: 'layout', items: [], list: h('ul', { class: 'lib-flat', role: 'list' }) };
  addItem(lg.list, lg, {
    id: 'intro', glyph: '¶', name: 'Intro line', detail: 'One sentence at the top saying what this week’s email holds. Always first.',
    label: 'Add the intro line', search: 'intro introduction summary top',
  }, 'intro', () => ({ type: 'intro' }));
  addItem(lg.list, lg, {
    id: 'share', glyph: '↗', name: 'Share strip', detail: 'A one-line invitation to share the newsletter.',
    label: 'Add the share strip', search: 'share strip invite friends',
  }, 'share', () => ({ type: 'share' }));
  sections.push(h('section', {}, h('h3', { class: 'lib-heading', text: 'Layout' }), lg.list));

  sections.push(buildCustom());
  sections.push(h('p', { class: 'lib-empty', id: 'lib-no-match', hidden: true },
    h('span', { id: 'lib-no-match-text' }), ' ',
    h('button', { type: 'button', class: 'linkish', text: 'Describe it as a custom section', onclick: searchToCustom })));

  mount(body, ...sections);
  if (!open.size && groups[0]) setOpen(groups[0], true);
  else for (const g of groups) setOpen(g, open.has(g.id));
  builtFor = ctx.catalog;
}

// ── Custom section ───────────────────────────────────────────────────────────

let refined = '';

function buildCustom() {
  const input = h('input', {
    type: 'text', id: 'custom-input', maxlength: '200', autocomplete: 'off',
    placeholder: 'e.g. SpaceX launches, Montreal start-ups', 'aria-describedby': 'custom-help',
    onkeydown: e => { if (e.key === 'Enter') { e.preventDefault(); refine(); } },
    oninput: () => { $('custom-result').hidden = true; setCustomError(''); },
  });
  return h('section', { class: 'lib-custom', id: 'lib-custom' },
    h('h3', { class: 'lib-heading', text: 'Your own section' }),
    h('p', { class: 'lib-help', id: 'custom-help', text: 'Describe a topic in your own words. We suggest a precise version to search for; you can use it, edit it, or keep your words.' }),
    h('label', { class: 'sr-only', for: 'custom-input', text: 'Describe a custom section' }),
    input,
    h('div', { class: 'lib-custom__actions' },
      h('button', { type: 'button', class: 'btn btn--primary btn--sm', id: 'custom-refine', text: 'Suggest a precise topic', onclick: refine }),
      h('button', { type: 'button', class: 'btn btn--sm', id: 'custom-direct', text: 'Add as typed', onclick: () => addCustom(input.value, input.value) })),
    h('p', { class: 'lib-custom__loading', id: 'custom-loading', hidden: true, text: 'Finding a precise topic…' }),
    h('div', { class: 'lib-custom__result', id: 'custom-result', hidden: true },
      h('p', { class: 'lib-help', text: 'Suggested topic:' }),
      h('p', { class: 'lib-custom__suggestion', id: 'custom-suggestion' }),
      h('div', { class: 'lib-custom__actions' },
        h('button', { type: 'button', class: 'btn btn--primary btn--sm', text: 'Use this', onclick: () => addCustom(input.value, refined) }),
        h('button', { type: 'button', class: 'btn btn--sm', text: 'Edit', onclick: () => { input.value = refined; $('custom-result').hidden = true; input.focus(); } }),
        h('button', { type: 'button', class: 'btn btn--sm btn--quiet', text: 'Try again', onclick: refine }))),
    h('p', { class: 'lib-custom__error', id: 'custom-error', role: 'alert', hidden: true }));
}

function setCustomError(msg) {
  const el = $('custom-error');
  el.textContent = msg;
  el.hidden = !msg;
}

async function refine() {
  const input = $('custom-input');
  const raw = input.value.trim();
  if (!raw) { input.focus(); return; }
  if (/[<>]/.test(raw)) { setCustomError('Leave out < and >.'); return; }
  $('custom-refine').disabled = true;
  $('custom-loading').hidden = false;
  $('custom-result').hidden = true;
  setCustomError('');
  try {
    const data = await ctx.api.refineTopic(raw);
    refined = String(data.refined_topic || '').slice(0, M.LIMITS.query);
    $('custom-suggestion').textContent = refined;
    $('custom-result').hidden = false;
    $('custom-result').scrollIntoView({ block: 'nearest' });
    ctx.announce(`Suggested topic: ${refined}`);
  } catch (e) {
    setCustomError(e?.message || 'Something went wrong. Please try again.');
  } finally {
    $('custom-refine').disabled = false;
    $('custom-loading').hidden = true;
  }
}

function addCustom(raw, query) {
  const title = String(raw || '').trim().replace(/\s+/g, ' ');
  const q = String(query || '').trim();
  if (!title) { $('custom-input').focus(); return; }
  if (/[<>]/.test(title + q)) { setCustomError('Leave out < and >.'); return; }
  const block = M.customBlock(title.slice(0, M.LIMITS.name), q.slice(0, M.LIMITS.query));
  if (doAdd(block, `custom section “${block.title}”`)) {
    $('custom-input').value = '';
    $('custom-result').hidden = true;
    setCustomError('');
  }
}

function searchToCustom() {
  const q = $('lib-search').value.trim();
  $('lib-search').value = '';
  applySearch();
  $('custom-input').value = q;
  $('custom-input').focus();
  $('lib-custom').scrollIntoView({ block: 'nearest', behavior: 'smooth' });
}

// ── Adding ───────────────────────────────────────────────────────────────────

function describe(item) {
  if (item.kind === 'section') {
    const [, tid] = item.button.dataset.libId.split('|');
    return `${item.name} (${topicLabel(tid)})`;
  }
  return item.name;
}

function add(item) {
  if (ctx.readOnly) return;
  doAdd(item.block(), describe(item));
}

/** Add `block` at `index` (default: the end). Returns true when added. Shared with drag and drop. */
export function doAdd(block, what, index) {
  if (ctx.readOnly) return false;
  const r = M.addBlock(ctx.state, block, index);
  if (r.error) {
    ctx.showLimit?.(r.error);
    ctx.announce(r.error);
    // On phones the canvas (and its limit message) is behind the library sheet.
    if ($('studio').dataset.libraryOpen === 'true') ctx.toast(r.error, { tone: 'error', ms: 4000 });
    return false;
  }
  ctx.set(r.state, { focus: null });
  ctx.showLimit?.('');
  ctx.flashBlock?.(r.key);
  const msg = `Added ${what}.`;
  ctx.announce(msg);
  if ($('studio').dataset.libraryOpen === 'true') ctx.toast(msg, { ms: 2500 });
  return true;
}

/** For drag and drop: the block a library button stands for. */
export function blockForButton(button) {
  const item = items.find(i => i.button === button);
  return item ? { block: item.block(), what: describe(item) } : null;
}

// ── Updating ─────────────────────────────────────────────────────────────────

function applySearch() {
  const q = $('lib-search').value.trim().toLowerCase();
  const words = q.split(/\s+/).filter(Boolean);
  let shown = 0;
  for (const it of items) {
    const byFilter = !it.widget || widgetFilter === 'all' || it.widget.topics.includes(widgetFilter) || it.widget.topics.includes('*');
    const bySearch = words.every(w => it.text.includes(w));
    it.li.hidden = !(byFilter && bySearch);
    if (!it.li.hidden) shown += 1;
  }
  for (const g of groups) {
    const any = g.items.some(i => !i.li.hidden);
    g.el.hidden = !any;
    if (words.length) {
      g.toggle.setAttribute('aria-expanded', String(any));
      g.el.classList.toggle('is-open', any);
    } else {
      setOpen(g, open.has(g.id));
    }
  }
  const none = $('lib-no-match');
  none.hidden = !(words.length && shown === 0);
  if (!none.hidden) $('lib-no-match-text').textContent = `Nothing matches “${$('lib-search').value.trim()}”.`;
  $('lib-body').classList.toggle('is-searching', words.length > 0);
}

export function renderLibrary() {
  if (!ctx.catalog?.topics?.length) return;
  if (builtFor !== ctx.catalog) build();
  for (const chip of document.querySelectorAll('.chip')) chip.setAttribute('aria-pressed', String(chip.dataset.filter === widgetFilter));
  for (const it of items) {
    const block = it.block();
    const added = it.kind === 'intro' || it.kind === 'share'
      ? ctx.state.blocks.some(b => b.type === it.kind)
      : M.contains(ctx.state, block);
    const blocked = !added && M.cannotAdd(ctx.state, block);
    it.button.classList.toggle('is-added', added);
    it.button.setAttribute('aria-disabled', String(added || ctx.readOnly));
    it.button.title = added ? 'Already in your newsletter' : (blocked || '');
    it.button.disabled = ctx.readOnly;
    const state = it.button.querySelector('.lib-item__state');
    mount(state, icon(added ? ICONS.check : ICONS.plus));
    it.li.classList.toggle('is-blocked', !!blocked);
  }
  for (const el of $('lib-body').querySelectorAll('input, .lib-custom button')) el.disabled = ctx.readOnly;
  $('lib-search').disabled = false;
  applySearch();
}

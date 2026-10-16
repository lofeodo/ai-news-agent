// studio/canvas.js — the newsletter being built: its name and colour, and the ordered blocks with their settings.
//
// Moving a block: drag its grip (mouse, touch or pen), its Move up / Move down buttons, or focus it and press
// Alt+↑ / Alt+↓. Removing one offers Undo. Typing in a text field updates the state without rebuilding the list
// (so the caret stays put); everything else re-renders and puts focus back on the same control.

import * as M from './model.js';
import { h, icon, ICONS, mount, debounce } from './dom.js';
import { startDrag } from './dnd.js';
import { blockForButton, doAdd } from './library.js';

let ctx = null;
let limitTimer = null;
const $ = id => document.getElementById(id);

const TOPIC_SHORT = { ai: 'AI', finance: 'Finance', gaming: 'Video games', boardgames: 'Board games', printing3d: '3D printing' };

export function initCanvas(c) {
  ctx = c;
  ctx.showLimit = showLimit;
  ctx.flashBlock = flashBlock;
  ctx.errors = new Map();        // block key (or '' for the newsletter) -> [messages] from the last save
  ctx.renderCounts = renderCounts;
  ctx.showNameError = showNameError;

  const name = $('nl-name');
  name.addEventListener('input', () => {
    // A name is one line: drop pasted line breaks.
    if (/[\r\n]/.test(name.value)) name.value = name.value.replace(/[\r\n]+/g, ' ');
    ctx.set(M.rename(ctx.state, name.value), { render: false });
    showNameError(false);
    fitName();
  });
  window.addEventListener('resize', fitName);
  name.addEventListener('blur', () => showNameError(true));
  name.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); name.blur(); } });

  $('blocks').addEventListener('keydown', onListKey);
  wireLibraryDrag();
}

// ── Helpers ──────────────────────────────────────────────────────────────────

export function blockTitle(b) {
  switch (b.type) {
    case 'section': return b.section;
    case 'custom_section': return b.title || 'Custom section';
    case 'widget': return M.widgetById(ctx.catalog, b.widget)?.name || b.widget;
    case 'intro': return 'Intro line';
    case 'share': return 'Share strip';
    default: return b.type;
  }
}

function topicShort(id) {
  return TOPIC_SHORT[id] || M.topicById(ctx.catalog, id)?.name || id;
}

/** "Crypto & Fintech, Finance section, 3 stories" — for screen readers and toasts. */
export function blockSummary(b) {
  switch (b.type) {
    case 'section': return `${b.section}, ${topicShort(b.topic)} section`;
    case 'custom_section': return `${blockTitle(b)}, custom section`;
    case 'widget': return `${blockTitle(b)} widget`;
    default: return blockTitle(b);
  }
}

/** The name field grows to show the whole name (it is a textarea so long names wrap). */
function fitName() {
  const name = $('nl-name');
  name.style.height = 'auto';
  name.style.height = `${name.scrollHeight}px`;
}

function showLimit(msg) {
  const el = $('limit-msg');
  clearTimeout(limitTimer);
  el.textContent = msg || '';
  el.hidden = !msg;
  if (msg) limitTimer = setTimeout(() => { el.hidden = true; }, 7000);
}

function showNameError(touched) {
  const name = $('nl-name');
  const errs = M.validate({ ...ctx.state, blocks: [{ type: 'intro' }] }, ctx.catalog).filter(e => e.key === null && /name/i.test(e.message));
  const msg = errs[0]?.message || '';
  name.setAttribute('aria-invalid', String(!!msg && touched));
  let el = $('name-error');
  if (!el) {
    el = h('p', { class: 'field-error', id: 'name-error', role: 'alert' });
    $('masthead').querySelector('.masthead__rule').after(el);
    name.setAttribute('aria-describedby', 'name-error');
  }
  el.textContent = touched ? msg : '';
  el.hidden = !(touched && msg);
}

export function focusBlock(key, ctl = null) {
  requestAnimationFrame(() => {
    const li = $('blocks').querySelector(`li[data-key="${CSS.escape(key)}"]`);
    if (!li) return;
    const target = (ctl && li.querySelector(`[data-ctl="${ctl}"]:not(:disabled)`)) || li;
    target.focus({ preventScroll: true });
    li.scrollIntoView({ block: 'nearest' });
  });
}

function flashBlock(key) {
  requestAnimationFrame(() => {
    const li = $('blocks').querySelector(`li[data-key="${CSS.escape(key)}"]`);
    if (!li) return;
    li.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    li.classList.remove('is-new');
    void li.offsetWidth;
    li.classList.add('is-new');
  });
}

// ── Changes ──────────────────────────────────────────────────────────────────

function move(key, delta) {
  const from = M.indexOf(ctx.state, key);
  const next = M.moveBy(ctx.state, key, delta);
  if (next === ctx.state) return false;
  ctx.set(next);
  const to = M.indexOf(next, key);
  ctx.announce(`Moved ${blockTitle(next.blocks[to])} to position ${to + 1} of ${next.blocks.length}.`);
  return from !== to;
}

function remove(key) {
  const i = M.indexOf(ctx.state, key);
  const { state, removed } = M.removeBlock(ctx.state, key);
  if (!removed) return;
  ctx.set(state);
  const title = blockTitle(removed.block);
  ctx.toast(`Removed ${title}.`, {
    action: {
      label: 'Undo',
      run: () => {
        ctx.set(M.restoreBlock(ctx.state, removed));
        ctx.announce(`Restored ${title}.`);
        focusBlock(removed.block.key);
      },
    },
  });
  ctx.announce(`Removed ${title}. Undo is available.`);
  const next = state.blocks[Math.min(i, state.blocks.length - 1)];
  if (next) focusBlock(next.key); else $('blocks-title').focus();
}

function onListKey(e) {
  if (!e.altKey || (e.key !== 'ArrowUp' && e.key !== 'ArrowDown')) return;
  const li = e.target.closest('li[data-key]');
  if (!li || ctx.readOnly) return;
  e.preventDefault();
  const key = li.dataset.key;
  const ctl = e.target === li ? null : e.target.dataset.ctl;
  move(key, e.key === 'ArrowUp' ? -1 : 1);
  focusBlock(key, ctl);
}

// ── Drag and drop ────────────────────────────────────────────────────────────

function wireBlockDrag(grip, li) {
  grip.addEventListener('pointerdown', e => {
    if (ctx.readOnly) return;
    startDrag(e, {
      source: li, skip: true, threshold: 0,
      list: () => $('blocks'), zone: () => $('canvas-scroll'), scroller: $('canvas-scroll'),
      onDrop: index => {
        // `index` counts the list without the dragged block, which is exactly moveBlock's target position.
        const key = li.dataset.key;
        const next = M.moveBlock(ctx.state, key, index);
        if (next !== ctx.state) {
          ctx.set(next);
          const at = M.indexOf(next, key);
          ctx.announce(`Moved ${blockTitle(next.blocks[at])} to position ${at + 1}.`);
        }
        focusBlock(key);
      },
    });
  });
}

/** Library items can be dragged onto the canvas with a mouse or pen (a tap or click adds them at the end). */
function wireLibraryDrag() {
  $('lib-body').addEventListener('pointerdown', e => {
    const button = e.target.closest('.lib-item__btn');
    if (!button || ctx.readOnly || e.pointerType === 'touch' || button.classList.contains('is-added')) return;
    const wrapped = { currentTarget: button, button: e.button, pointerId: e.pointerId, clientX: e.clientX, clientY: e.clientY, preventDefault: () => e.preventDefault() };
    startDrag(wrapped, {
      source: button, skip: false,
      list: () => $('blocks'), zone: () => $('canvas'), scroller: $('canvas-scroll'),
      onDrop: index => {
        const spec = blockForButton(button);
        if (spec) doAdd(spec.block, spec.what, index);
      },
    });
  });
}

// ── Rendering ────────────────────────────────────────────────────────────────

function iconButton(label, path, onclick, extra = {}) {
  return h('button', { type: 'button', class: 'icon-btn', 'aria-label': label, title: label, onclick, ...extra }, icon(path));
}

function stepper(label, value, min, max, onChange, ctl) {
  const id = `${ctl}-${Math.random().toString(36).slice(2, 7)}`;
  return h('div', { class: 'stepper', role: 'group', 'aria-labelledby': id },
    h('span', { class: 'stepper__label', id, text: label }),
    iconButton(`Fewer: ${label}`, ICONS.minus, () => onChange(value - 1), { disabled: value <= min || ctx.readOnly, 'data-ctl': `${ctl}-dec` }),
    h('output', { class: 'stepper__value', 'aria-live': 'polite', text: String(value) }),
    iconButton(`More: ${label}`, ICONS.plus, () => onChange(value + 1), { disabled: value >= max || ctx.readOnly, 'data-ctl': `${ctl}-inc` }));
}

function textField(label, value, max, onCommit, ctl, { multiline = false, help = '' } = {}) {
  const id = `f-${ctl}-${Math.random().toString(36).slice(2, 7)}`;
  const input = h(multiline ? 'textarea' : 'input', {
    id, class: 'field-input', maxlength: String(max), 'data-ctl': ctl, readOnly: ctx.readOnly,
    rows: multiline ? '2' : undefined, type: multiline ? undefined : 'text', autocomplete: 'off',
    'aria-describedby': help ? `${id}-help` : undefined,
  });
  input.value = value ?? '';
  const commit = debounce(() => onCommit(input.value), 250);
  input.addEventListener('input', () => commit());
  input.addEventListener('blur', () => { commit.flush(); });
  if (!multiline) input.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); commit.flush(); } });
  return h('div', { class: 'field-row' },
    h('label', { class: 'field-label', for: id, text: label }),
    input,
    help ? h('span', { class: 'field-help', id: `${id}-help`, text: help }) : null);
}

function widgetFields(b, w) {
  const out = [];
  if (w.scope !== 'personal' && w.topics.includes('*')) {
    const id = `wt-${b.key}`;
    out.push(h('div', { class: 'field-row field-row--inline' },
      h('label', { class: 'field-label', for: id, text: 'From' }),
      h('select', {
        id, class: 'field-input', 'data-ctl': 'topic', disabled: ctx.readOnly,
        onchange: e => ctx.set(M.setWidgetTopic(ctx.state, b.key, e.target.value), { focus: null }),
      }, ctx.catalog.topics.map(t => h('option', { value: t.id, selected: t.id === b.topic, text: t.name })))));
  }
  for (const f of w.config_fields || []) {
    const v = b.config?.[f.name];
    const set = val => ctx.set(M.setConfig(ctx.state, b.key, f.name, val));
    if (f.type === 'int') {
      out.push(stepper(f.label, Number.isInteger(v) ? v : f.default, 1, f.max ?? 99, n => set(n), `cfg-${f.name}`));
    } else if (f.type === 'choice') {
      const id = `c-${b.key}-${f.name}`;
      out.push(h('div', { class: 'field-row field-row--inline' },
        h('label', { class: 'field-label', for: id, text: f.label }),
        h('select', { id, class: 'field-input', 'data-ctl': `cfg-${f.name}`, disabled: ctx.readOnly, onchange: e => set(e.target.value) },
          (f.options || []).map(o => h('option', { value: o, selected: o === v, text: o })))));
    } else if (f.type === 'bool') {
      const id = `b-${b.key}-${f.name}`;
      out.push(h('div', { class: 'field-row field-row--check' },
        h('input', { type: 'checkbox', id, 'data-ctl': `cfg-${f.name}`, checked: !!v, disabled: ctx.readOnly, onchange: e => set(e.target.checked) }),
        h('label', { for: id, text: f.label })));
    } else if (f.type === 'tickers') {
      out.push(textField(f.label, (Array.isArray(v) ? v : []).join(', '), 120,
        text => {
          ctx.set(M.setConfig(ctx.state, b.key, f.name, M.parseTickers(text)), { render: false });
          refreshErrors(b.key);
        }, `cfg-${f.name}`,
        { help: `Up to ${f.max || M.LIMITS.tickers} symbols, separated by commas.` }));
    }
  }
  return out;
}

function blockCard(b, i, numbers) {
  const n = ctx.state.blocks.length;
  const bounds = M.moveBounds(ctx.state, b.key);
  const errors = [...(ctx.liveErrors.get(b.key) || []), ...(ctx.errors.get(b.key) || [])];
  const topic = b.topic ? M.topicById(ctx.catalog, b.topic) : null;
  const rail = b.type === 'section' || (b.type === 'widget' && topic) ? (topic?.accent_color || 'var(--nl)') : 'var(--nl)';
  const title = blockTitle(b);

  let mark = '';
  if (b.type === 'section' || b.type === 'custom_section') mark = numbers.get(b.key);
  else mark = { widget: '◆', intro: '¶', share: '↗' }[b.type] || '';

  const meta = [];
  if (b.type === 'section') meta.push(h('span', { class: 'tag', style: { '--dot': topic?.accent_color || '#888' } }, topicShort(b.topic)));
  if (b.type === 'custom_section') meta.push(h('span', { class: 'tag tag--custom' }, 'Custom'));
  if (b.type === 'widget') {
    const w = M.widgetById(ctx.catalog, b.widget);
    meta.push(h('span', { class: 'tag tag--widget' }, w?.scope === 'personal' ? 'Personal widget' : 'Widget'));
    if (topic && !w?.topics.includes('*')) meta.push(h('span', { class: 'tag', style: { '--dot': topic.accent_color } }, topicShort(b.topic)));
  }

  const body = [];
  if (b.type === 'section') {
    body.push(stepper('Stories', b.max_items, M.LIMITS.minItems, M.LIMITS.maxItems, v => ctx.set(M.setItems(ctx.state, b.key, v)), 'items'));
    const id = `ie-${b.key}`;
    body.push(h('div', { class: 'field-row field-row--check' },
      h('input', {
        type: 'checkbox', id, 'data-ctl': 'if-empty', checked: b.if_empty === 'hide', disabled: ctx.readOnly,
        onchange: e => ctx.set(M.setIfEmpty(ctx.state, b.key, e.target.checked ? 'hide' : 'note')),
      }),
      h('label', { for: id, text: 'Leave out when there is no news' })));
  } else if (b.type === 'custom_section') {
    body.push(stepper('Stories', b.max_items, M.LIMITS.minItems, M.LIMITS.maxItems, v => ctx.set(M.setItems(ctx.state, b.key, v)), 'items'));
    body.push(textField('Title', b.title, M.LIMITS.name, v => updateCustom(b.key, { title: v }), 'title'));
    body.push(textField('What to look for', b.query, M.LIMITS.query, v => updateCustom(b.key, { query: v }), 'query', { multiline: true }));
  } else if (b.type === 'widget') {
    const w = M.widgetById(ctx.catalog, b.widget);
    if (w) {
      body.push(h('p', { class: 'block__desc', text: w.description }));
      body.push(...widgetFields(b, w));
    }
  } else if (b.type === 'intro') {
    body.push(h('p', { class: 'block__desc', text: 'One sentence saying what this week’s email holds. Always first.' }));
  } else if (b.type === 'share') {
    body.push(h('p', { class: 'block__desc', text: 'A one-line invitation to share the newsletter.' }));
  }

  const grip = h('div', { class: 'block__grip', 'aria-hidden': 'true', title: b.type === 'intro' ? 'The intro stays first' : 'Drag to move' }, icon(ICONS.grip));
  const li = h('li', {
    class: `block block--${b.type}${errors.length ? ' has-error' : ''}`,
    'data-key': b.key, tabindex: '0', style: { '--rail': rail },
    'aria-label': `${i + 1} of ${n}: ${blockSummary(b)}`,
  },
  grip,
  h('div', { class: 'block__main' },
    h('div', { class: 'block__top' },
      h('span', { class: 'block__mark', 'aria-hidden': 'true', text: mark }),
      h('h3', { class: 'block__name', text: title }),
      meta),
    body.length ? h('div', { class: 'block__body' }, body) : null,
    errors.length ? h('ul', { class: 'block__errors', role: 'alert' }, errors.map(m => h('li', { text: m }))) : null),
  h('div', { class: 'block__actions' },
    iconButton(`Move ${title} up`, ICONS.up, () => { move(b.key, -1); focusBlock(b.key, 'up'); },
      { disabled: ctx.readOnly || i <= bounds.min, 'data-ctl': 'up' }),
    iconButton(`Move ${title} down`, ICONS.down, () => { move(b.key, 1); focusBlock(b.key, 'down'); },
      { disabled: ctx.readOnly || i >= bounds.max, 'data-ctl': 'down' }),
    iconButton(`Remove ${title}`, ICONS.close, () => remove(b.key), { disabled: ctx.readOnly, 'data-ctl': 'remove', class: 'icon-btn icon-btn--remove' })));
  if (b.type !== 'intro' && !ctx.readOnly) wireBlockDrag(grip, li);
  if (b.type === 'intro' || ctx.readOnly) grip.classList.add('is-fixed');
  return li;
}

function updateCustom(key, patch) {
  const b = ctx.state.blocks.find(x => x.key === key);
  if (!b) return;
  const title = (patch.title ?? b.title);
  const query = (patch.query ?? b.query);
  ctx.set(M.setCustom(ctx.state, key, title, query), { render: false });
  refreshErrors(key);
}

/** Re-show one block's error list after a text edit, without rebuilding the block (keeps the caret). */
function refreshErrors(key) {
  computeLiveErrors();
  const li = $('blocks').querySelector(`li[data-key="${CSS.escape(key)}"]`);
  if (!li) return;
  const errors = [...(ctx.liveErrors.get(key) || []), ...(ctx.errors.get(key) || [])];
  li.querySelector('.block__errors')?.remove();
  li.classList.toggle('has-error', errors.length > 0);
  if (errors.length) li.querySelector('.block__main').append(h('ul', { class: 'block__errors', role: 'alert' }, errors.map(m => h('li', { text: m }))));
}

/** Problems the model can see now (a bad ticker, an empty custom title): shown on the block straight away. */
function computeLiveErrors() {
  ctx.liveErrors = new Map();
  for (const e of M.validate(ctx.state, ctx.catalog)) {
    if (!e.key) continue;
    if (!ctx.liveErrors.has(e.key)) ctx.liveErrors.set(e.key, []);
    ctx.liveErrors.get(e.key).push(e.message);
  }
}

export function renderCounts() {
  const c = M.counts(ctx.state);
  const L = M.LIMITS;
  $('counts').textContent = `${c.section} of ${L.sections} sections · ${c.widget} of ${L.widgets} widgets · ${c.custom_section} of ${L.custom} custom · ${c.blocks} of ${L.blocks} blocks`;
}

function renderColour() {
  const el = $('colour-picker');
  const current = ctx.state.accent_color;
  const custom = !M.ACCENT_PRESETS.includes(current);
  const hexId = 'accent-hex';
  const hex = h('input', {
    type: 'text', id: hexId, class: 'hex-input', maxlength: '7', autocomplete: 'off', spellcheck: 'false',
    'data-ctl': 'hex', readOnly: ctx.readOnly, 'aria-describedby': 'accent-note',
  });
  hex.value = current;
  const applyHex = () => {
    const c = M.normalizeHex(hex.value);
    if (!c) { $('accent-note').textContent = 'Use a colour like #7fd1a0.'; hex.setAttribute('aria-invalid', 'true'); return; }
    hex.removeAttribute('aria-invalid');
    if (c !== ctx.state.accent_color) ctx.set(M.recolor(ctx.state, c));
    restoreFocus('hex');
  };
  hex.addEventListener('change', applyHex);
  hex.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); applyHex(); } });

  const swatches = M.ACCENT_PRESETS.map((c, i) => h('button', {
    type: 'button', class: 'swatch', role: 'radio', 'aria-checked': String(c === current),
    'aria-label': `Accent ${c}`, title: c, style: { '--sw': c }, disabled: ctx.readOnly,
    tabindex: (c === current || (custom && i === 0)) ? '0' : '-1', 'data-ctl': `sw-${i}`,
    onclick: () => { ctx.set(M.recolor(ctx.state, c)); restoreFocus(`sw-${i}`); },
    onkeydown: e => {
      const d = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
      if (!d) return;
      e.preventDefault();
      const j = (i + d + M.ACCENT_PRESETS.length) % M.ACCENT_PRESETS.length;
      ctx.set(M.recolor(ctx.state, M.ACCENT_PRESETS[j]));
      restoreFocus(`sw-${j}`);
    },
  }));
  const low = M.contrast(current, '#080808') < 3;
  mount(el,
    h('div', { class: 'colour' },
      h('span', { class: 'colour__label', id: 'accent-label', text: 'Colour' }),
      h('div', { class: 'swatches', role: 'radiogroup', 'aria-labelledby': 'accent-label' }, swatches),
      h('label', { class: 'sr-only', for: hexId, text: 'Accent colour as a hex code' }),
      hex),
    h('p', { class: 'colour__note', id: 'accent-note', 'aria-live': 'polite',
      text: low ? 'This colour is hard to read on the email’s dark header; a lighter one works better.' : '' }));
}

function restoreFocus(ctl) {
  requestAnimationFrame(() => document.querySelector(`#colour-picker [data-ctl="${ctl}"]`)?.focus());
}

export function renderCanvas() {
  const name = $('nl-name');
  if (document.activeElement !== name) name.value = ctx.state.name;
  name.readOnly = ctx.readOnly;
  name.placeholder = 'Name your newsletter';
  requestAnimationFrame(fitName);
  renderColour();
  renderCounts();
  computeLiveErrors();

  // Remember focus inside the list to put it back after rebuilding.
  const active = document.activeElement;
  const activeLi = active?.closest?.('#blocks li[data-key]');
  const keep = activeLi ? { key: activeLi.dataset.key, ctl: active === activeLi ? null : active.dataset.ctl } : null;

  const numbers = new Map();
  let k = 0;
  for (const b of ctx.state.blocks) if (b.type === 'section' || b.type === 'custom_section') numbers.set(b.key, String(++k).padStart(2, '0'));
  mount($('blocks'), ctx.state.blocks.map((b, i) => blockCard(b, i, numbers)));
  if (!ctx.state.blocks.length && ctx.started) {
    $('blocks').append(h('li', { class: 'blocks__empty' },
      h('p', { text: 'Nothing here yet. Add sections, widgets or a custom section from the library.' })));
  }
  if (keep && ctx.state.blocks.some(b => b.key === keep.key) && !keep.ctl?.startsWith('title') && keep.ctl !== 'query') {
    const li = $('blocks').querySelector(`li[data-key="${CSS.escape(keep.key)}"]`);
    const t = (keep.ctl && li?.querySelector(`[data-ctl="${keep.ctl}"]`)) || li;
    if (t && !t.disabled) t.focus({ preventScroll: true });
    else li?.focus({ preventScroll: true });
  }
  $('add-open').disabled = ctx.readOnly;
}

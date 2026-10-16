// studio/manage.js — the member's saved newsletters: switching between them (up to the API's limit), saving with
// the server's validation errors shown on the blocks, the unsaved-changes guard, the "send it to me every Monday"
// toggle, and deleting. Fills the canvas bar (top) and foot (bottom).

import * as M from './model.js';
import { ApiError } from './api.js';
import { h, mount } from './dom.js';
import { requestPreview } from './preview.js';

let ctx = null;
let saving = false;
let busy = false;
let message = null;     // {text, tone} under the bar until the next save or until nothing is wrong
const $ = id => document.getElementById(id);

export function initManage(c) {
  ctx = c;
  window.addEventListener('beforeunload', e => {
    if (!ctx.readOnly && ctx.isDirty()) { e.preventDefault(); e.returnValue = ''; }
  });
  document.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's' && !ctx.readOnly) { e.preventDefault(); save(); }
  });
}

// ── Confirm dialog ───────────────────────────────────────────────────────────

/** A modal question; resolves true when the member confirms. Focus returns to where it was. */
export function confirmDialog({ title, body, confirm, cancel = 'Cancel', danger = false }) {
  return new Promise(resolve => {
    const back = document.activeElement;
    const dlg = h('dialog', { class: 'dlg', 'aria-labelledby': 'dlg-title' },
      h('h2', { class: 'dlg__title', id: 'dlg-title', text: title }),
      body ? h('p', { class: 'dlg__body', text: body }) : null,
      h('div', { class: 'dlg__actions' },
        h('button', { type: 'button', class: 'btn btn--quiet', value: 'cancel', text: cancel, onclick: () => dlg.close('cancel') }),
        h('button', { type: 'button', class: `btn ${danger ? 'btn--danger' : 'btn--primary'}`, text: confirm, onclick: () => dlg.close('ok') })));
    dlg.addEventListener('close', () => {
      dlg.remove();
      back?.focus?.();
      resolve(dlg.returnValue === 'ok');
    });
    document.body.append(dlg);
    dlg.showModal();
    dlg.querySelector('.btn--quiet').focus();
  });
}

// ── Switching ────────────────────────────────────────────────────────────────

async function okToLeave() {
  if (!ctx.isDirty()) return true;
  const name = ctx.state.name.trim() || 'this newsletter';
  return confirmDialog({
    title: 'Discard your changes?',
    body: `Your changes to “${name}” are not saved yet.`,
    confirm: 'Discard changes', cancel: 'Keep editing', danger: true,
  });
}

async function switchTo(value) {
  if (!(await okToLeave())) { renderBar(); return; }
  ctx.errors.clear();
  if (value === '__new') {
    ctx.saved = null;
    ctx.state = M.emptyState();
    ctx.started = false;
    ctx.render();
    requestPreview({ now: true });
    ctx.announce('New newsletter. Pick a starting point.');
    return;
  }
  const item = ctx.newsletters.find(n => n.id === value);
  if (!item) return;
  ctx.state = M.fromDefinition(item.definition);
  ctx.saved = { id: item.id, state: ctx.state };
  ctx.started = true;
  ctx.render();
  requestPreview({ now: true });
  ctx.announce(`Opened ${item.definition.name}.`);
}

// ── Saving ───────────────────────────────────────────────────────────────────

/** Server errors look like "block 3 (section): message" (agents/newsletter_def.py); put each on its block. */
export function mapServerErrors(errors, state) {
  const out = new Map();
  for (const raw of errors || []) {
    const m = /^block (\d+)(?: \([^)]*\))?: (.*)$/.exec(String(raw));
    const block = m ? state.blocks[Number(m[1]) - 1] : null;
    const key = block ? block.key : '';
    const msg = m ? m[2] : String(raw);
    if (!out.has(key)) out.set(key, []);
    out.get(key).push(msg.charAt(0).toUpperCase() + msg.slice(1));
  }
  return out;
}

function setMessage(text, tone = 'error') {
  message = text ? { text, tone } : null;
  const el = $('save-msg');
  if (!el) return;
  el.textContent = text || '';
  el.dataset.tone = tone;
  el.hidden = !text;
}

export async function save() {
  if (ctx.readOnly || saving || !ctx.started) return;
  ctx.errors.clear();
  const problems = M.validate(ctx.state, ctx.catalog);
  if (problems.length) {
    const general = problems.filter(p => !p.key).map(p => p.message);
    ctx.render();
    setMessage(general[0] || 'Some blocks need a fix before saving. They are marked in red.');
    ctx.showNameError?.(true);
    focusFirstProblem(problems);
    ctx.announce(general[0] || 'Some blocks need a fix before saving.');
    return;
  }
  saving = true;
  renderBar();
  const snapshot = ctx.state;
  const isNew = !ctx.saved;
  try {
    const res = await ctx.api.saveNewsletter({ id: ctx.saved?.id, definition: M.serialize(snapshot) });
    const prev = ctx.newsletters.find(n => n.id === res.id);
    const item = { id: res.id, definition: res.definition, subscribed: prev?.subscribed ?? false, updated_at: new Date().toISOString() };
    ctx.newsletters = [item, ...ctx.newsletters.filter(n => n.id !== res.id)];
    ctx.saved = { id: res.id, state: snapshot };
    setMessage('');
    ctx.toast(isNew ? 'Saved. You can have it sent to you every Monday below.' : 'Saved.');
    ctx.announce('Saved.');
  } catch (e) {
    if (e instanceof ApiError && e.errors?.length) {
      ctx.errors = mapServerErrors(e.errors, snapshot);
      const general = ctx.errors.get('') || [];
      ctx.render();
      setMessage(general[0] || 'Some blocks need a fix before saving. They are marked in red.');
      focusFirstProblem([...ctx.errors.keys()].map(key => ({ key: key || null })));
    } else {
      setMessage(e?.message || 'Saving failed. Try again.');
    }
    ctx.announce(`Not saved: ${$('save-msg')?.textContent || ''}`);
  } finally {
    saving = false;
    renderBar();
    renderFoot();
  }
}

function focusFirstProblem(problems) {
  const first = problems.find(p => p.key);
  if (first) {
    requestAnimationFrame(() => {
      const li = document.querySelector(`#blocks li[data-key="${CSS.escape(first.key)}"]`);
      li?.scrollIntoView({ block: 'center' });
      li?.focus({ preventScroll: true });
    });
  } else if (problems.some(p => !p.key)) {
    $('nl-name').focus();
  }
}

// ── Subscribe and delete ─────────────────────────────────────────────────────

async function toggleSubscribe(on) {
  const id = ctx.saved?.id;
  if (!id || busy) return;
  busy = true;
  renderFoot();
  try {
    const res = await ctx.api.setSubscribed(id, on);
    const item = ctx.newsletters.find(n => n.id === id);
    if (item) item.subscribed = res.subscribed;
    ctx.announce(res.subscribed ? `It will be sent to ${ctx.email} every Monday morning.` : 'It will not be sent.');
  } catch (e) {
    ctx.toast(e?.message || 'That did not work. Try again.', { tone: 'error' });
  } finally {
    busy = false;
    renderFoot();
    $('sub-toggle')?.focus();
  }
}

async function remove() {
  const id = ctx.saved?.id;
  if (!id) return;
  const name = ctx.saved.state.name || 'this newsletter';
  const ok = await confirmDialog({
    title: `Delete “${name}”?`,
    body: 'It will no longer be sent to you, and this cannot be undone.',
    confirm: 'Delete newsletter', danger: true,
  });
  if (!ok) return;
  busy = true;
  renderFoot();
  try {
    await ctx.api.deleteNewsletter(id);
    ctx.newsletters = ctx.newsletters.filter(n => n.id !== id);
    ctx.saved = null;
    ctx.errors.clear();
    const next = ctx.newsletters[0];
    if (next) {
      ctx.state = M.fromDefinition(next.definition);
      ctx.saved = { id: next.id, state: ctx.state };
      ctx.started = true;
    } else {
      ctx.state = M.emptyState();
      ctx.started = false;
    }
    ctx.render();
    requestPreview({ now: true });
    ctx.toast(`Deleted “${name}”.`);
    ctx.announce(`Deleted ${name}.`);
    $('blocks-title').focus();
  } catch (e) {
    ctx.toast(e?.message || 'Deleting failed. Try again.', { tone: 'error' });
  } finally {
    busy = false;
    renderFoot();
  }
}

// ── Rendering ────────────────────────────────────────────────────────────────

function renderBar() {
  const bar = $('canvas-bar');
  if (ctx.readOnly) return;     // the read-only sample keeps its own bar (app.js)
  const dirty = ctx.isDirty();
  const atLimit = ctx.newsletters.length >= ctx.limit;
  const currentId = ctx.saved?.id || '__current';
  const options = ctx.newsletters.map(n => h('option', { value: n.id, selected: n.id === currentId, text: n.definition.name }));
  if (!ctx.saved) options.unshift(h('option', { value: '__current', selected: true, text: ctx.started ? (ctx.state.name.trim() || 'Untitled') + ' (not saved)' : 'New newsletter' }));
  options.push(h('option', {
    value: '__new', disabled: atLimit,
    text: atLimit ? `New newsletter (you have ${ctx.limit}, the most you can keep)` : '+ New newsletter',
  }));
  const select = h('select', {
    id: 'nl-switch', class: 'switch__select', 'aria-label': 'My newsletters',
    onchange: e => { if (e.target.value !== currentId) switchTo(e.target.value); },
  }, options);

  const saveBtn = h('button', {
    type: 'button', id: 'save-btn', class: 'btn btn--primary save-btn',
    disabled: saving || !ctx.started || (!dirty && !!ctx.saved),
    onclick: save,
  }, saving ? 'Saving…' : (!dirty && ctx.saved ? 'Saved' : 'Save'));
  const hadFocus = bar.contains(document.activeElement) ? document.activeElement.id : '';
  mount(bar,
    h('div', { class: 'switch' },
      h('span', { class: 'switch__label', 'aria-hidden': 'true', text: 'My newsletters' }), select),
    h('span', { class: 'dirty', hidden: !dirty, title: 'Unsaved changes' },
      h('span', { class: 'dirty__dot', 'aria-hidden': 'true' }), h('span', { class: 'dirty__text', text: 'Unsaved' })),
    h('button', { type: 'button', class: 'btn btn--quiet only-tablet', text: 'Preview', onclick: () => ctx.openPreview() }),
    saveBtn,
    h('p', { class: 'save-msg', id: 'save-msg', role: 'alert', hidden: true }));
  if (hadFocus && $(hadFocus) && !$(hadFocus).disabled) $(hadFocus).focus();
  else if (hadFocus) $('nl-switch').focus();     // e.g. Save just turned into a disabled "Saved"
}

function renderFoot() {
  const foot = $('canvas-foot');
  if (ctx.readOnly || !ctx.started) { mount(foot); foot.hidden = true; return; }
  foot.hidden = false;
  const item = ctx.saved ? ctx.newsletters.find(n => n.id === ctx.saved.id) : null;
  const on = !!item?.subscribed;
  const canToggle = !!item && !busy;
  const sentence = !item
    ? 'Save this newsletter first, then you can have it sent to you.'
    : on ? `Sent to ${ctx.email || 'your email'} every Monday morning.`
      : 'Not being sent. Turn it on to get it every Monday morning.';
  mount(foot,
    h('div', { class: 'sub' },
      h('button', {
        type: 'button', id: 'sub-toggle', class: 'switch-btn', role: 'switch', 'aria-checked': String(on),
        'aria-describedby': 'sub-sentence', disabled: !canToggle, onclick: () => toggleSubscribe(!on),
      }, h('span', { class: 'switch-btn__track', 'aria-hidden': 'true' }, h('span', { class: 'switch-btn__thumb' })),
      h('span', { class: 'switch-btn__label', text: 'Send it to me every Monday' })),
      h('p', { class: 'sub__sentence', id: 'sub-sentence', text: sentence })),
    item ? h('button', { type: 'button', class: 'btn btn--danger delete-btn', disabled: busy, text: 'Delete this newsletter', onclick: remove }) : null);
}

/** Called after every change (app.js ctx.onChange). */
export function renderManage() {
  if (ctx.readOnly) return;
  if (message && !ctx.errors.size && !M.validate(ctx.state, ctx.catalog).length) message = null;
  renderBar();
  if (message) setMessage(message.text, message.tone);
  renderFoot();
}

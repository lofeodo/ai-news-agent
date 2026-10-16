// studio/dnd.js — drag and drop with pointer events, so mouse, touch and pen all work (the HTML5 drag API does
// not fire on touch screens). One drag at a time:
//   - a floating copy of the dragged element follows the pointer,
//   - a line in the target list shows where it will land,
//   - the list's scroll container scrolls when the pointer is near its top or bottom edge,
//   - Escape or releasing outside the list cancels.
// Blocks start dragging from their grip at once (the grip has touch-action: none). Library items start after the
// pointer moves a few pixels with a mouse or pen, so a plain click still adds them; touch taps add instead.

const THRESHOLD = 6;
const EDGE = 64;          // px from the scroller's edge where auto-scroll starts
const MAX_SPEED = 14;     // px per frame

let drag = null;

/** The insertion index in `list` for a pointer at clientY: before the first item whose middle is below it. */
function indexAt(list, y, skip) {
  const kids = [...list.children].filter(el => el !== skip && !el.classList.contains('drop-line') && !el.hidden);
  for (let i = 0; i < kids.length; i++) {
    const r = kids[i].getBoundingClientRect();
    if (y < r.top + r.height / 2) return i;
  }
  return kids.length;
}

function placeLine(list, index, skip) {
  const kids = [...list.children].filter(el => el !== skip && !el.classList.contains('drop-line') && !el.hidden);
  const line = drag.line;
  if (index >= kids.length) list.append(line);
  else list.insertBefore(line, kids[index]);
}

function inside(el, x, y, pad = 0) {
  const r = el.getBoundingClientRect();
  return x >= r.left - pad && x <= r.right + pad && y >= r.top - pad && y <= r.bottom + pad;
}

function autoScroll() {
  if (!drag) return;
  const s = drag.scroller;
  if (s && drag.active) {
    const r = s.getBoundingClientRect();
    let dy = 0;
    if (drag.y < r.top + EDGE) dy = -MAX_SPEED * Math.min(1, (r.top + EDGE - drag.y) / EDGE);
    else if (drag.y > r.bottom - EDGE) dy = MAX_SPEED * Math.min(1, (drag.y - (r.bottom - EDGE)) / EDGE);
    if (dy) { s.scrollTop += dy; update(); }
  }
  drag.raf = requestAnimationFrame(autoScroll);
}

function begin() {
  drag.active = true;
  const src = drag.source;
  const r = src.getBoundingClientRect();
  drag.dx = drag.x - r.left;
  drag.dy = drag.y - r.top;
  const ghost = src.cloneNode(true);
  ghost.removeAttribute('id');
  ghost.querySelectorAll('[id]').forEach(el => el.removeAttribute('id'));
  ghost.classList.add('drag-ghost');
  ghost.setAttribute('aria-hidden', 'true');
  ghost.style.width = `${r.width}px`;
  document.body.append(ghost);
  drag.ghost = ghost;
  drag.line = document.createElement('li');
  drag.line.className = 'drop-line';
  drag.line.setAttribute('aria-hidden', 'true');
  src.classList.add('is-dragging');
  document.body.classList.add('is-dragging-any');
  drag.opts.onStart?.();
  update();
}

function update() {
  if (!drag?.active) return;
  drag.ghost.style.transform = `translate(${drag.x - drag.dx}px, ${drag.y - drag.dy}px)`;
  const list = drag.opts.list();
  const zone = drag.opts.zone?.() || list;
  if (inside(zone, drag.x, drag.y, 8)) {
    drag.index = indexAt(list, drag.y, drag.opts.skip ? drag.source : null);
    placeLine(list, drag.index, drag.opts.skip ? drag.source : null);
  } else {
    drag.index = null;
    drag.line.remove();
  }
}

function end(commit) {
  if (!drag) return;
  const d = drag;
  drag = null;
  cancelAnimationFrame(d.raf);
  window.removeEventListener('pointermove', onMove);
  window.removeEventListener('pointerup', onUp);
  window.removeEventListener('pointercancel', onCancel);
  window.removeEventListener('keydown', onKey, true);
  try { d.handle.releasePointerCapture?.(d.pointerId); } catch { /* already released */ }
  if (!d.active) return;
  d.ghost?.remove();
  d.line?.remove();
  d.source.classList.remove('is-dragging');
  document.body.classList.remove('is-dragging-any');
  // A drag that started on a button must not also click it.
  const swallow = e => { e.stopPropagation(); e.preventDefault(); };
  window.addEventListener('click', swallow, { capture: true, once: true });
  setTimeout(() => window.removeEventListener('click', swallow, { capture: true }), 0);
  if (commit && d.index !== null) d.opts.onDrop(d.index, d.source);
  else d.opts.onCancel?.();
}

function onMove(e) {
  if (!drag || e.pointerId !== drag.pointerId) return;
  drag.x = e.clientX;
  drag.y = e.clientY;
  if (!drag.active) {
    if (Math.hypot(drag.x - drag.x0, drag.y - drag.y0) < drag.opts.threshold) return;
    begin();
  }
  e.preventDefault();
  update();
}

function onUp(e) { if (drag && e.pointerId === drag.pointerId) end(true); }
function onCancel(e) { if (drag && e.pointerId === drag.pointerId) end(false); }
function onKey(e) { if (e.key === 'Escape' && drag?.active) { e.preventDefault(); e.stopPropagation(); end(false); } }

/**
 * Start tracking a possible drag from a pointerdown.
 * opts: list() -> the <ol>/<ul> to drop into; zone() -> the element the pointer must be over (default: list);
 * scroller -> element to auto-scroll; source -> element being dragged; skip -> the source sits in the list
 * (reordering); threshold -> px before the drag starts (0 = at once); onDrop(index, source); onStart; onCancel.
 */
export function startDrag(e, opts) {
  if (drag || e.button > 0) return;
  drag = {
    opts: { threshold: THRESHOLD, ...opts },
    source: opts.source,
    handle: e.currentTarget,
    pointerId: e.pointerId,
    x0: e.clientX, y0: e.clientY, x: e.clientX, y: e.clientY,
    scroller: opts.scroller || null,
    active: false,
    index: null,
  };
  try { e.currentTarget.setPointerCapture?.(e.pointerId); } catch { /* not capturable */ }
  window.addEventListener('pointermove', onMove, { passive: false });
  window.addEventListener('pointerup', onUp);
  window.addEventListener('pointercancel', onCancel);
  window.addEventListener('keydown', onKey, true);
  drag.raf = requestAnimationFrame(autoScroll);
  if (drag.opts.threshold === 0) { e.preventDefault(); begin(); }
}

export function isDragging() {
  return !!drag?.active;
}

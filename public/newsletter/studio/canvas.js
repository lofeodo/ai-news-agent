// studio/canvas.js — the newsletter being built (filled in by roadmap step 6).
import { h, mount } from './dom.js';

let ctx = null;
export function initCanvas(c) { ctx = c; }
export function focusBlock() {}
export function renderCanvas() {
  const name = document.getElementById('nl-name');
  if (document.activeElement !== name) name.value = ctx.state.name;
  name.readOnly = ctx.readOnly;
  mount(document.getElementById('blocks'),
    ctx.state.blocks.map(b => h('li', { text: b.section || b.title || b.widget || b.type })));
}

// Minimal DOM helpers. Panels re-render as HTML strings and only touch the
// DOM when the string changes; clicks are delegated via data-action.
export function el<K extends keyof HTMLElementTagNameMap>(tag: K, attrs: Record<string, string> = {}, html = ''): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  e.innerHTML = html;
  return e;
}

export function esc(s: unknown): string {
  return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!);
}

export class Panel {
  readonly root: HTMLElement;
  private last = '';
  constructor(id: string, onAction?: (action: string, arg: string) => void) {
    this.root = el('div', { id, class: 'panel' });
    this.root.style.display = 'none';
    if (onAction) {
      this.root.addEventListener('click', (ev) => {
        const t = (ev.target as HTMLElement).closest<HTMLElement>('[data-action]');
        if (t) onAction(t.dataset.action!, t.dataset.arg ?? '');
      });
    }
  }
  set(html: string): void {
    if (html === this.last) return;
    this.last = html;
    this.root.style.display = html ? '' : 'none';
    patchChildren(this.root, html);
  }
}

/**
 * Replaces `root`'s top-level children to match `html`, but reuses any
 * existing child whose outerHTML is unchanged instead of destroying and
 * recreating it (#247). Panels such as the docked-robot menu render
 * unrelated content (a DAY/TIME clock that changes every simulation tick)
 * as a sibling of interactive buttons in the same HTML string; blindly
 * setting `innerHTML` on every change therefore tore down and rebuilt the
 * buttons dozens of times a second even though nothing about them changed.
 * A live mouse click's mousedown and mouseup land on the DOM at slightly
 * different times, so a burst of full-subtree rebuilds landing between them
 * can detach the mousedown target before mouseup fires, and the browser
 * drops the click entirely (verified against real Chromium: a burst of
 * replacements during the mousedown-mouseup dwell reduced click success
 * from 100% to under 10%; see ui/dom.test.ts). Diffing one level deep keeps
 * sibling regions (clock vs. buttons) independent, so a clock-only change
 * never touches the buttons' DOM nodes.
 */
function patchChildren(root: HTMLElement, html: string): void {
  const next = document.createElement('div');
  next.innerHTML = html;
  const prevChildren = [...root.children];
  const nextChildren = [...next.children];
  const max = Math.max(prevChildren.length, nextChildren.length);
  for (let i = 0; i < max; i++) {
    const a = prevChildren[i];
    const b = nextChildren[i];
    if (!b) {
      a?.remove();
      continue;
    }
    if (a && a.outerHTML === b.outerHTML) continue; // unchanged: keep the live node
    if (a) a.replaceWith(b);
    else root.appendChild(b);
  }
}

export function btn(action: string, label: string, arg = '', cls = ''): string {
  return `<button data-action="${esc(action)}" data-arg="${esc(arg)}" class="${cls}">${esc(label)}</button>`;
}

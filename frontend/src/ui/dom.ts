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
    this.root.innerHTML = html;
    this.root.style.display = html ? '' : 'none';
  }
}

export function btn(action: string, label: string, arg = '', cls = ''): string {
  return `<button data-action="${esc(action)}" data-arg="${esc(arg)}" class="${cls}">${esc(label)}</button>`;
}

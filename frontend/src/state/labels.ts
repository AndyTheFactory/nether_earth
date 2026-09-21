// CR002.23: structure name labels and robot strength numbers are an optional
// overlay, default off. Per-viewer setting persisted in localStorage; a
// `?labels=1|0` query parameter overrides it for that page load.

export const LABELS_STORAGE_KEY = 'nether.labels';

type Storage = Pick<globalThis.Storage, 'getItem' | 'setItem'>;

function defaultStorage(): Storage | null {
  try {
    return globalThis.localStorage ?? null;
  } catch {
    return null;
  }
}

const parse = (v: string | null): boolean | null =>
  v === null ? null : ['1', 'on', 'true', ''].includes(v.toLowerCase()) ? true : ['0', 'off', 'false'].includes(v.toLowerCase()) ? false : null;

/** Initial labels setting: query parameter, else the stored choice, else off. */
export function loadLabels(search = '', storage: Storage | null = defaultStorage()): boolean {
  const q = parse(new URLSearchParams(search).get('labels'));
  if (q !== null) return q;
  try {
    return parse(storage?.getItem(LABELS_STORAGE_KEY) ?? null) ?? false;
  } catch {
    return false;
  }
}

export function saveLabels(on: boolean, storage: Storage | null = defaultStorage()): void {
  try {
    storage?.setItem(LABELS_STORAGE_KEY, on ? '1' : '0');
  } catch {
    // Storage unavailable (private window, blocked site data): the toggle still applies for this page.
  }
}

/** Which text overlays to draw: structure names also show with the debug grid (G). */
export function textOverlays(ui: { labels: boolean; debugGrid: boolean }): { structureNames: boolean; robotStrength: boolean } {
  return { structureNames: ui.labels || ui.debugGrid, robotStrength: ui.labels };
}

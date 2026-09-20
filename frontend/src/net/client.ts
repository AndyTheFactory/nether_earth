// Transport boundary (M8.4). One small interface so fixture mode and live
// WebSocket mode are interchangeable to every UI/input module.
import type {
  ClientMessage,
  ClientReconnect,
  ServerMessage,
  ServerResync,
  SnapshotMessage,
} from '../../../protocol/generated/types';

export type InboundMessage = ServerMessage | SnapshotMessage | ServerResync;

export interface GameClient {
  send(msg: ClientMessage | ClientReconnect): void;
  connect(): void;
  close(): void;
}

export interface ClientEvents {
  onMessage(msg: InboundMessage): void;
  onOpen(): void;
  onClose(): void;
}

export const PROTOCOL_VERSION = 1 as const;

/** Default WebSocket URL: same origin, `/ws` (matches deploy/nginx.conf). */
export function defaultWsUrl(loc: { protocol: string; host: string } = window.location): string {
  const override = new URLSearchParams(window.location.search).get('ws');
  if (override) return override;
  const scheme = loc.protocol === 'https:' ? 'wss' : 'ws';
  return `${scheme}://${loc.host}/ws`;
}

export function parseInbound(raw: string): InboundMessage | null {
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!data || typeof data !== 'object' || (data as { protocolVersion?: unknown }).protocolVersion !== PROTOCOL_VERSION) {
    return null;
  }
  if (typeof (data as { type?: unknown }).type !== 'string') return null;
  return data as InboundMessage;
}

export class WebSocketClient implements GameClient {
  private ws: WebSocket | null = null;
  private closedByUser = false;

  constructor(
    private readonly url: string,
    private readonly events: ClientEvents,
  ) {}

  connect(): void {
    this.closedByUser = false;
    this.ws?.close();
    const ws = new WebSocket(this.url);
    this.ws = ws;
    ws.onopen = () => this.events.onOpen();
    ws.onmessage = (ev) => {
      const msg = parseInbound(String(ev.data));
      if (msg) this.events.onMessage(msg);
    };
    ws.onclose = () => {
      if (this.ws === ws) this.ws = null;
      if (!this.closedByUser) this.events.onClose();
    };
    ws.onerror = () => {
      /* onclose follows; nothing to add */
    };
  }

  send(msg: ClientMessage | ClientReconnect): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    this.ws.send(JSON.stringify(msg));
  }

  close(): void {
    this.closedByUser = true;
    this.ws?.close();
    this.ws = null;
  }
}

/** Fixture/mock sink: records outbound messages so tests can assert on them. */
export class RecordingClient implements GameClient {
  readonly sent: (ClientMessage | ClientReconnect)[] = [];
  send(msg: ClientMessage | ClientReconnect): void {
    this.sent.push(msg);
  }
  connect(): void {}
  close(): void {}
}

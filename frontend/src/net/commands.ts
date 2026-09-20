// Outbound gameplay command construction (M8.4/M8.5). All wire payloads use
// the generated protocol types; clientSequence is a monotonic per-session
// counter, the only client-side ordering the backend relies on.
import type { ClientGameplayCommand, ClientMessage, ClientReconnect } from '../../../protocol/generated/types';
import type { GameClient } from './client.ts';
import type { Session } from '../state/store.ts';
import { PROTOCOL_VERSION } from './client.ts';

export type CommandPayload = ClientGameplayCommand['payload'];

export class CommandSender {
  private sequence = 0;
  private session: Session | null = null;

  constructor(private readonly client: GameClient) {}

  bind(session: Session | null): void {
    if (session?.sessionToken !== this.session?.sessionToken) this.sequence = 0;
    this.session = session;
  }

  get nextSequence(): number {
    return this.sequence + 1;
  }

  command(payload: CommandPayload): ClientGameplayCommand | null {
    if (!this.session) return null;
    this.sequence += 1;
    const msg: ClientGameplayCommand = {
      protocolVersion: PROTOCOL_VERSION,
      type: 'command',
      matchId: this.session.matchId,
      playerId: this.session.playerId,
      sessionToken: this.session.sessionToken,
      clientSequence: this.sequence,
      payload,
    };
    this.client.send(msg);
    return msg;
  }

  create(nickname: string): void {
    this.client.send({ protocolVersion: PROTOCOL_VERSION, type: 'create', nickname });
  }

  join(joinCode: string, nickname: string): void {
    this.client.send({ protocolVersion: PROTOCOL_VERSION, type: 'join', joinCode, nickname });
  }

  ready(ready: boolean): void {
    const s = this.session;
    if (!s) return;
    const msg: ClientMessage = {
      protocolVersion: PROTOCOL_VERSION,
      type: 'ready',
      matchId: s.matchId,
      playerId: s.playerId,
      sessionToken: s.sessionToken,
      ready,
    };
    this.client.send(msg);
  }

  leave(): void {
    const s = this.session;
    if (!s) return;
    this.client.send({ protocolVersion: PROTOCOL_VERSION, type: 'leave', matchId: s.matchId, playerId: s.playerId, sessionToken: s.sessionToken });
  }

  reconnect(): void {
    const s = this.session;
    if (!s) return;
    const msg: ClientReconnect = {
      protocolVersion: PROTOCOL_VERSION,
      type: 'reconnect',
      matchId: s.matchId,
      playerId: s.playerId,
      sessionToken: s.sessionToken,
    };
    this.client.send(msg);
  }
}

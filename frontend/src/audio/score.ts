// Interpreter for the title-music bytecode (#272).
//
// This is `Lc4fd_title_music_loop_interrupt` and the routines it calls, with
// no audio in it: one `step()` is one 50 Hz frame and returns what the three
// channels are doing during that frame. The bytes it walks are the score
// lifted out of the disassembly by frontend/scripts/decode-music.py.
//
// Bytecode (see Lc528 / Lc53b and the jump tables at Lc54f / Lc56e):
//   >= 9    note: period byte, then a duration in frames
//   0       unused (the original's handler for it corrupts the stack)
//   1 a a   jump to address
//   2 a a   call address
//   3 a a   set the percussion pointer (also 7 and 8)
//   4       activate the channel
//   5       silence the channel
//   6       return from a call
import { CHANNEL1_START, CHANNEL2_START, PERCUSSION_START, SCORE_BASE, SCORE_BYTES } from './title-score.ts';

export type Instrument = 'drum1' | 'drum2' | 'tone';

export interface PercussionHit {
  instrument: Instrument;
  /** Wave period of the tone drum (`Lc616`); 0 for the two noise drums. */
  period: number;
}

export interface ChannelFrame {
  /** Oscillator period byte, as written to the self-modifying counter. */
  period: number;
  /** False while the channel is silenced (`ld a, 0` in place of `ld a, 24`). */
  on: boolean;
}

export interface MusicFrame {
  channel1: ChannelFrame;
  channel2: ChannelFrame;
  /** The percussion fires on some frames only; null on the others. */
  percussion: PercussionHit | null;
}

class Channel {
  ptr: number;
  period = 0;
  on = true;
  /** Frames left of the current note; 1 so the first frame reads an event. */
  frames = 1;
  private returnPtr = 0;

  constructor(
    private readonly score: Score,
    start: number,
  ) {
    this.ptr = start;
  }

  /** Run events until one is a note, which is what consumes frames. */
  nextEvent(): void {
    for (let guard = 0; guard < 1000; guard++) {
      const event = this.score.byte(this.ptr);
      if (event >= 9) {
        this.period = event;
        this.frames = this.score.byte(this.ptr + 1);
        this.ptr += 2;
        return;
      }
      switch (event) {
        case 1:
          this.ptr = this.score.word(this.ptr + 1);
          break;
        case 2:
          this.returnPtr = this.ptr + 3;
          this.ptr = this.score.word(this.ptr + 1);
          break;
        case 3:
        case 7:
        case 8:
          // Lc5cc always reads through the channel-1 pointer, so only
          // channel 1 can move the percussion; channel 2's score never uses
          // these events.
          this.score.percussionPtr = this.score.word(this.ptr + 1);
          this.ptr += 3;
          break;
        case 4:
          this.on = true;
          this.ptr += 1;
          break;
        case 5:
          this.on = false;
          this.ptr += 1;
          break;
        case 6:
          this.ptr = this.returnPtr;
          break;
        default:
          // Event 0 never appears in the score; skip it rather than hang.
          this.ptr += 1;
          break;
      }
    }
    throw new Error('music score made no progress');
  }

  step(): ChannelFrame {
    this.frames -= 1;
    if (this.frames <= 0) this.nextEvent();
    return { period: this.period, on: this.on };
  }
}

/** Plays the decoded title score, one 50 Hz frame at a time. */
export class Score {
  percussionPtr = PERCUSSION_START;
  private readonly channel1 = new Channel(this, CHANNEL1_START);
  private readonly channel2 = new Channel(this, CHANNEL2_START);
  /** Frames until the next percussion hit; 1 so it fires on the first frame. */
  private percussionFrames = 1;

  constructor(
    private readonly bytes: readonly number[] = SCORE_BYTES,
    private readonly base: number = SCORE_BASE,
  ) {}

  byte(address: number): number {
    const value = this.bytes[address - this.base];
    if (value === undefined) throw new Error(`music score read out of range: #${address.toString(16)}`);
    return value;
  }

  word(address: number): number {
    return this.byte(address) | (this.byte(address + 1) << 8);
  }

  /** One interrupt: advance the three channels and report the frame. */
  step(): MusicFrame {
    const percussion = this.stepPercussion();
    return { channel1: this.channel1.step(), channel2: this.channel2.step(), percussion };
  }

  /**
   * `Lc5db_music_percussion`: each entry is `frames, instrument` (plus a
   * period byte for the tone drum) and plays its instrument once, `frames`
   * frames after the previous hit. `01 lo hi` is a go-to.
   */
  private stepPercussion(): PercussionHit | null {
    this.percussionFrames -= 1;
    if (this.percussionFrames > 0) return null;

    let frames = this.byte(this.percussionPtr++);
    while (frames === 1) {
      this.percussionPtr = this.word(this.percussionPtr);
      frames = this.byte(this.percussionPtr++);
    }
    this.percussionFrames = frames;

    const instrument = this.byte(this.percussionPtr++);
    if (instrument === 2) return { instrument: 'tone', period: this.byte(this.percussionPtr++) };
    return { instrument: instrument === 0 ? 'drum1' : 'drum2', period: 0 };
  }
}

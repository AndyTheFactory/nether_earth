// Beeper primitives shared by the music and the SFX (#272).
//
// Every sound in the original game is the CPU toggling bit 3/4 of the ULA port
// inside a timing loop, so the sound's pitch and length are the loop's T-state
// count. The routines here reproduce those counts rather than guessing
// frequencies: `Tape` records the port transitions of a routine on a T-state
// clock, and `renderTape` turns that into PCM.

/** Z80 clock of a 48K Spectrum. Every T-state count below is at this rate. */
export const CPU_HZ = 3_500_000;

/** The interrupt (and so the music/SFX frame) runs at 50 Hz. */
export const FRAME_T = CPU_HZ / 50;

/**
 * Records `out (ULA_PORT), a` transitions on a T-state clock.
 *
 * Levels are 0 or 1; the speaker is where the game writes 16 or 24 (bit 4) and
 * silent where it writes 0.
 */
export class Tape {
  readonly times: number[] = [];
  readonly levels: number[] = [];
  t = 0;
  private level = 0;

  /** Advance the clock without touching the speaker. */
  wait(tstates: number): void {
    this.t += tstates;
  }

  /** `out (ULA_PORT), a` with the speaker bit of `a` set or clear. */
  out(level: number): void {
    const next = level ? 1 : 0;
    if (next !== this.level || this.times.length === 0) {
      this.times.push(this.t);
      this.levels.push(next);
      this.level = next;
    }
  }
}

/**
 * Render a tape to mono PCM by integrating the speaker level over each sample
 * window. Integration rather than point sampling matters: the noise routines
 * toggle the port faster than 40 kHz, and point sampling those turns a hiss
 * into an arbitrary whine.
 */
export function renderTape(tape: Tape, sampleRate: number, lengthT = tape.t): Float32Array {
  const samples = Math.max(1, Math.ceil((lengthT / CPU_HZ) * sampleRate));
  const out = new Float32Array(samples);
  const perSample = CPU_HZ / sampleRate;
  let edge = 0;
  let level = 0;
  for (let i = 0; i < samples; i++) {
    const start = i * perSample;
    const end = start + perSample;
    let area = 0;
    let at = start;
    while (edge < tape.times.length && tape.times[edge] < end) {
      const t = Math.max(tape.times[edge], start);
      area += (t - at) * level;
      level = tape.levels[edge];
      at = t;
      edge++;
    }
    area += (end - at) * level;
    // Centre on zero: the speaker idles at one of the two levels, and a DC
    // offset would only click on start and stop.
    out[i] = (area / perSample) * 2 - 1;
  }
  return out;
}

/**
 * Stand-in for the game's noise source.
 *
 * The original reads bytes out of the Spectrum ROM (`ld h, 0` / `ld h, 8` /
 * `ld hl, 144`) and keeps one bit of each, which is noise because ROM code is
 * not a waveform. The ROM is not ours to redistribute, so a seeded PRNG
 * supplies the bits instead: perceptually the same hiss, and deterministic for
 * the tests. This is the one deliberate deviation from the disassembly.
 */
export function noiseBits(seed: number): () => number {
  let s = seed >>> 0 || 1;
  return () => {
    // xorshift32
    s ^= s << 13;
    s >>>= 0;
    s ^= s >>> 17;
    s ^= s << 5;
    s >>>= 0;
    return s & 1;
  };
}

/**
 * Period of one wave of a music channel, in T-states, for the period byte the
 * score writes into the oscillator.
 *
 * The oscillator loop (`Lc4cb`..`Lc4fc`) costs 122 T per pass while it is just
 * counting down, and the pass that emits the pulse costs 128 T plus 14 T per
 * step of the pulse-width counter, which the score interpreter sets to
 * `period >> 2` (`rrca rrca` / `and #3f`).
 *
 * Both channels share that one loop, so on the Spectrum each channel's pitch
 * shifts slightly while the other one is sounding. That cross-talk is not
 * modelled here; the channels are rendered as if each had the loop to itself.
 */
export function tonePeriodT(period: number): number {
  return 122 * period + 14 * (period >> 2) + 6;
}

/** Frequency the oscillator produces for a score period byte. */
export function toneFrequency(period: number): number {
  return CPU_HZ / tonePeriodT(period);
}

/**
 * Fraction of each wave the speaker is pushed out for.
 *
 * The pulse is the `ld a, 24` / wait / `ld a, 0` part of the loop, which is
 * short next to the countdown: about 3% at every pitch, which is where the
 * thin, buzzy beeper timbre comes from.
 */
export function toneDuty(period: number): number {
  return (14 * (period >> 2) + 39) / tonePeriodT(period);
}

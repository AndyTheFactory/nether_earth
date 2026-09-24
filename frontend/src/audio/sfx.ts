// The game's sound effects, re-run as T-state tapes (#272).
//
// Each function below is one routine of the disassembly with its instruction
// timings kept, so the pitch and the length of the result are the Spectrum's
// rather than a reimagining. The ROM bytes the noise routines read are the one
// substitution; see `noiseBits`.
import { CPU_HZ, FRAME_T, Tape, noiseBits, renderTape } from './spectrum.ts';

/** Speaker bit the game writes: `ld a, 24` for the music, `and 16` for noise. */
const ON = 1;

/**
 * Values the game latches in `Lfd53_produce_in_game_sound`, from the sites
 * that write it. The interrupt increments the latch every frame and stops at
 * 128 (`Ld582_radar_flicker`), so the value is also the sound's length:
 * negative values run for `256 - value` frames, and 1 runs for 127.
 */
export const IN_GAME_SOUNDS = {
  /** Lb70c: a robot fired. */
  fire: 1,
  /** Lb7ea_bullet_disappear: the shot expired without hitting. */
  bullet_gone: 252,
  /** Lb7db_robot_hit: the shot damaged a robot. */
  hit: 250,
  /** Lb7db_robot_hit with strength <= 0: the robot is going up. */
  destroyed: 200,
} as const;

/**
 * `a` values the UI passes to `Lccac_beep`, from its call sites: the
 * construction cursor (Lcb4a area), menu selection, a finished robot
 * (Lccb9_construction_finished) and the end of the game (Lc4fc area).
 */
export const UI_BEEPS = {
  cursor: 20,
  select: 120,
  confirm: 100,
  built: 200,
  game_over: 250,
} as const;

/**
 * `Lccac_beep`: a square wave of 256 half-waves, the inner `djnz` delay set by
 * `a`. Roughly 5.4 kHz and 22 ms for `a = 20`, 540 Hz and 240 ms for `a = 250`.
 */
export function beepTape(a: number): Tape {
  const tape = new Tape();
  let level = 0;
  for (let i = 0; i < 256; i++) {
    level ^= ON;
    tape.out(level);
    // push bc / xor 16 / out / djnz a / pop bc / dec c / jr nz
    tape.wait(13 * a + 50);
  }
  tape.out(0);
  return tape;
}

/**
 * `Ld5ec_random_noise`: 30 noise pulses, called once per frame while the latch
 * is negative. The gap between the bursts is what gives the in-game sounds
 * their 50 Hz rattle.
 */
function randomNoiseFrame(tape: Tape, bit: () => number): void {
  for (let i = 0; i < 30; i++) {
    tape.out(bit() ? ON : 0);
    // call Ld358_random / and 16 / out / djnz
    tape.wait(141);
  }
}

/**
 * `Ld5c4_produce_in_game_sound` with a positive latch: `500 / (a + 2)` pulses
 * of ROM noise, each `a` `djnz` steps long, so the pitch falls and the burst
 * shortens as the latch counts up frame by frame.
 */
function romNoiseFrame(tape: Tape, a: number, bit: () => number): void {
  let pulses = 0;
  for (let hl = 500; hl >= 0; hl -= a + 2) pulses++;
  for (let i = 0; i < pulses; i++) {
    tape.out(bit() ? ON : 0);
    // push bc / ld a,(hl) / inc hl / and 16 / out / djnz b / pop bc / dec c / jr nz
    tape.wait(13 * a + 58);
  }
  tape.out(0);
}

/**
 * A whole in-game sound: the interrupt replays the routine every frame and
 * increments the latch until it reaches 128 (or wraps to 0), so one trigger is
 * a burst of frames, not a single pulse.
 */
export function inGameSoundTape(latch: number, seed = 1): Tape {
  const tape = new Tape();
  const bit = noiseBits(seed);
  let a = latch;
  for (let guard = 0; guard < 256; guard++) {
    const start = tape.t;
    // `jp m`: the latch is a signed byte, so everything but the fire sound
    // takes the random-noise branch.
    if (a >= 128) randomNoiseFrame(tape, bit);
    else romNoiseFrame(tape, a, bit);
    tape.out(0);
    tape.t = start + FRAME_T;
    a = (a + 1) & 0xff;
    if (a === 128 || a === 0) break;
  }
  return tape;
}

/**
 * `Lbaee_nuclear_explosion_sfx` driven by its caller's `ld bc, #f401` loop:
 * each call emits `b` noise pulses of period `c`, then `b` drops by 3 (twice
 * in the routine, once in the caller's `djnz`) and `c` grows by 3, so the roar
 * falls in pitch and thins out over about five seconds.
 */
export function nuclearTape(seed = 1): Tape {
  const tape = new Tape();
  const bit = noiseBits(seed);
  let b = 0xf4;
  let c = 1;
  while (b > 3) {
    for (let i = 0; i < b; i++) {
      tape.out(bit() ? ON : 0);
      // ld a,(hl) / and 16 / out / inc hl / dec c + nop + nop + jr nz
      tape.wait(24 * c + 36);
    }
    b -= 3;
    c += 3;
  }
  tape.out(0);
  return tape;
}

/** `Lc608_drum1`: one ROM byte held at the port for 96 `djnz` steps: a click. */
function drum1Tape(bit: () => number): Tape {
  const tape = new Tape();
  tape.out(bit() ? ON : 0);
  tape.wait(96 * 38);
  tape.out(0);
  return tape;
}

/**
 * `Lc5f0_drum2`: 80 ROM bytes, each held for `(~b) & 63` `djnz` steps, so the
 * noise coarsens as the counter runs down: the snare of the title music.
 */
function drum2Tape(bit: () => number): Tape {
  const tape = new Tape();
  for (let b = 80; b > 0; b--) {
    tape.out(bit() ? ON : 0);
    tape.wait(16 * (~b & 63) + 46);
  }
  tape.out(0);
  return tape;
}

/**
 * `Lc616_tone_drum`: 48 pulses whose off-time counts down from the entry's
 * period byte while the on-time grows by 4 each pulse: a short, falling blip.
 */
function toneDrumTape(period: number): Tape {
  const tape = new Tape();
  let l = period & 0xff;
  let h = ((period >> 1) | ((period & 1) << 7)) & 0xff;
  for (let i = 0; i < 48; i++) {
    tape.out(0);
    l = (l - 1) & 0xff;
    tape.wait(16 * l + 30);
    tape.out(ON);
    h = (4 + h) & 0xff;
    tape.wait(16 * h + 30);
  }
  tape.out(0);
  return tape;
}

/** PCM for one percussion hit of the title music. */
export function renderDrum(
  instrument: 'drum1' | 'drum2' | 'tone',
  period: number,
  sampleRate: number,
  seed = 1,
): Float32Array {
  const bit = noiseBits(seed);
  const tape =
    instrument === 'tone' ? toneDrumTape(period) : instrument === 'drum1' ? drum1Tape(bit) : drum2Tape(bit);
  return renderTape(tape, sampleRate);
}

/** PCM for a UI beep (`Lccac_beep`). */
export function renderBeep(a: number, sampleRate: number): Float32Array {
  return renderTape(beepTape(a), sampleRate);
}

/** PCM for an in-game sound, identified by its latch value. */
export function renderInGameSound(latch: number, sampleRate: number, seed = 1): Float32Array {
  return renderTape(inGameSoundTape(latch, seed), sampleRate);
}

/** PCM for the nuclear explosion. */
export function renderNuclear(sampleRate: number, seed = 1): Float32Array {
  return renderTape(nuclearTape(seed), sampleRate);
}

/** Length of a tape in seconds, for tests and for scheduling. */
export function tapeSeconds(tape: Tape): number {
  return tape.t / CPU_HZ;
}

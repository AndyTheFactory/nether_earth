import { describe, expect, it } from 'vitest';
import { beepTape, inGameSoundTape, nuclearTape, renderBeep, tapeSeconds, IN_GAME_SOUNDS } from './sfx.ts';
import { CPU_HZ, Tape, renderTape, toneFrequency } from './spectrum.ts';

describe('beeper tapes', () => {
  it('gives Lccac_beep the pitch and length its T-states imply', () => {
    // 256 half-waves of 13a + 50 T: a = 120 is ~1.1 kHz for ~0.12 s.
    const tape = beepTape(120);
    expect(tapeSeconds(tape)).toBeCloseTo((256 * (13 * 120 + 50)) / CPU_HZ, 5);
    const halfWave = tape.times[1] - tape.times[0];
    expect(CPU_HZ / (2 * halfWave)).toBeCloseTo(1087, 0);
  });

  it('makes the cursor blip short and high next to the game-over beep', () => {
    expect(tapeSeconds(beepTape(20))).toBeLessThan(tapeSeconds(beepTape(250)) / 5);
  });

  it('runs an in-game sound for the frames its latch value leaves', () => {
    // The interrupt increments the latch each frame and stops at 128 or 0.
    expect(tapeSeconds(inGameSoundTape(IN_GAME_SOUNDS.bullet_gone))).toBeCloseTo(4 / 50, 3);
    expect(tapeSeconds(inGameSoundTape(IN_GAME_SOUNDS.destroyed))).toBeCloseTo(56 / 50, 3);
    expect(tapeSeconds(inGameSoundTape(IN_GAME_SOUNDS.fire))).toBeCloseTo(127 / 50, 3);
  });

  it('sweeps the nuclear blast down over several seconds', () => {
    const tape = nuclearTape();
    const seconds = tapeSeconds(tape);
    expect(seconds).toBeGreaterThan(3);
    expect(seconds).toBeLessThan(8);
    // Pulses get longer as the routine's period counter grows.
    const first = tape.times[1] - tape.times[0];
    const last = tape.times[tape.times.length - 1] - tape.times[tape.times.length - 2];
    expect(last).toBeGreaterThan(first);
  });

  it('renders PCM that swings across zero and is the tape length', () => {
    const rate = 44100;
    const pcm = renderBeep(120, rate);
    expect(pcm.length).toBeCloseTo(rate * tapeSeconds(beepTape(120)), -1);
    expect(Math.max(...pcm)).toBeGreaterThan(0.5);
    expect(Math.min(...pcm)).toBeLessThan(-0.5);
  });

  it('integrates rather than point-samples, so a fast toggle stays quiet-ish', () => {
    // A square wave far above Nyquist must not come back as a full-scale tone.
    const tape = new Tape();
    for (let i = 0; i < 2000; i++) {
      tape.out(i & 1);
      tape.wait(20);
    }
    const pcm = renderTape(tape, 44100);
    expect(Math.max(...pcm.map(Math.abs))).toBeLessThan(0.5);
  });

  it('is deterministic for a given noise seed', () => {
    const a = inGameSoundTape(IN_GAME_SOUNDS.hit, 7).levels.join('');
    const b = inGameSoundTape(IN_GAME_SOUNDS.hit, 7).levels.join('');
    expect(a).toBe(b);
    expect(inGameSoundTape(IN_GAME_SOUNDS.hit, 8).levels.join('')).not.toBe(a);
  });

  it('keeps the music oscillator inside the audible range', () => {
    // The score's extremes are #29 and #ba.
    expect(toneFrequency(0x29)).toBeLessThan(1000);
    expect(toneFrequency(0xba)).toBeGreaterThan(100);
  });
});

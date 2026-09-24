import { describe, expect, it } from 'vitest';
import { Score } from './score.ts';
import { SCORE_BASE, SCORE_BYTES, CHANNEL1_START, CHANNEL2_START } from './title-score.ts';
import { toneFrequency } from './spectrum.ts';

describe('title score', () => {
  it('starts both channels on the notes the disassembly opens with', () => {
    // Channel 1 jumps to the percussion pointer, then calls Lc6bd, whose first
    // note is #7c for #10 frames; channel 2 calls Lc789, first note #37.
    const frame = new Score().step();
    expect(frame.channel1).toEqual({ period: 0x7c, on: true });
    expect(frame.channel2).toEqual({ period: 0x37, on: true });
  });

  it('holds a note for the frame count that follows it', () => {
    const score = new Score();
    for (let i = 0; i < 0x10; i++) expect(score.step().channel1.period).toBe(0x7c);
    // Lc6bd continues `#05` (a command, not a note) -> activate, then #7c #08.
    expect(score.step().channel1.period).toBe(0x7c);
  });

  it('runs for a full minute of frames without leaving the score', () => {
    const score = new Score();
    for (let i = 0; i < 50 * 60; i++) score.step();
    expect(score.step().channel1.period).toBeGreaterThanOrEqual(9);
  });

  it('fires the percussion on the first frame and every 32 frames after', () => {
    const score = new Score();
    const hits: number[] = [];
    for (let i = 0; i < 100; i++) if (score.step().percussion) hits.push(i);
    expect(hits.slice(0, 4)).toEqual([0, 32, 64, 96]);
  });

  it('silences and reactivates channel 2 where the score says to', () => {
    // Channel 2's table has a bare `#05` (silence) followed later by `#04`.
    const score = new Score();
    let silenced = false;
    for (let i = 0; i < 50 * 120 && !silenced; i++) silenced = !score.step().channel2.on;
    expect(silenced).toBe(true);
  });

  it('reads note periods that map to a chromatic scale', () => {
    // #29 and #52 are an octave apart in the data, so they must be in the
    // rendered pitches too: that is the check that tonePeriodT is sane.
    expect(toneFrequency(0x29) / toneFrequency(0x52)).toBeCloseTo(2, 1);
  });

  it('decodes a blob whose entry points sit inside it', () => {
    expect(SCORE_BYTES.length).toBeGreaterThan(400);
    for (const entry of [CHANNEL1_START, CHANNEL2_START]) {
      expect(entry - SCORE_BASE).toBeLessThan(SCORE_BYTES.length);
    }
  });
});

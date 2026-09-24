// WebAudio playback for the decoded Spectrum audio (#272).
//
// The beeper only ever pushed the speaker in or out, so the whole sound set is
// two pulse oscillators plus short noise buffers. The music is scheduled ahead
// frame by frame from `Score`; the effects are rendered once per sound and
// replayed from an AudioBuffer.
import { Score, type MusicFrame } from './score.ts';
import {
  IN_GAME_SOUNDS,
  UI_BEEPS,
  renderBeep,
  renderDrum,
  renderInGameSound,
  renderNuclear,
} from './sfx.ts';
import { toneDuty, toneFrequency } from './spectrum.ts';

export type SfxName =
  | keyof typeof UI_BEEPS
  | keyof typeof IN_GAME_SOUNDS
  | 'nuclear';

const MUTE_KEY = 'nether-earth.muted';
/** The music interpreter's frame, and the rate the oscillators are steered at. */
const FRAME_S = 1 / 50;
/** How far ahead frames are scheduled, and how often the scheduler wakes. */
const LOOKAHEAD_S = 0.25;
const TICK_MS = 60;
/** Two thin pulses and a drum at once clip at unity, so leave headroom. */
const MASTER_GAIN = 0.25;

/** One music channel: a pulse oscillator with its own on/off gate. */
class ToneChannel {
  private readonly oscillator: OscillatorNode;
  private readonly gate: GainNode;

  constructor(context: AudioContext, destination: AudioNode, wave: PeriodicWave) {
    this.oscillator = context.createOscillator();
    this.oscillator.setPeriodicWave(wave);
    this.gate = context.createGain();
    this.gate.gain.value = 0;
    this.oscillator.connect(this.gate).connect(destination);
    this.oscillator.start();
  }

  frame(at: number, period: number, on: boolean): void {
    if (period > 0) this.oscillator.frequency.setValueAtTime(toneFrequency(period), at);
    this.gate.gain.setValueAtTime(on && period > 0 ? 1 : 0, at);
  }

  stop(at: number): void {
    this.gate.gain.setValueAtTime(0, at);
    this.oscillator.stop(at + 0.05);
  }
}

/**
 * Fourier series of a pulse train, which is what the oscillator loop emits.
 *
 * The duty is about 3% at every pitch (see `toneDuty`), so one wave serves the
 * whole score.
 */
function pulseWave(context: AudioContext, duty: number, harmonics = 64): PeriodicWave {
  const real = new Float32Array(harmonics + 1);
  const imag = new Float32Array(harmonics + 1);
  for (let n = 1; n <= harmonics; n++) {
    real[n] = (2 / (n * Math.PI)) * Math.sin(n * Math.PI * duty);
  }
  return context.createPeriodicWave(real, imag, { disableNormalization: false });
}

/**
 * Sound playback for the whole app.
 *
 * Nothing is created until `unlock()` runs inside a user gesture, because
 * browsers refuse to start an AudioContext outside one; before that every call
 * is a no-op, so callers never have to check.
 */
export class AudioEngine {
  private context: AudioContext | null = null;
  private master: GainNode | null = null;
  private wave: PeriodicWave | null = null;
  private muted = readMuted();
  private readonly buffers = new Map<SfxName, AudioBuffer>();

  private score: Score | null = null;
  private channels: [ToneChannel, ToneChannel] | null = null;
  private nextFrameAt = 0;
  private timer: ReturnType<typeof setInterval> | null = null;

  get isMuted(): boolean {
    return this.muted;
  }

  /** Start (or resume) the audio context. Safe to call on every gesture. */
  unlock(): void {
    if (!this.context) {
      const Ctor = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
      if (!Ctor) return;
      this.context = new Ctor();
      this.master = this.context.createGain();
      this.master.gain.value = this.muted ? 0 : MASTER_GAIN;
      this.master.connect(this.context.destination);
      this.wave = pulseWave(this.context, toneDuty(0x7c));
    }
    void this.context.resume();
  }

  setMuted(muted: boolean): void {
    this.muted = muted;
    try {
      localStorage.setItem(MUTE_KEY, muted ? '1' : '0');
    } catch {
      // Private browsing: the toggle still works, it just will not persist.
    }
    if (this.master && this.context) {
      this.master.gain.setValueAtTime(muted ? 0 : MASTER_GAIN, this.context.currentTime);
    }
    if (muted) this.stopMusic();
  }

  toggleMuted(): boolean {
    this.setMuted(!this.muted);
    return this.muted;
  }

  /** Play the title music from the top; a no-op while it is already playing. */
  startMusic(): void {
    if (this.timer || this.muted) return;
    this.unlock();
    if (!this.context || !this.master || !this.wave) return;
    this.score = new Score();
    this.channels = [
      new ToneChannel(this.context, this.master, this.wave),
      new ToneChannel(this.context, this.master, this.wave),
    ];
    this.nextFrameAt = this.context.currentTime + 0.1;
    this.timer = setInterval(() => this.schedule(), TICK_MS);
    this.schedule();
  }

  stopMusic(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    const at = this.context?.currentTime ?? 0;
    this.channels?.forEach((c) => c.stop(at));
    this.channels = null;
    this.score = null;
  }

  /** Queue the frames that fall inside the lookahead window. */
  private schedule(): void {
    if (!this.context || !this.score || !this.channels) return;
    // A suspended context (autoplay policy, or a backgrounded tab) freezes
    // currentTime. Scheduling against it would queue a pile of frames in the
    // past that all fire at once on resume, so wait and then resync.
    if (this.context.state !== 'running') {
      this.nextFrameAt = this.context.currentTime + 0.1;
      return;
    }
    if (this.nextFrameAt < this.context.currentTime) this.nextFrameAt = this.context.currentTime + 0.05;
    const until = this.context.currentTime + LOOKAHEAD_S;
    while (this.nextFrameAt < until) {
      const frame: MusicFrame = this.score.step();
      this.channels[0].frame(this.nextFrameAt, frame.channel1.period, frame.channel1.on);
      this.channels[1].frame(this.nextFrameAt, frame.channel2.period, frame.channel2.on);
      if (frame.percussion) {
        this.playBuffer(this.drum(frame.percussion.instrument, frame.percussion.period), this.nextFrameAt);
      }
      this.nextFrameAt += FRAME_S;
    }
  }

  /** Play one effect now. Unknown names are ignored rather than thrown. */
  play(name: SfxName): void {
    if (this.muted || !this.context || this.context.state !== 'running') return;
    const buffer = this.buffers.get(name) ?? this.render(name);
    if (buffer) this.playBuffer(buffer, this.context.currentTime);
  }

  private playBuffer(buffer: AudioBuffer | null, at: number): void {
    if (!buffer || !this.context || !this.master) return;
    const source = this.context.createBufferSource();
    source.buffer = buffer;
    source.connect(this.master);
    source.start(at);
  }

  private render(name: SfxName): AudioBuffer | null {
    if (!this.context) return null;
    const rate = this.context.sampleRate;
    const samples =
      name === 'nuclear'
        ? renderNuclear(rate)
        : name in UI_BEEPS
          ? renderBeep(UI_BEEPS[name as keyof typeof UI_BEEPS], rate)
          : renderInGameSound(IN_GAME_SOUNDS[name as keyof typeof IN_GAME_SOUNDS], rate);
    const buffer = this.toBuffer(samples);
    if (buffer) this.buffers.set(name, buffer);
    return buffer;
  }

  private readonly drums = new Map<string, AudioBuffer>();

  private drum(instrument: 'drum1' | 'drum2' | 'tone', period: number): AudioBuffer | null {
    if (!this.context) return null;
    const key = `${instrument}:${period}`;
    const cached = this.drums.get(key);
    if (cached) return cached;
    const buffer = this.toBuffer(renderDrum(instrument, period, this.context.sampleRate));
    if (buffer) this.drums.set(key, buffer);
    return buffer;
  }

  private toBuffer(samples: Float32Array): AudioBuffer | null {
    if (!this.context) return null;
    const buffer = this.context.createBuffer(1, samples.length, this.context.sampleRate);
    buffer.copyToChannel(samples, 0);
    return buffer;
  }
}

function readMuted(): boolean {
  try {
    return localStorage.getItem(MUTE_KEY) === '1';
  } catch {
    return false;
  }
}

const KEY = "skyeye.sound";

export function loadSoundEnabled(): boolean {
  try {
    return window.localStorage.getItem(KEY) === "1";
  } catch {
    return false;
  }
}

export function saveSoundEnabled(on: boolean): void {
  try {
    window.localStorage.setItem(KEY, on ? "1" : "0");
  } catch {
    // ignore: the choice just will not persist
  }
}

let ctx: AudioContext | null = null;

function getContext(): AudioContext | null {
  if (ctx) return ctx;
  const Ctor: typeof AudioContext | undefined =
    window.AudioContext ??
    (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!Ctor) return null;
  ctx = new Ctor();
  return ctx;
}

/** Create or resume the audio context. Call from a user gesture (Safari/iOS need that). */
export function unlockAudio(): void {
  try {
    const c = getContext();
    if (c && c.state === "suspended") void c.resume();
  } catch {
    // no audio available
  }
}

/** A short 880 Hz beep (150 ms). Does nothing where Web Audio is unavailable or blocked. */
export function beep(): void {
  try {
    const c = getContext();
    if (!c) return;
    if (c.state === "suspended") void c.resume();
    const osc = c.createOscillator();
    const gain = c.createGain();
    osc.type = "sine";
    osc.frequency.value = 880;
    gain.gain.value = 0.15;
    osc.connect(gain);
    gain.connect(c.destination);
    osc.start();
    osc.stop(c.currentTime + 0.15);
  } catch {
    // no audio: stay silent
  }
}

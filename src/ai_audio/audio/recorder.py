"""Microphone recorder.

Toggle-only interface: ``start()`` begins recording, ``stop()`` finishes it and
returns the recorded audio buffer. There is **no VAD or silence-based
auto-stop**; this is intentional to support speakers who stutter or pause for
long stretches while thinking.

The audio callback runs on a separate thread and only enqueues frames; no
network calls or heavy processing happen in the callback.
"""

from __future__ import annotations

import queue
import threading
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import sounddevice as sd

DTYPE = "int16"


@dataclass
class RecordingResult:
    audio: np.ndarray  # shape (n_samples, channels), int16
    sample_rate: int
    channels: int
    duration_seconds: float


class Recorder:
    """Toggle-style microphone recorder."""

    def __init__(self, sample_rate: int = 16000, channels: int = 1) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self._queue: queue.Queue[np.ndarray] = queue.Queue()
        self._stream: sd.InputStream | None = None
        self._lock = threading.Lock()
        self._recording = False
        # Latest RMS level (0.0 - 1.0). Single float assignment is atomic in CPython,
        # so the GUI thread can poll without locking.
        self._latest_level: float = 0.0

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def current_level(self) -> float:
        """Most recent RMS level in [0.0, 1.0]. Useful for live level meters."""
        return self._latest_level

    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ARG002
        # Drop nothing — even if status flags overruns, we keep going.
        # Copy because sounddevice reuses the buffer.
        if self._recording:
            # Use peak (max abs) instead of RMS for the level meter. RMS values
            # for normal speech at 16-bit/16 kHz are very small (0.01-0.05), so
            # the bars barely move. Peak is much more visually responsive while
            # still being a reasonable proxy for "is the user speaking".
            try:
                arr = indata.astype(np.float32) / 32768.0
                peak = float(np.max(np.abs(arr)))
                self._latest_level = min(1.0, peak)
            except Exception:
                self._latest_level = 0.0
            self._queue.put(indata.copy())

    def start(self) -> None:
        with self._lock:
            if self._recording:
                return
            # Drain any leftover frames from a previous session.
            while not self._queue.empty():
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    break
            self._recording = True
            self._latest_level = 0.0
            self._stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype=DTYPE,
                callback=self._callback,
            )
            self._stream.start()

    def stop(self) -> RecordingResult:
        with self._lock:
            if not self._recording:
                raise RuntimeError("Recorder is not running")
            self._recording = False
            self._latest_level = 0.0
            assert self._stream is not None
            self._stream.stop()
            self._stream.close()
            self._stream = None

        frames: list[np.ndarray] = []
        while not self._queue.empty():
            try:
                frames.append(self._queue.get_nowait())
            except queue.Empty:
                break

        if not frames:
            audio = np.zeros((0, self.channels), dtype=np.int16)
        else:
            audio = np.concatenate(frames, axis=0)

        duration = audio.shape[0] / self.sample_rate
        return RecordingResult(
            audio=audio,
            sample_rate=self.sample_rate,
            channels=self.channels,
            duration_seconds=duration,
        )


def write_wav(path: Path, result: RecordingResult) -> None:
    """Persist a RecordingResult to a 16-bit PCM WAV file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(result.channels)
        wf.setsampwidth(2)  # int16
        wf.setframerate(result.sample_rate)
        wf.writeframes(result.audio.tobytes())

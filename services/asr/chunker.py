"""
chunker.py — Streaming chunker with local-agreement policy.

Implements the whisper-streaming approach:
  1. PCM frames are accumulated in a rolling buffer.
  2. Every STEP_MS milliseconds Whisper is run on the full buffer.
  3. A prefix of the transcription is considered *stable* (ready to emit as
     "final") once the last N consecutive transcriptions agree on the same
     prefix text.
  4. When the client sends an end-of-stream signal the remaining buffer is
     flushed unconditionally.

Usage (async generator pattern)::

    chunker = StreamingChunker(transcriber, vad)
    async for event in chunker.feed(pcm_bytes):
        send_to_client(event)
    async for event in chunker.flush():
        send_to_client(event)
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import AsyncIterator, Optional

import numpy as np

from transcriber import WhisperTranscriber
from vad import VADProcessor

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration constants (tunable via constructor kwargs)
# ---------------------------------------------------------------------------
STEP_MS: int = 500          # run Whisper every N milliseconds of accumulated audio
AGREEMENT_RUNS: int = 3     # how many consecutive matching prefixes = stable
SAMPLING_RATE: int = 16000  # Hz
BYTES_PER_SAMPLE: int = 2   # int16 -> 2 bytes per sample

# Derived
STEP_SAMPLES: int = (SAMPLING_RATE * STEP_MS) // 1000  # 8 000 samples per step


class StreamingChunker:
    """Stateful PCM accumulator with local-agreement stable-prefix detection.

    This object is **not** coroutine-safe; use one instance per WebSocket
    connection.

    Parameters
    ----------
    transcriber:
        A ready :class:`~transcriber.WhisperTranscriber` instance.
    vad:
        A ready :class:`~vad.VADProcessor` instance.
    step_ms:
        Interval in milliseconds between consecutive Whisper inference
        passes over the accumulated buffer.  Default ``500``.
    agreement_runs:
        Number of consecutive identical-prefix passes required before a
        prefix is emitted as ``"final"``.  Default ``3``.
    session_id:
        Optional identifier forwarded in every emitted event dict so
        callers can correlate events to a WebSocket session.
    """

    def __init__(
        self,
        transcriber: WhisperTranscriber,
        vad: VADProcessor,
        *,
        step_ms: int = STEP_MS,
        agreement_runs: int = AGREEMENT_RUNS,
        session_id: Optional[str] = None,
    ) -> None:
        self._transcriber = transcriber
        self._vad = vad
        self._step_samples = (SAMPLING_RATE * step_ms) // 1000
        self._agreement_runs = agreement_runs
        self.session_id = session_id

        # Rolling buffer of float32 samples
        self._buffer: np.ndarray = np.array([], dtype=np.float32)

        # Tracks how many chars of prefix have already been finalised
        self._committed_chars: int = 0

        # Deque of the last N transcription texts - used for agreement check
        self._history: deque[str] = deque(maxlen=agreement_runs)

        # Samples added since the last Whisper pass
        self._samples_since_step: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def feed(self, raw_bytes: bytes) -> AsyncIterator[dict]:
        """Accept a binary PCM frame and yield zero or more event dicts.

        Parameters
        ----------
        raw_bytes:
            Raw PCM bytes: 16 kHz, mono, int16 little-endian.

        Yields
        ------
        dict
            Either a ``"partial"`` event (unstable hypothesis) or a
            ``"final"`` event (stable prefix, ready to display permanently).
        """
        # Decode int16 -> float32 in [-1, 1]
        pcm_int16 = np.frombuffer(raw_bytes, dtype=np.int16)
        pcm_float = pcm_int16.astype(np.float32) / 32768.0

        self._buffer = np.concatenate([self._buffer, pcm_float])
        self._samples_since_step += len(pcm_float)

        # Only run inference if we have accumulated at least one step
        if self._samples_since_step < self._step_samples:
            return

        self._samples_since_step = 0

        async for event in self._run_pass():
            yield event

    async def flush(self) -> AsyncIterator[dict]:
        """Flush remaining buffer as a single ``"final"`` event.

        Called when the client sends ``{"type": "end"}``.  Runs Whisper on
        whatever PCM is left in the buffer and emits it as ``"final"``
        regardless of agreement status.

        Yields
        ------
        dict
            A single ``"final"`` event, or nothing if the buffer is empty
            or contains no speech.
        """
        if self._buffer.size == 0:
            logger.debug("Flush: buffer empty, nothing to emit")
            return

        if not self._vad.has_speech(self._buffer):
            logger.debug("Flush: no speech detected in remaining buffer")
            self._reset()
            return

        try:
            text, language = await asyncio.get_event_loop().run_in_executor(
                None, self._transcriber.transcribe, self._buffer
            )
        except Exception as exc:
            logger.exception("Flush transcription error: %s", exc)
            self._reset()
            return

        new_text = self._extract_new(text)
        if new_text:
            yield {
                "type": "final",
                "text": new_text,
                "language": language,
                "t": time.time(),
                "session_id": self.session_id,
            }

        self._reset()

    def reset(self) -> None:
        """Discard all buffered audio and internal state."""
        self._reset()

    # ------------------------------------------------------------------
    # Internal inference pass
    # ------------------------------------------------------------------

    async def _run_pass(self) -> AsyncIterator[dict]:
        """Run one Whisper inference pass and apply agreement policy."""
        if not self._vad.has_speech(self._buffer):
            logger.debug("Pass: VAD found no speech, skipping Whisper")
            return

        try:
            text, language = await asyncio.get_event_loop().run_in_executor(
                None, self._transcriber.transcribe, self._buffer
            )
        except Exception as exc:
            logger.warning("Pass transcription error: %s", exc)
            return

        # Always emit partial for live UI feedback
        new_partial = self._extract_new(text)
        if new_partial:
            yield {
                "type": "partial",
                "text": new_partial,
                "language": language,
                "t": time.time(),
                "session_id": self.session_id,
            }

        # Agreement check
        self._history.append(text)
        if len(self._history) < self._agreement_runs:
            return  # not enough history yet

        stable_prefix = self._stable_prefix(list(self._history))
        if not stable_prefix:
            return

        already_committed = self._committed_chars
        if len(stable_prefix) <= already_committed:
            return  # nothing new to commit

        new_final = stable_prefix[already_committed:].strip()
        if not new_final:
            return

        self._committed_chars = len(stable_prefix)

        # Trim the buffer: drop audio proportional to committed text ratio
        if len(text) > 0:
            committed_ratio = len(stable_prefix) / len(text)
            keep_from = int(len(self._buffer) * committed_ratio)
            self._buffer = self._buffer[keep_from:]
            self._committed_chars = 0  # reset relative to new buffer start

        yield {
            "type": "final",
            "text": new_final,
            "language": language,
            "t": time.time(),
            "session_id": self.session_id,
        }
        self._history.clear()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _extract_new(self, text: str) -> str:
        """Return the portion of *text* that comes after the already-committed prefix."""
        return text[self._committed_chars:].strip()

    @staticmethod
    def _stable_prefix(texts: list[str]) -> str:
        """Find the longest common word-level prefix across all strings.

        Parameters
        ----------
        texts:
            List of full transcription strings.

        Returns
        -------
        str
            The longest word-level prefix identical across every text,
            or ``""`` if there is none.
        """
        if not texts:
            return ""

        tokenised = [t.split() for t in texts]
        min_len = min(len(t) for t in tokenised)

        common_words: list[str] = []
        for i in range(min_len):
            word = tokenised[0][i]
            if all(t[i] == word for t in tokenised[1:]):
                common_words.append(word)
            else:
                break

        return " ".join(common_words)

    def _reset(self) -> None:
        """Reset all mutable state."""
        self._buffer = np.array([], dtype=np.float32)
        self._committed_chars = 0
        self._samples_since_step = 0
        self._history.clear()

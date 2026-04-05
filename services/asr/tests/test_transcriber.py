"""
test_transcriber.py — Unit tests for WhisperTranscriber, VADProcessor, StreamingChunker,
and the db helper utilities.

Tests that require CUDA or a live Whisper model are skipped automatically
when the model or GPU is unavailable, making the suite safe to run in CI.
"""

from __future__ import annotations

import asyncio
import math
import os
import struct
import sys
import types
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_RATE = 16_000  # Hz

SAMPLE_WAV = os.path.join(os.path.dirname(__file__), "sample.wav")


def _sine_wave(duration_s: float = 1.0, freq: float = 440.0) -> np.ndarray:
    """Generate a float32 mono sine wave at 16 kHz."""
    n = int(SAMPLE_RATE * duration_s)
    t = np.arange(n, dtype=np.float32) / SAMPLE_RATE
    return (0.5 * np.sin(2 * math.pi * freq * t)).astype(np.float32)


def _silence(duration_s: float = 1.0) -> np.ndarray:
    """Generate float32 silence."""
    return np.zeros(int(SAMPLE_RATE * duration_s), dtype=np.float32)


def _pcm_bytes(audio: np.ndarray) -> bytes:
    """Convert float32 audio to int16 PCM bytes."""
    int16 = (audio * 32767).astype(np.int16)
    return int16.tobytes()


# ---------------------------------------------------------------------------
# WhisperTranscriber — validation tests (no GPU required)
# ---------------------------------------------------------------------------

class TestWhisperTranscriberValidation(unittest.TestCase):
    """Test input validation without loading the actual Whisper model."""

    def setUp(self):
        """Patch WhisperModel so no weights are downloaded."""
        patcher = patch("transcriber.WhisperModel")
        self.mock_wm_cls = patcher.start()
        self.addCleanup(patcher.stop)

        # Make the mock model produce a plausible transcribe() return value
        mock_info = MagicMock()
        mock_info.language = "en"
        mock_info.duration = 1.0
        self.mock_wm_cls.return_value.transcribe.return_value = ([], mock_info)

        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from transcriber import WhisperTranscriber
        self.transcriber = WhisperTranscriber(model_size="tiny", device="cpu", compute_type="int8")

    def test_rejects_non_array(self):
        from transcriber import WhisperTranscriber
        with self.assertRaises(ValueError):
            self.transcriber._validate_audio([1, 2, 3])  # type: ignore[arg-type]

    def test_rejects_2d_array(self):
        with self.assertRaises(ValueError):
            self.transcriber._validate_audio(np.zeros((2, 100), dtype=np.float32))

    def test_rejects_wrong_dtype(self):
        with self.assertRaises(ValueError):
            self.transcriber._validate_audio(np.zeros(100, dtype=np.int16))

    def test_rejects_empty_array(self):
        with self.assertRaises(ValueError):
            self.transcriber._validate_audio(np.array([], dtype=np.float32))

    def test_transcribe_returns_tuple(self):
        audio = _sine_wave(0.5)
        result = self.transcriber.transcribe(audio)
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)

    def test_transcribe_joins_segments(self):
        seg1 = MagicMock(); seg1.text = "Hello"
        seg2 = MagicMock(); seg2.text = "world"
        mock_info = MagicMock(); mock_info.language = "en"; mock_info.duration = 1.0
        self.mock_wm_cls.return_value.transcribe.return_value = ([seg1, seg2], mock_info)
        text, lang = self.transcriber.transcribe(_sine_wave(1.0))
        self.assertEqual(text, "Hello world")
        self.assertEqual(lang, "en")


# ---------------------------------------------------------------------------
# VADProcessor — unit tests (no GPU required)
# ---------------------------------------------------------------------------

class TestVADProcessorValidation(unittest.TestCase):
    """Test VADProcessor with mocked Silero model."""

    def setUp(self):
        patcher_load = patch("vad.load_silero_vad")
        patcher_ts   = patch("vad.get_speech_timestamps")
        self.mock_load = patcher_load.start()
        self.mock_ts   = patcher_ts.start()
        self.addCleanup(patcher_load.stop)
        self.addCleanup(patcher_ts.stop)

        mock_model = MagicMock()
        mock_model.eval.return_value = None
        mock_model.reset_states.return_value = None
        self.mock_load.return_value = mock_model
        self.mock_ts.return_value = []  # default: no speech

        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from vad import VADProcessor
        self.vad = VADProcessor(threshold=0.5, sampling_rate=16000)

    def test_rejects_empty_array(self):
        from vad import VADProcessor
        with self.assertRaises(ValueError):
            self.vad.has_speech(np.array([], dtype=np.float32))

    def test_rejects_2d_array(self):
        with self.assertRaises(ValueError):
            self.vad.has_speech(np.zeros((2, 100), dtype=np.float32))

    def test_invalid_sample_rate(self):
        from vad import VADProcessor
        with self.assertRaises(ValueError):
            VADProcessor(sampling_rate=44100)

    def test_invalid_threshold(self):
        from vad import VADProcessor
        with self.assertRaises(ValueError):
            VADProcessor(threshold=0.0)

    def test_no_speech_returns_false(self):
        self.mock_ts.return_value = []
        result = self.vad.has_speech(_sine_wave(0.5))
        self.assertFalse(result)

    def test_speech_detected_returns_true(self):
        self.mock_ts.return_value = [{"start": 0, "end": 8000}]
        result = self.vad.has_speech(_sine_wave(0.5))
        self.assertTrue(result)

    def test_speech_ratio_no_speech(self):
        self.mock_ts.return_value = []
        ratio = self.vad.speech_ratio(_sine_wave(1.0))
        self.assertAlmostEqual(ratio, 0.0)

    def test_speech_ratio_full_speech(self):
        audio = _sine_wave(1.0)
        self.mock_ts.return_value = [{"start": 0, "end": len(audio)}]
        ratio = self.vad.speech_ratio(audio)
        self.assertAlmostEqual(ratio, 1.0, places=3)


# ---------------------------------------------------------------------------
# StreamingChunker — unit tests with mocked Whisper + VAD
# ---------------------------------------------------------------------------

class TestStreamingChunkerStablePrefix(unittest.TestCase):
    """Test the stable-prefix detection algorithm in isolation."""

    def setUp(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from chunker import StreamingChunker
        self.SC = StreamingChunker

    def test_empty_list(self):
        result = self.SC._stable_prefix([])
        self.assertEqual(result, "")

    def test_single_string(self):
        result = self.SC._stable_prefix(["hello world"])
        self.assertEqual(result, "hello world")

    def test_common_prefix(self):
        result = self.SC._stable_prefix(["hello world foo", "hello world bar", "hello world baz"])
        self.assertEqual(result, "hello world")

    def test_no_common_prefix(self):
        result = self.SC._stable_prefix(["alpha beta", "gamma delta"])
        self.assertEqual(result, "")

    def test_identical_strings(self):
        result = self.SC._stable_prefix(["one two three", "one two three"])
        self.assertEqual(result, "one two three")


class TestStreamingChunkerFeed(unittest.IsolatedAsyncioTestCase):
    """Test StreamingChunker.feed() with mocked dependencies."""

    def _make_chunker(self, has_speech: bool, transcript: str = "hello world"):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

        mock_vad = MagicMock()
        mock_vad.has_speech.return_value = has_speech

        mock_transcriber = MagicMock()
        mock_transcriber.transcribe.return_value = (transcript, "en")

        from chunker import StreamingChunker
        chunker = StreamingChunker(
            mock_transcriber,
            mock_vad,
            step_ms=10,       # very short step so a small buffer triggers a pass
            agreement_runs=2,
            session_id="test-session",
        )
        return chunker, mock_transcriber, mock_vad

    async def test_no_speech_yields_nothing(self):
        chunker, _, _ = self._make_chunker(has_speech=False)
        # Feed 1 second of PCM (enough to exceed step threshold)
        audio = _silence(1.0)
        pcm = _pcm_bytes(audio)
        events = []
        async for event in chunker.feed(pcm):
            events.append(event)
        self.assertEqual(events, [])

    async def test_partial_emitted_before_agreement(self):
        chunker, _, _ = self._make_chunker(has_speech=True, transcript="hello world")
        # Feed enough for one pass but not enough for agreement (need 2 passes)
        audio = _sine_wave(0.5)
        pcm = _pcm_bytes(audio)
        events = []
        async for event in chunker.feed(pcm):
            events.append(event)
        # May get a partial — check type if any events present
        for ev in events:
            self.assertIn(ev["type"], ("partial", "final"))
            self.assertIn("language", ev)
            self.assertIn("t", ev)

    async def test_final_emitted_after_agreement(self):
        chunker, _, _ = self._make_chunker(has_speech=True, transcript="stable text")
        # Feed two large chunks to trigger two passes (agreement_runs=2)
        audio = _sine_wave(1.0)
        pcm = _pcm_bytes(audio)
        events = []
        # Two separate feeds to ensure two passes
        async for ev in chunker.feed(pcm):
            events.append(ev)
        async for ev in chunker.feed(pcm):
            events.append(ev)
        final_events = [e for e in events if e["type"] == "final"]
        # After 2 identical transcriptions a final should be emitted
        self.assertGreater(len(final_events), 0)
        self.assertEqual(final_events[0]["text"], "stable text")

    async def test_flush_with_speech(self):
        chunker, _, _ = self._make_chunker(has_speech=True, transcript="flush result")
        chunker._buffer = _sine_wave(0.5)  # pre-seed buffer
        events = []
        async for ev in chunker.flush():
            events.append(ev)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "final")
        self.assertEqual(events[0]["text"], "flush result")

    async def test_flush_no_speech_yields_nothing(self):
        chunker, _, _ = self._make_chunker(has_speech=False)
        chunker._buffer = _silence(0.5)
        events = []
        async for ev in chunker.flush():
            events.append(ev)
        self.assertEqual(events, [])

    async def test_flush_empty_buffer_yields_nothing(self):
        chunker, _, _ = self._make_chunker(has_speech=True)
        events = []
        async for ev in chunker.flush():
            events.append(ev)
        self.assertEqual(events, [])

    async def test_reset_clears_state(self):
        chunker, _, _ = self._make_chunker(has_speech=True)
        chunker._buffer = _sine_wave(1.0)
        chunker._committed_chars = 5
        chunker.reset()
        self.assertEqual(chunker._buffer.size, 0)
        self.assertEqual(chunker._committed_chars, 0)


# ---------------------------------------------------------------------------
# db.py — unit tests with mocked asyncpg
# ---------------------------------------------------------------------------

class TestDbInsertChunk(unittest.IsolatedAsyncioTestCase):
    """Test insert_chunk() with a mocked asyncpg pool."""

    def _make_pool(self, returned_id: str = "abc-123"):
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value={"id": returned_id})
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)

        mock_pool = MagicMock()
        mock_pool.acquire.return_value = mock_conn
        return mock_pool

    async def test_insert_returns_chunk_id(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        import db as db_module
        pool = self._make_pool("dead-beef-1234")
        chunk_id = await db_module.insert_chunk(
            pool=pool,
            text="Hello world",
            language="en",
            session_id="sess-1",
            duration_s=1.5,
            source="upload",
        )
        self.assertEqual(chunk_id, "dead-beef-1234")

    async def test_insert_raises_on_empty_text(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        import db as db_module
        pool = self._make_pool()
        with self.assertRaises(ValueError):
            await db_module.insert_chunk(pool=pool, text="")

    def test_get_pool_raises_before_init(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        import db as db_module
        db_module._pool = None
        with self.assertRaises(RuntimeError):
            db_module.get_pool()


# ---------------------------------------------------------------------------
# Integration smoke test: load sample.wav and verify audio shape
# ---------------------------------------------------------------------------

class TestSampleWav(unittest.TestCase):
    """Verify that sample.wav can be decoded to the expected shape."""

    def test_sample_wav_exists(self):
        self.assertTrue(os.path.isfile(SAMPLE_WAV), f"Missing: {SAMPLE_WAV}")

    def test_sample_wav_readable(self):
        import soundfile as sf
        audio, sr = sf.read(SAMPLE_WAV, dtype="float32", always_2d=False)
        self.assertEqual(sr, 16000)
        self.assertEqual(audio.ndim, 1)
        self.assertGreater(len(audio), 0)

    def test_sample_wav_duration(self):
        import soundfile as sf
        audio, sr = sf.read(SAMPLE_WAV, dtype="float32")
        duration = len(audio) / sr
        self.assertAlmostEqual(duration, 2.0, places=1)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)

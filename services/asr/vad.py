"""
vad.py — Silero-VAD wrapper.

Provides :class:`VADProcessor`, a lightweight facade around the
``silero-vad`` v5 Python package that answers a single question:
*does this audio chunk contain speech?*
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import torch
from silero_vad import load_silero_vad, get_speech_timestamps

logger = logging.getLogger(__name__)


class VADProcessor:
    """Speech / silence detector powered by Silero VAD v5.

    The Silero model is stateful on longer streams but :meth:`has_speech`
    intentionally resets the hidden state before every call so that each
    invocation is independent (suitable for the chunker's sliding-window
    approach).

    Parameters
    ----------
    threshold:
        Posterior probability above which a frame is considered speech.
        Lower values -> more sensitive (may pick up noise).
        Higher values -> more conservative. Default ``0.5``.
    sampling_rate:
        Expected sample rate of all incoming audio. Silero VAD supports
        ``8000`` and ``16000`` Hz. Default ``16000``.
    """

    _SUPPORTED_RATES = (8000, 16000)

    def __init__(
        self,
        threshold: float = 0.5,
        sampling_rate: int = 16000,
    ) -> None:
        if sampling_rate not in self._SUPPORTED_RATES:
            raise ValueError(
                f"sampling_rate must be one of {self._SUPPORTED_RATES}, "
                f"got {sampling_rate}"
            )
        if not (0.0 < threshold <= 1.0):
            raise ValueError(f"threshold must be in (0, 1], got {threshold}")

        self.threshold = threshold
        self.sr = sampling_rate

        logger.info("Loading Silero VAD model ...")
        self.model = load_silero_vad()
        self.model.eval()
        logger.info("Silero VAD ready (threshold=%.2f, sr=%d)", threshold, sampling_rate)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def has_speech(self, audio_np: np.ndarray) -> bool:
        """Return ``True`` if the audio array contains at least one speech segment.

        Parameters
        ----------
        audio_np:
            1-D NumPy array, dtype ``float32``, values in ``[-1, 1]``,
            sampled at :attr:`sr` Hz.

        Returns
        -------
        bool
            ``True`` when Silero detects at least one speech timestamp;
            ``False`` for silence-only or sub-threshold audio.

        Raises
        ------
        ValueError
            If *audio_np* is not 1-D float32 or is empty.
        """
        self._validate_audio(audio_np)
        self.model.reset_states()  # stateless per-call behaviour

        tensor = torch.from_numpy(audio_np).float()
        try:
            timestamps = get_speech_timestamps(
                tensor,
                self.model,
                threshold=self.threshold,
                sampling_rate=self.sr,
            )
        except Exception as exc:
            logger.warning("VAD inference failed: %s - treating as no speech", exc)
            return False

        result = len(timestamps) > 0
        logger.debug("VAD: %d speech segment(s) found", len(timestamps))
        return result

    def speech_ratio(self, audio_np: np.ndarray) -> float:
        """Return the fraction of the audio that contains speech (0.0 to 1.0).

        Parameters
        ----------
        audio_np:
            1-D float32 NumPy array at :attr:`sr` Hz.

        Returns
        -------
        float
            Ratio of speech samples to total samples.
        """
        self._validate_audio(audio_np)
        self.model.reset_states()

        tensor = torch.from_numpy(audio_np).float()
        try:
            timestamps = get_speech_timestamps(
                tensor,
                self.model,
                threshold=self.threshold,
                sampling_rate=self.sr,
                return_seconds=False,
            )
        except Exception as exc:
            logger.warning("VAD speech_ratio failed: %s", exc)
            return 0.0

        speech_samples = sum(ts["end"] - ts["start"] for ts in timestamps)
        ratio = speech_samples / max(len(audio_np), 1)
        logger.debug("VAD speech ratio=%.3f", ratio)
        return ratio

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_audio(audio_np: np.ndarray) -> None:
        """Raise *ValueError* for obviously invalid audio arrays."""
        if not isinstance(audio_np, np.ndarray):
            raise ValueError("audio_np must be a NumPy ndarray")
        if audio_np.ndim != 1:
            raise ValueError(
                f"audio_np must be 1-D, got shape {audio_np.shape}"
            )
        if audio_np.size == 0:
            raise ValueError("audio_np is empty")
        if audio_np.dtype != np.float32:
            logger.debug(
                "VAD received dtype=%s - will be cast to float32 by torch",
                audio_np.dtype,
            )

"""
transcriber.py — WhisperTranscriber wrapping faster-whisper.

Provides a thin, thread-safe facade around ``faster_whisper.WhisperModel``
so that the rest of the codebase works with plain NumPy arrays and does not
need to know about faster-whisper internals.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Optional

import numpy as np
from faster_whisper import WhisperModel

logger = logging.getLogger(__name__)


class WhisperTranscriber:
    """Wrapper around :class:`faster_whisper.WhisperModel`.

    The underlying model is loaded once at construction time and reused for
    every call to :meth:`transcribe`.  Because the CTranslate2 backend is
    **not** thread-safe for concurrent *inference*, a ``threading.Lock`` is
    held for the duration of each transcription call.

    Parameters
    ----------
    model_size:
        Name of the Whisper model variant to load (default ``"large-v3"``).
        Can be overridden by the ``WHISPER_MODEL`` environment variable.
    device:
        ``"cuda"`` (default) or ``"cpu"``.
        Can be overridden by the ``WHISPER_DEVICE`` environment variable.
    compute_type:
        CTranslate2 quantisation type (default ``"float16"``).
        Use ``"int8"`` for CPU-only deployments.
        Can be overridden by the ``WHISPER_COMPUTE_TYPE`` environment variable.
    """

    def __init__(
        self,
        model_size: str = "large-v3",
        device: str = "cuda",
        compute_type: str = "float16",
    ) -> None:
        model_size = os.getenv("WHISPER_MODEL", model_size)
        device = os.getenv("WHISPER_DEVICE", device)
        compute_type = os.getenv("WHISPER_COMPUTE_TYPE", compute_type)

        logger.info(
            "Loading Whisper model '%s' on device='%s' compute_type='%s'",
            model_size,
            device,
            compute_type,
        )
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type

        self.model = WhisperModel(
            model_size,
            device=device,
            compute_type=compute_type,
        )
        self._lock = threading.Lock()
        logger.info("Whisper model ready")

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def transcribe(
        self,
        audio_np: np.ndarray,
        language: Optional[str] = None,
    ) -> tuple[str, str]:
        """Transcribe a mono 16 kHz float32 audio array.

        Parameters
        ----------
        audio_np:
            1-D NumPy array, dtype ``float32``, values in ``[-1, 1]``,
            sampled at 16 000 Hz.
        language:
            Optional BCP-47 language hint (e.g. ``"ru"`` or ``"en"``).
            When ``None`` Whisper performs automatic language detection.

        Returns
        -------
        tuple[str, str]
            ``(text, detected_language)`` where *text* is the full
            concatenation of all Whisper segments and *detected_language*
            is the ISO-639-1 code Whisper decided on.

        Raises
        ------
        ValueError
            If *audio_np* is empty or not 1-D float32.
        RuntimeError
            On any error raised by the underlying CTranslate2 model.
        """
        self._validate_audio(audio_np)

        with self._lock:
            try:
                segments, info = self.model.transcribe(
                    audio_np,
                    language=language,
                    vad_filter=True,
                    beam_size=5,
                )
                # Materialise the generator inside the lock so that the model
                # is not re-entered before we are done reading segments.
                text = " ".join(s.text for s in segments).strip()
            except Exception as exc:
                logger.exception("Whisper transcription failed: %s", exc)
                raise RuntimeError(f"Transcription error: {exc}") from exc

        detected = info.language
        logger.debug(
            "Transcribed %.2f s -> lang=%s text=%r",
            info.duration,
            detected,
            text[:80],
        )
        return text, detected

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
        if audio_np.dtype != np.float32:
            raise ValueError(
                f"audio_np must be float32, got dtype={audio_np.dtype}"
            )
        if audio_np.size == 0:
            raise ValueError("audio_np is empty")

"""
main.py — FastAPI application entry point for the ASR microservice.

Exposes three endpoints:

- ``WS  /stream``      — real-time PCM streaming with partial / final events
- ``POST /transcribe`` — one-shot file upload (WAV / MP3 / OGG)
- ``GET  /health``     — liveness probe

Environment variables
---------------------
DATABASE_URL
    asyncpg-compatible PostgreSQL DSN.
    Default: ``postgresql://asr:asr@localhost:5432/asr``
WHISPER_MODEL
    Faster-Whisper model name. Default: ``large-v3``
WHISPER_DEVICE
    ``cuda`` or ``cpu``. Default: ``cuda``
WHISPER_COMPUTE_TYPE
    CTranslate2 compute type. Default: ``float16``
"""

from __future__ import annotations

import io
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Optional

import numpy as np
import soundfile as sf
import librosa
from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import JSONResponse

import db
from chunker import StreamingChunker
from transcriber import WhisperTranscriber
from vad import VADProcessor

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Singleton ML models (loaded once, shared across requests)
# ---------------------------------------------------------------------------
_transcriber: Optional[WhisperTranscriber] = None
_vad: Optional[VADProcessor] = None

TARGET_SR = 16_000  # Hz — both Whisper and Silero VAD require 16 kHz


# ---------------------------------------------------------------------------
# Application lifespan (startup / shutdown)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load ML models and connect to PostgreSQL on startup; clean up on shutdown."""
    global _transcriber, _vad

    # 1. Load Whisper
    logger.info("Initialising WhisperTranscriber ...")
    _transcriber = WhisperTranscriber(
        model_size=os.getenv("WHISPER_MODEL", "large-v3"),
        device=os.getenv("WHISPER_DEVICE", "cuda"),
        compute_type=os.getenv("WHISPER_COMPUTE_TYPE", "float16"),
    )

    # 2. Load Silero VAD
    logger.info("Initialising VADProcessor ...")
    _vad = VADProcessor(threshold=0.5, sampling_rate=TARGET_SR)

    # 3. PostgreSQL pool + DDL
    logger.info("Connecting to PostgreSQL ...")
    pool = await db.create_pool()
    await db.create_tables(pool)

    logger.info("ASR service ready")
    yield

    # Shutdown
    logger.info("Shutting down ASR service ...")
    await db.close_pool()


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="ASR Microservice",
    description="Automatic Speech Recognition via faster-whisper (Jarvis Phase 1)",
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _get_transcriber() -> WhisperTranscriber:
    if _transcriber is None:
        raise RuntimeError("WhisperTranscriber not initialised")
    return _transcriber


def _get_vad() -> VADProcessor:
    if _vad is None:
        raise RuntimeError("VADProcessor not initialised")
    return _vad


def _load_audio_bytes(data: bytes, filename: str = "") -> tuple[np.ndarray, float]:
    """Decode audio bytes to a mono float32 16 kHz NumPy array.

    Parameters
    ----------
    data:
        Raw audio file bytes (WAV, MP3, OGG, FLAC, ...).
    filename:
        Optional original filename — used only for logging.

    Returns
    -------
    tuple[np.ndarray, float]
        ``(audio_float32, duration_seconds)``

    Raises
    ------
    HTTPException 422
        If the file cannot be decoded or is empty.
    """
    try:
        audio, sr = sf.read(io.BytesIO(data), dtype="float32", always_2d=False)
    except Exception:
        # soundfile cannot handle MP3 — fall back to librosa
        try:
            audio, sr = librosa.load(io.BytesIO(data), sr=None, mono=True)
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Cannot decode audio file '{filename}': {exc}",
            ) from exc

    if audio.ndim > 1:
        audio = audio.mean(axis=1)  # stereo -> mono

    if sr != TARGET_SR:
        audio = librosa.resample(audio, orig_sr=sr, target_sr=TARGET_SR)

    if audio.size == 0:
        raise HTTPException(status_code=422, detail="Audio file is empty")

    duration_s = len(audio) / TARGET_SR
    return audio.astype(np.float32), duration_s


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------

@app.get("/health", summary="Liveness probe")
async def health() -> JSONResponse:
    """Return service health status.

    Returns
    -------
    JSON
        ``{"status": "ok", "model": "<model_size>", "device": "<device>"}``
    """
    tr = _get_transcriber()
    return JSONResponse(
        {
            "status": "ok",
            "model": tr.model_size,
            "device": tr.device,
        }
    )


# ---------------------------------------------------------------------------
# POST /transcribe
# ---------------------------------------------------------------------------

@app.post("/transcribe", summary="One-shot file transcription")
async def transcribe_file(
    file: UploadFile = File(..., description="Audio file (WAV, MP3, OGG, FLAC)"),
    session_id: Optional[str] = Form(None, description="Optional session identifier"),
    language: Optional[str] = Form(None, description="BCP-47 language hint, e.g. 'ru'"),
) -> JSONResponse:
    """Transcribe an uploaded audio file and return the result.

    The file is decoded server-side to 16 kHz mono float32 PCM, passed
    through Whisper, and the result is logged to PostgreSQL.

    Parameters
    ----------
    file:
        Multipart audio file upload.
    session_id:
        Optional caller-supplied session tag stored in the DB row.
    language:
        Optional language hint forwarded to Whisper.

    Returns
    -------
    JSON
        ``{"text": "...", "language": "ru", "duration_s": 4.2, "chunk_id": "uuid"}``

    Raises
    ------
    HTTPException 422
        If the file cannot be decoded or Whisper fails.
    """
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=422, detail="Uploaded file is empty")

    audio, duration_s = _load_audio_bytes(raw, filename=file.filename or "")

    try:
        text, detected_lang = _get_transcriber().transcribe(audio, language=language)
    except Exception as exc:
        logger.exception("Transcription failed for file '%s': %s", file.filename, exc)
        raise HTTPException(status_code=500, detail=f"Transcription failed: {exc}") from exc

    pool = db.get_pool()
    chunk_id = await db.insert_chunk(
        pool=pool,
        text=text or "(empty)",
        language=detected_lang,
        session_id=session_id,
        duration_s=duration_s,
        source="upload",
    )

    logger.info(
        "POST /transcribe - file=%s lang=%s dur=%.2fs chunk_id=%s",
        file.filename,
        detected_lang,
        duration_s,
        chunk_id,
    )

    return JSONResponse(
        {
            "text": text,
            "language": detected_lang,
            "duration_s": round(duration_s, 3),
            "chunk_id": chunk_id,
        }
    )


# ---------------------------------------------------------------------------
# WS /stream
# ---------------------------------------------------------------------------

@app.websocket("/stream")
async def stream_audio(websocket: WebSocket):
    """Stream raw PCM frames and receive partial / final transcription events.

    Protocol
    --------
    **Client -> Server**

    * Binary frames: raw PCM bytes, 16 kHz mono int16 LE.
    * JSON text frame ``{"type": "end"}`` to signal end-of-stream.

    **Server -> Client**

    * ``{"type": "partial", "text": "...", "language": "ru", "t": <epoch>}``
    * ``{"type": "final",   "text": "...", "language": "ru", "t": <epoch>, "chunk_id": "uuid"}``
    * ``{"type": "error",   "detail": "..."}`` on unrecoverable errors.

    Errors
    ------
    Any exception during frame processing is caught, logged, and sent back
    to the client as an ``"error"`` event; the connection is then closed.
    """
    await websocket.accept()
    session_id = str(uuid.uuid4())
    logger.info("WS /stream - new session %s", session_id)

    chunker = StreamingChunker(
        _get_transcriber(),
        _get_vad(),
        session_id=session_id,
    )
    pool = db.get_pool()

    try:
        while True:
            message = await websocket.receive()

            # ---- Binary PCM frame ----------------------------------------
            if "bytes" in message and message["bytes"] is not None:
                raw_bytes: bytes = message["bytes"]
                async for event in chunker.feed(raw_bytes):
                    if event["type"] == "final":
                        chunk_id = await db.insert_chunk(
                            pool=pool,
                            text=event["text"],
                            language=event.get("language"),
                            session_id=session_id,
                            duration_s=None,
                            source="stream",
                        )
                        event["chunk_id"] = chunk_id
                    await websocket.send_text(json.dumps(event, ensure_ascii=False))

            # ---- Text / JSON control frame --------------------------------
            elif "text" in message and message["text"] is not None:
                try:
                    ctrl = json.loads(message["text"])
                except json.JSONDecodeError:
                    logger.warning("WS %s - received non-JSON text frame", session_id)
                    continue

                if ctrl.get("type") == "end":
                    logger.info("WS %s - received end signal, flushing ...", session_id)
                    async for event in chunker.flush():
                        if event["type"] == "final":
                            chunk_id = await db.insert_chunk(
                                pool=pool,
                                text=event["text"],
                                language=event.get("language"),
                                session_id=session_id,
                                duration_s=None,
                                source="stream",
                            )
                            event["chunk_id"] = chunk_id
                        await websocket.send_text(json.dumps(event, ensure_ascii=False))
                    await websocket.close()
                    break

    except WebSocketDisconnect:
        logger.info("WS %s - client disconnected", session_id)
    except Exception as exc:
        logger.exception("WS %s - unexpected error: %s", session_id, exc)
        try:
            await websocket.send_text(
                json.dumps({"type": "error", "detail": str(exc)}, ensure_ascii=False)
            )
            await websocket.close(code=1011)
        except Exception:
            pass  # connection already gone

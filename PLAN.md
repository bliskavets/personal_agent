# Jarvis — Personal Agent: Build Plan (v2)

## Addressing Architecture Concerns

### 1. Agent is CLIENT-side, not server-side
The PRD explicitly says: *"по идее он должен быть на стороне клиента (то есть на моей стороне)"*.
The agent must run on the user's desktop because it needs to:
- Open the user's browser
- Control the desktop (keyboard, mouse, windows)
- Run commands in the user's terminal
- Access local files

**Server-side** = only stateless, compute-heavy services (ASR, DB for persistence)  
**Client-side** = everything with agency (reasoning, tool execution, audio I/O)

### 2. Build on openclaw, not from scratch
openclaw (running on this machine) already provides:
- ✅ **Telegram bot** — full implementation via grammy, multi-account, voice messages, streaming replies
- ✅ **TTS** — abstracted providers: ElevenLabs, OpenAI TTS, Edge TTS; streaming, format selection
- ✅ **Session management** — session store, context window management, message history
- ✅ **Memory** — QMD vector embeddings, semantic search, MCP bridge
- ✅ **LLM provider abstraction** — OpenAI, Anthropic, Google, OpenRouter, local models
- ✅ **Plugin/channel system** — adapter pattern, registry, config-driven
- ✅ **Daemon pattern** — systemd/launchd/schtasks lifecycle management
- ✅ **Config system** — YAML, centralised, validated with Zod

**We add**: A **voice channel plugin** for openclaw + a standalone **ASR service**.

---

## Revised Architecture

```
╔══════════════════════ USER'S DESKTOP (CLIENT) ══════════════════════╗
║                                                                      ║
║  ┌─────────────────────────────────────────────────────────────┐    ║
║  │               openclaw daemon  (already exists)              │    ║
║  │                                                              │    ║
║  │  ┌──────────────────┐   ┌──────────────────────────────┐   │    ║
║  │  │  Telegram channel│   │  Voice channel plugin (NEW)   │   │    ║
║  │  │  (already works) │   │                              │   │    ║
║  │  └──────────────────┘   │  wakeword.py                 │   │    ║
║  │                         │    ↓ "Jarvis"                 │   │    ║
║  │  ┌──────────────────┐   │  mic_streamer.py             │   │    ║
║  │  │  TTS provider    │   │    ↓ WebSocket chunks         │   │    ║
║  │  │  (already works) │◄──│  ASR client                  │   │    ║
║  │  └──────────────────┘   │    ↓ transcript               │   │    ║
║  │                         │  intent_filter.py             │   │    ║
║  │  ┌──────────────────┐   │    ↓ confirmed request        │   │    ║
║  │  │  Agent loop      │◄──│  → openclaw message queue     │   │    ║
║  │  │  (openclaw core) │   └──────────────────────────────┘   │    ║
║  │  │                  │                                        │    ║
║  │  │  MCP clients:    │                                        │    ║
║  │  │  ┌────────────┐  │                                        │    ║
║  │  │  │ playwright │  │  ← controls YOUR browser               │    ║
║  │  │  │  -mcp      │  │                                        │    ║
║  │  │  ├────────────┤  │                                        │    ║
║  │  │  │ desktop-   │  │  ← controls YOUR terminal/files        │    ║
║  │  │  │ commander  │  │                                        │    ║
║  │  │  ├────────────┤  │                                        │    ║
║  │  │  │ filesystem │  │  ← reads/writes YOUR files             │    ║
║  │  │  └────────────┘  │                                        │    ║
║  │  └──────────────────┘                                        │    ║
║  └─────────────────────────────────────────────────────────────┘    ║
║                          │  WebSocket                               ║
╚══════════════════════════│══════════════════════════════════════════╝
                           │
╔══════════════════════ SERVER (Docker Compose) ═══════════════════════╗
║                           │                                          ║
║  ┌────────────────────────▼──────────────────────────────────────┐  ║
║  │  asr-service                                                  │  ║
║  │  faster-whisper (whisper-small / large-v3)                    │  ║
║  │  + silero-vad v6                                              │  ║
║  │  FastAPI  WS /stream   POST /transcribe                       │  ║
║  └────────────────────────┬──────────────────────────────────────┘  ║
║                            │ writes chunks                           ║
║  ┌─────────────────────────▼─────────────────────────────────────┐  ║
║  │  PostgreSQL                                                    │  ║
║  │  • asr_chunks  (transcription log)                            │  ║
║  │  • interaction_history  (for fine-tuning later)               │  ║
║  └───────────────────────────────────────────────────────────────┘  ║
║                                                                      ║
╚══════════════════════════════════════════════════════════════════════╝
```

**Key difference from v1:**
- Agent runs on **client** inside openclaw — has direct access to desktop MCP tools
- Server only runs ASR (compute-heavy) and DB (persistence)
- Telegram is already fully working via openclaw — **zero new code needed there**
- TTS is already working via openclaw — **zero new code needed there**
- Response playback: openclaw handles TTS audio, client plays it via existing sounddevice/speaker

---

## What openclaw Already Gives Us (free)

| Feature | openclaw component | Status |
|---|---|---|
| Telegram bot | `src/telegram/` (grammy) | ✅ Running now |
| TTS playback | `src/tts/` (ElevenLabs/OpenAI/Edge) | ✅ Working |
| LLM calls | `src/providers/` (OpenRouter, Anthropic, etc.) | ✅ Working |
| Session history | `src/sessions/` | ✅ Working |
| Vector memory | `src/memory/` (QMD) | ✅ Working |
| Config system | YAML + Zod validation | ✅ Working |
| Daemon lifecycle | `src/daemon/` (systemd/launchd) | ✅ Working |
| MCP tool integration | via `mcporter` bridge | ✅ Working |

---

## Technology Stack

### ASR Service (Docker, server-side)
| Concern | Choice | Why |
|---|---|---|
| Transcription | **faster-whisper** | 4× faster than stock Whisper, INT8, CPU+GPU |
| Model | `whisper-small` (CPU) → `large-v3` (GPU) | Best real-time speed/accuracy |
| VAD | **silero-vad v6** | Built into faster-whisper, 4× fewer errors than webrtcvad |
| Streaming | **whisper-streaming** (UFAL) chunking strategy | 3.3s latency, local agreement policy |
| API | **FastAPI + WebSocket** | Real-time chunk streaming |
| Docker base | `python:3.12-slim` + ctranslate2 | Small image |

### Voice Channel Plugin (client-side, extends openclaw)
| Concern | Choice | Why |
|---|---|---|
| Wake word | **openWakeWord** | MIT, <0.5 false-accepts/hr, custom "Jarvis" training |
| Mic capture | **sounddevice** | NumPy-native, cross-platform, lowest latency |
| Intent filter | **Claude Haiku** (OpenRouter, already in openclaw) | Cheap, fast yes/no classification |
| Language | **TypeScript** (plugin) + **Python** (ASR client) | Match openclaw's codebase |

### Agent + MCP tools (client-side, via openclaw)
| Concern | Choice | Why |
|---|---|---|
| LLM | **Claude Sonnet 4.6** (already in openclaw config) | Best reasoning for desktop tasks |
| Browser | **microsoft/playwright-mcp** | Accessibility-tree based, no vision needed |
| Terminal/FS | **DesktopCommanderMCP** | Terminal + filesystem in one MCP server |
| Desktop fallback | **pyautogui** | Cross-platform, covers MCP gaps |
| Protocol | **MCP** via openclaw's mcporter | Already integrated |

### Storage (server-side Docker)
| Concern | Choice | Why |
|---|---|---|
| Primary DB | **PostgreSQL 16** | ASR chunks + history + LISTEN/NOTIFY |
| Queue | **PostgreSQL LISTEN/NOTIFY** | No extra service, sufficient for personal scale |
| Migrations | **Alembic** | Standard Python migration tool |

---

## Project Structure

```
jarvis/                          (this repo)
│
├── services/
│   └── asr/                     ← NEW: Docker microservice
│       ├── Dockerfile
│       ├── main.py              FastAPI + WebSocket
│       ├── transcriber.py       faster-whisper + silero-vad
│       ├── chunker.py           whisper-streaming chunking logic
│       └── tests/
│
├── client/                      ← NEW: runs on user's desktop
│   ├── wakeword.py              openWakeWord "Jarvis" detector
│   ├── mic_streamer.py          sounddevice → WebSocket → ASR
│   ├── intent_filter.py         LLM: "is this a command?"
│   └── main.py                  orchestrates the above
│
├── openclaw-plugin/             ← NEW: voice channel plugin for openclaw
│   ├── package.json
│   ├── src/
│   │   ├── index.ts             ChannelPlugin registration
│   │   ├── setup.ts             config setup adapter
│   │   ├── messaging.ts         inbound: receives ASR transcripts as messages
│   │   └── outbound.ts          outbound: TTS via openclaw's existing tts module
│   └── README.md
│
├── db/
│   ├── migrations/              Alembic migrations
│   └── schema.sql               Reference schema
│
├── docker-compose.yml           asr + postgres services only
├── config/
│   └── default.yaml             centralised config (read by both client + server)
└── Makefile
```

---

## Build Phases

### Phase 1: ASR Service ← START HERE
**Goal:** Audio in via WebSocket → transcription chunks out

1. `services/asr/transcriber.py` — faster-whisper + silero-vad, streaming chunk output
2. `services/asr/main.py` — FastAPI: `WS /stream` (real-time), `POST /transcribe` (file)
3. `services/asr/Dockerfile` — python:3.12-slim, ctranslate2, model pre-downloaded
4. `docker-compose.yml` — `asr` + `postgres` services with health checks
5. Tests — POST a WAV file → assert transcript returned

**Resources:**
- https://github.com/SYSTRAN/faster-whisper
- `openai/whisper-small` from HuggingFace
- https://github.com/ufal/whisper_streaming (chunking strategy reference)

---

### Phase 2: Desktop Client (voice capture)
**Goal:** Say "Jarvis" → audio streamed to ASR → transcript returned

6. `client/wakeword.py` — openWakeWord detector, activates mic on trigger
7. `client/mic_streamer.py` — sounddevice capture → WebSocket stream to ASR service
8. `client/intent_filter.py` — sliding window of chunks → LLM: is this a command?
9. `client/main.py` — ties it together, pushes confirmed requests to openclaw

**Resources:**
- https://github.com/dscripka/openWakeWord
- sounddevice docs

---

### Phase 3: openclaw Voice Channel Plugin
**Goal:** Voice request appears in openclaw as a regular message → agent processes it → TTS response played back

10. `openclaw-plugin/src/index.ts` — register voice as a `ChannelPlugin`
11. `openclaw-plugin/src/messaging.ts` — receive inbound from `client/intent_filter.py`
12. `openclaw-plugin/src/outbound.ts` — wrap openclaw's existing TTS system for playback
13. Wire MCP servers: playwright-mcp + desktop-commander into openclaw config

**Resources:**
- openclaw `src/channels/` — channel plugin interface
- openclaw `src/tts/` — existing TTS, reuse as-is
- https://github.com/microsoft/playwright-mcp
- https://github.com/wonderwhy-er/DesktopCommanderMCP

---

### Phase 4: Interaction History
**Goal:** All agent actions stored in PostgreSQL for future fine-tuning

14. `db/schema.sql` — `sessions`, `asr_chunks`, `messages`, `agent_actions` tables
15. Alembic migrations
16. History writer — after each agent turn, dump full trace to DB

---

### Phase 5: Telemetry (future)
17. Prometheus metrics: cost, request count, ASR chunks/day, latency
18. Grafana dashboard JSON
19. Alert rules (daily cost threshold)

---

## Configuration (centralised, single file)

```yaml
# config/default.yaml

asr:
  url: ws://localhost:8765/stream     # ASR service WebSocket
  model: openai/whisper-small         # HuggingFace model ID
  device: cpu                         # cpu | cuda
  language: ru                        # primary language
  vad_threshold: 0.5
  chunk_duration_ms: 500

wakeword:
  model: jarvis                       # openWakeWord model name
  threshold: 0.5
  activation_phrase: "Джарвис"

intent:
  window_seconds: 10                  # sliding window for intent classification
  llm: anthropic/claude-haiku-4.5    # cheap + fast for yes/no

# agent config lives in openclaw's existing config
# (openclaw already handles LLM, MCP, TTS, Telegram)

db:
  url: postgresql://jarvis:jarvis@localhost:5432/jarvis

telegram:
  # configured in openclaw's existing config (~/.openclaw/openclaw.json)
  # no changes needed
```

---

## Questions for You

1. **GPU?** Does your desktop/server have a CUDA GPU?
   - Yes → `whisper-large-v3` (highest accuracy) + Coqui XTTS for TTS
   - No → `whisper-small` (good for Russian) + Piper TTS (CPU-native)

2. **Language:** Russian-first or English-first? (or both equally?)
   - Affects VAD thresholds and Whisper language config

3. **Wake word language:** "Jarvis" (English) or "Джарвис" (Russian pronunciation)?
   - openWakeWord has English "hey jarvis" built-in; Russian needs custom training (1-2h)

4. **Client OS:** Linux/Mac/Windows?
   - Affects desktop automation: xdotool (Linux), AppleScript (Mac), pyautogui (all)

5. **openclaw location:** Is the openclaw source at `/home/mle/openclaw` the same instance currently running? Can we modify it, or should the voice plugin be a separate npm package installed into it?

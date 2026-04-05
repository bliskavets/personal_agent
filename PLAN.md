# Jarvis — Personal Agent: Build Plan

## What We're Building

A Jarvis-like personal assistant that:
- Listens in the background via voice ("Джарвис, ...") or Telegram
- Transcribes speech → detects intent → dispatches to an agent
- Agent thinks, calls tools (browser, terminal, filesystem) via MCP
- Composes a spoken response via TTS and plays it back
- Everything runs in Docker Compose, modular and testable

---

## Architecture

```
┌─────────────────── CLIENT (desktop) ────────────────────────┐
│                                                              │
│  Microphone                                                  │
│      │                                                       │
│      ▼                                                       │
│  [openWakeWord]  ─── detects "Jarvis" ──►  [ASR Client]     │
│                                                │             │
│                                          streams audio        │
│                                                │             │
└────────────────────────────────────────────────│─────────────┘
                                                 │  WebSocket
┌─────────────────── SERVER (docker-compose) ────▼─────────────┐
│                                                               │
│  ┌──────────────────────────────────────────────────────┐    │
│  │  asr-service  (faster-whisper + silero-vad)           │    │
│  │  POST /transcribe  WS /stream                         │    │
│  └───────────────────────┬──────────────────────────────┘    │
│                           │ writes chunks                     │
│                           ▼                                   │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  PostgreSQL                                            │  │
│  │  • asr_chunks   • messages   • sessions  • events     │  │
│  └───────┬──────────────────────────────────────┬────────┘  │
│           │ LISTEN/NOTIFY                        │           │
│           ▼                                      │           │
│  ┌─────────────────────┐              ┌──────────▼─────────┐ │
│  │  intent-service     │              │  memory-service    │ │
│  │  reads chunks,      │              │  stores & retrieves│ │
│  │  LLM classifies     │              │  interaction hist. │ │
│  │  → posts to         │              └────────────────────┘ │
│  │    message queue    │                                     │
│  └──────────┬──────────┘                                     │
│             │                                                 │
│             ▼                                                 │
│  ┌──────────────────────────────────────────────────────┐    │
│  │  agent-service  (client-side, MCP + Claude API)       │    │
│  │  • loads message from queue                           │    │
│  │  • reasons + calls MCP tools                          │    │
│  │  • writes response to message queue                   │    │
│  │                                                       │    │
│  │  MCP Servers (stdio):                                 │    │
│  │    playwright-mcp    (browser)                        │    │
│  │    desktop-commander (terminal + filesystem)          │    │
│  │    filesystem-mcp    (Anthropic official)             │    │
│  └──────────────────────┬────────────────────────────────┘   │
│                          │                                    │
│                          ▼                                    │
│  ┌───────────────────────────────────────────────────────┐   │
│  │  response-composer                                     │   │
│  │  reads text responses → calls TTS → sends audio back  │   │
│  └───────────────────────────────────────────────────────┘   │
│                                                               │
│  ┌───────────────────────────────────────────────────────┐   │
│  │  tts-service  (Piper TTS — CPU; Coqui XTTS — GPU)     │   │
│  │  POST /synthesize   streaming chunks                   │   │
│  └───────────────────────────────────────────────────────┘   │
│                                                               │
│  ┌───────────────────────────────────────────────────────┐   │
│  │  telegram-bot  (aiogram)                               │   │
│  │  receives text + voice → feeds into message queue     │   │
│  │  plays back TTS audio or sends text replies            │   │
│  └───────────────────────────────────────────────────────┘   │
│                                                               │
│  ┌───────────────────────────────────────────────────────┐   │
│  │  telemetry  (future)                                   │   │
│  │  Prometheus + Grafana                                  │   │
│  │  metrics: cost, requests/day, ASR chunks, latency      │   │
│  └───────────────────────────────────────────────────────┘   │
└───────────────────────────────────────────────────────────────┘
```

---

## Technology Stack

### ASR Service
| Concern | Choice | Reason |
|---|---|---|
| Transcription engine | **faster-whisper** (SYSTRAN) | 4× faster than OpenAI Whisper, same accuracy, INT8 quantization, CPU+GPU |
| Model | `whisper-small` (CPU) / `whisper-large-v3` (GPU) | Best speed/accuracy tradeoff for real-time |
| VAD | **silero-vad v6** | Built into faster-whisper, 4× fewer errors than webrtcvad |
| Streaming | **whisper-streaming** (UFAL) | 3.3s latency on long-form, local agreement policy |
| Service layer | **FastAPI + WebSocket** | Real-time chunk streaming, easy to test |
| Wake word | **openWakeWord** | Open-source, MIT, <0.5 false-accepts/hour, trains custom "Jarvis" |
| Mic capture (client) | **sounddevice** | NumPy-native, cross-platform, lowest latency |
| Docker base | `python:3.12-slim` + ctranslate2 wheels | Minimal image, fast startup |

### Message Bus & Storage
| Concern | Choice | Reason |
|---|---|---|
| Primary DB | **PostgreSQL 16** | ASR chunks + messages + history + LISTEN/NOTIFY for events |
| Message queue | **PostgreSQL LISTEN/NOTIFY** via `asyncpg` | No extra service, sufficient for personal scale |
| Heavy queue (future) | **Redis Streams** | If multi-user or high concurrency needed |
| Migrations | **Alembic** | Standard Python ORM migration tool |

### Intent Service
| Concern | Choice | Reason |
|---|---|---|
| LLM for classification | **Claude Haiku 4.5** (via OpenRouter) | Cheapest, fast, sufficient for yes/no intent detection |
| Future replacement | Local small LLM (e.g., Qwen-2.5-0.5B via Ollama) | Zero cost, private |

### Agent Service
| Concern | Choice | Reason |
|---|---|---|
| LLM | **Claude Sonnet 4.6** (via OpenRouter) | Best reasoning for complex desktop tasks |
| Tool protocol | **MCP** (Model Context Protocol) | Native Anthropic support, extensible |
| Browser | **microsoft/playwright-mcp** | Accessibility-tree based, no vision needed |
| Terminal / FS | **DesktopCommanderMCP** | Terminal + filesystem in one, active maintenance |
| MCP client | **Anthropic Python SDK** + `mcp` package | Direct, no framework overhead |
| Desktop fallback | **pyautogui** | Cross-platform, covers gaps MCP doesn't |

### TTS & Playback
| Concern | Choice | Reason |
|---|---|---|
| TTS engine (CPU) | **Piper TTS** (rhasspy/piper) | <200ms latency, ONNX, Docker image available |
| TTS engine (GPU) | **Coqui XTTS-v2** | High quality, voice cloning, streaming |
| Future | **Voxtral** (Mistral) | Beats ElevenLabs, open weights |
| Google/ElevenLabs | Via config flag | Cloud fallback for quality |
| Playback | **sounddevice** | Streaming NumPy audio, cross-platform |

### Telegram Bot
| Concern | Choice | Reason |
|---|---|---|
| Framework | **aiogram v3** | Async-first, modern, best for high-concurrency |
| Transport | Polling (dev) → Webhook (prod) | Simple start, scalable finish |
| Voice input | Download OGG → convert to WAV → ASR | Standard Telegram voice flow |

### Infrastructure
| Concern | Choice | Reason |
|---|---|---|
| Orchestration | **Docker Compose** | Simple, single-machine, easy to extend |
| Config | **YAML configs** in `config/` | Readable, centralised, env var overrides |
| Testing | **pytest + testcontainers** | Real DB in tests, no mocking |
| Telemetry (future) | **Prometheus + Grafana** | Standard stack, many dashboards available |

---

## Project File Structure

```
jarvis/
├── config/
│   ├── default.yaml          # All defaults (model sizes, ports, thresholds)
│   └── local.yaml            # User overrides (gitignored)
│
├── services/
│   ├── asr/                  # ASR microservice
│   │   ├── Dockerfile
│   │   ├── main.py           # FastAPI + WebSocket
│   │   ├── transcriber.py    # faster-whisper + silero-vad
│   │   └── tests/
│   │
│   ├── intent/               # Intent detection microservice
│   │   ├── Dockerfile
│   │   ├── main.py           # Reads ASR chunks, classifies, enqueues
│   │   ├── classifier.py     # LLM prompt + response parsing
│   │   └── tests/
│   │
│   ├── agent/                # Main agent (client-side)
│   │   ├── Dockerfile
│   │   ├── main.py           # Queue reader + agent loop
│   │   ├── mcp_client.py     # MCP server connections
│   │   ├── tools.py          # MCP tool schemas
│   │   └── tests/
│   │
│   ├── tts/                  # TTS microservice
│   │   ├── Dockerfile
│   │   ├── main.py           # FastAPI /synthesize endpoint
│   │   └── tests/
│   │
│   ├── response_composer/    # Reads agent output → calls TTS → sends audio
│   │   ├── Dockerfile
│   │   ├── main.py
│   │   └── tests/
│   │
│   ├── telegram_bot/         # Telegram interface
│   │   ├── Dockerfile
│   │   ├── main.py           # aiogram bot
│   │   ├── handlers.py       # voice + text message handlers
│   │   └── tests/
│   │
│   └── memory/               # Interaction history service
│       ├── Dockerfile
│       ├── main.py
│       └── tests/
│
├── client/                   # Desktop client (runs on user's machine)
│   ├── wakeword.py           # openWakeWord listener
│   ├── mic_streamer.py       # sounddevice → WebSocket → ASR
│   ├── audio_player.py       # sounddevice playback
│   └── main.py               # Ties it all together
│
├── db/
│   ├── migrations/           # Alembic migrations
│   └── schema.sql            # Reference schema
│
├── docker-compose.yml        # Full stack
├── docker-compose.dev.yml    # Dev overrides (hot reload, exposed ports)
└── Makefile
```

---

## Build Plan — Ordered Steps

### Phase 1: Foundation (DB + ASR service) ← START HERE
**Goal:** Audio in → transcription chunks in DB, service running in Docker

1. **DB schema** — PostgreSQL with tables: `sessions`, `asr_chunks`, `messages`, `events`
2. **ASR service** — faster-whisper + silero-vad, FastAPI WebSocket endpoint
3. **ASR tests** — POST a WAV file, assert chunks in DB
4. **Docker Compose** — `postgres` + `asr` services, health checks
5. **Desktop client stub** — `mic_streamer.py` captures mic → streams to ASR WS

**Resources:**
- https://github.com/SYSTRAN/faster-whisper
- https://github.com/snakers4/silero-vad
- https://github.com/ufal/whisper_streaming (reference for chunking strategy)
- HuggingFace: `openai/whisper-small` weights

---

### Phase 2: Intent Detection
**Goal:** Chunks → LLM decides if it's a command → message in queue

6. **Intent service** — reads new `asr_chunks` via `LISTEN/NOTIFY`, assembles text window
7. **LLM classifier** — prompt: *"Does this transcript contain an actionable request? Answer JSON: {is_request: bool, query: str}"*
8. **Message queue** — confirmed requests written to `messages` table with `status=pending`
9. **Intent tests** — assert "найди отель" → `is_request=true`, "ммм" → `is_request=false`

**Resources:**
- Anthropic Claude Haiku via OpenRouter (cheap, fast)
- asyncpg LISTEN/NOTIFY pattern

---

### Phase 3: Agent + MCP tools
**Goal:** Pending message → agent reasons + executes → result in queue

10. **Agent service** — polls `messages` for `status=pending`, runs agent loop
11. **MCP client** — connects to: filesystem MCP, playwright-mcp, desktop-commander
12. **Tool integration** — browser search, terminal exec, file ops
13. **History dump** — full action trace written to `messages` table (for fine-tuning)
14. **Agent tests** — mock MCP servers, assert correct tool calls

**Resources:**
- https://github.com/microsoft/playwright-mcp
- https://github.com/wonderwhy-er/DesktopCommanderMCP
- https://github.com/modelcontextprotocol/python-sdk
- Anthropic `mcp` Python package

---

### Phase 4: TTS + Response Composer
**Goal:** Agent text response → spoken audio → played on desktop

15. **TTS service** — Piper TTS in Docker, `/synthesize` endpoint, streaming chunks
16. **Response composer** — reads `messages` with `status=needs_speech`, calls TTS, streams audio
17. **Audio player (client)** — `sounddevice` plays back streamed WAV chunks
18. **TTS tests** — assert WAV bytes returned for text input

**Resources:**
- https://github.com/rhasspy/piper
- `linuxserver/piper` Docker image
- sounddevice streaming docs

---

### Phase 5: Wake Word + Desktop Client
**Goal:** Say "Jarvis" → whole pipeline activates end-to-end

19. **Wake word listener** — openWakeWord detecting "Jarvis" trigger
20. **Desktop client** — `main.py` ties together wake word + mic streamer + audio player
21. **E2E test** — WAV file with "Jarvis, what time is it" → agent response played back

**Resources:**
- https://github.com/dscripka/openWakeWord
- Custom wake word training if needed

---

### Phase 6: Telegram Bot
**Goal:** Same pipeline accessible via Telegram text + voice

22. **Telegram bot** — aiogram v3, polling mode initially
23. **Voice handler** — OGG → WAV → ASR endpoint → same intent + agent flow
24. **Text handler** — direct to message queue, skip ASR
25. **Response handler** — TTS audio sent as voice message OR text reply
26. **Config** — bot token + channel selection in `config/default.yaml`

**Resources:**
- https://github.com/aiogram/aiogram
- Telegram Bot API voice message handling

---

### Phase 7: Telemetry (future)
27. **Prometheus metrics** — cost counter, request counter, ASR chunk counter, latency histograms
28. **Grafana dashboard** — pre-built JSON dashboard in `monitoring/`
29. **Alert rules** — daily cost threshold

---

## Key Configuration File (preview)

```yaml
# config/default.yaml
asr:
  model: openai/whisper-small          # HuggingFace model ID
  device: cpu                          # cpu | cuda
  language: ru                         # primary language
  vad_threshold: 0.5
  chunk_duration_ms: 500

intent:
  llm: anthropic/claude-haiku-4.5      # via OpenRouter
  window_seconds: 10                   # sliding window of chunks to classify
  confidence_threshold: 0.8

agent:
  llm: anthropic/claude-sonnet-4.6
  mcp_servers:
    - name: filesystem
      command: npx
      args: ["@modelcontextprotocol/server-filesystem", "/home"]
    - name: playwright
      command: npx
      args: ["@playwright/mcp@latest"]
    - name: desktop-commander
      command: npx
      args: ["@wonderwhy-er/desktop-commander"]

tts:
  engine: piper                        # piper | coqui | google | elevenlabs
  voice: en_US-lessac-medium          # Piper voice model
  streaming: true

telegram:
  enabled: true
  token: ""                            # set via env TELEGRAM_BOT_TOKEN
  polling: true                        # false = webhook

memory:
  dump_history: true                   # save all interactions for fine-tuning

db:
  url: postgresql://jarvis:jarvis@postgres:5432/jarvis
  backup_enabled: true
  backup_schedule: "0 3 * * *"        # daily at 3am
```

---

## Questions / Things Needed From You

Before full development starts, please confirm:

1. **Hardware**: Do you have a CUDA GPU on the target machine? → determines Whisper model size and TTS choice
2. **Language priority**: Russian-first or English-first? (Whisper handles both, but language config matters)
3. **Wake word**: Should it be "Jarvis" or "Джарвис" (Russian pronunciation)?
4. **Telegram bot token**: Already have one? (used in config, not committed)
5. **OpenRouter budget**: Current key has access to Claude Haiku + Sonnet — confirm this is OK for development
6. **Client OS**: Linux/Mac/Windows? (affects desktop automation tool choices)

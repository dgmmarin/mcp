# Bridges & Inspections Intelligence Agent

Local AI agent: **QwenV3 (Ollama)** + **Bridges MCP Server (stdio, TypeScript)**.
No cloud. Everything runs on your Linux machine.

```
bridges-inspections-agent/
├── AGENT.md              ← agent definition, domain rules, memory spec
├── config.yaml           ← model + MCP launch settings
├── agent.py              ← Python agentic loop
├── requirements.txt      ← Python deps
├── .env.example          ← env var template
├── memory/
│   └── MEMORY.md         ← auto-managed memory index
└── mcp-server/
    ├── index.ts          ← MCP server (your original, unchanged)
    ├── package.json
    └── tsconfig.json
```

---

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Python | ≥ 3.10 | `sudo apt install python3` |
| Node.js | ≥ 18 | `sudo apt install nodejs npm` |
| Ollama | latest | `curl -fsSL https://ollama.com/install.sh \| sh` |

---

## 1 — Pull the model

```bash
ollama pull qwen3:8b
```

For 8 GB VRAM, `qwen3:8b` is the safe default (~5.5 GB).

---

## 2 — Build the MCP server

```bash
cd mcp-server
npm install
npm run build        # compiles index.ts → dist/index.js
cd ..
```

Verify the server speaks MCP correctly:
```bash
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"0"}}}' \
  | API_BASE_URL=http://localhost:3003 API_KEY=secret node mcp-server/dist/index.js
```
You should see a JSON response with `serverInfo`.

---

## 3 — Configure environment

```bash
cp .env.example .env
# Edit .env: set API_BASE_URL and API_KEY to match your REST API
export API_BASE_URL=http://localhost:3003
export API_KEY=your_secret
```

---

## 4 — Install Python deps

```bash
pip install -r requirements.txt
```

---

## 5 — Start Ollama

```bash
OLLAMA_GPU_LAYERS=99 ollama serve
```

---

## 6 — Run the agent

```bash
# Single query
python agent.py "Show me statistics of all bridges"

# Interactive REPL
python agent.py --interactive
```

The agent launches the MCP server as a **subprocess** via stdio automatically —
you do not need to start `node mcp-server/dist/index.js` separately.

---

## Example Queries

```
Show me statistics of all bridges
List all bridges with condition grade D
Which inspections are currently in_review?
Get the full profile for bridge <uuid>
Show me the photo gallery for bridge <uuid>
List pending inspections assigned to inspector <uuid>
Which bridges haven't been inspected this fiscal year?
Get the full inspection report for inspection <uuid>
```

---

## Changing the model

Edit `config.yaml`:

```yaml
model: qwen3:8b            # default — safe for 8 GB VRAM
# model: qwen3:8b-q4_K_M  # lighter, ~4.5 GB
# model: qwen3:14b-q4_K_M # best quality, fills 8 GB
```

---

## How the MCP connection works

```
agent.py
  └── spawns subprocess: node mcp-server/dist/index.js
        ├── stdin  ← JSON-RPC requests  (tools/list, tools/call)
        └── stdout → JSON-RPC responses
              └── MCP server calls your REST API (API_BASE_URL)
                    with Bearer API_KEY
```

The Python `mcp` SDK (`stdio_client`) handles all framing.
The agent never calls your REST API directly — only the MCP server does.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `CUDA out of memory` | Switch to `qwen3:8b-q4_K_M` in `config.yaml` |
| `ModuleNotFoundError: mcp` | `pip install mcp` |
| `Cannot find module 'dist/index.js'` | Run `npm run build` in `mcp-server/` |
| `API error 401` | Check `API_KEY` env var matches your REST API |
| `API error 404` | Check `API_BASE_URL` points to your running REST API |
| Agent returns prose instead of calling tools | Lower `temperature` to `0.1` |
| Model outputs `<think>` blocks | Stripped automatically — no action needed |

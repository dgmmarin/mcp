"""
Bridges & Inspections Intelligence Agent
─────────────────────────────────────────
QwenV3 (Ollama) + Bridges-Inspections MCP server (HTTP/Streamable-HTTP).

Start the MCP server first:
    npm run build && node dist/index.js

Usage:
    python agent.py "Show me statistics of all bridges"
    python agent.py --interactive
    python agent.py -i
"""

import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

import ollama
import yaml
from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

load_dotenv()

# ── Load config ───────────────────────────────────────────────────────────────
CFG_PATH = Path(__file__).parent / "config.yaml"
cfg = yaml.safe_load(CFG_PATH.read_text())

MODEL       = cfg["model"]
TEMPERATURE = float(cfg["temperature"])
MAX_STEPS   = int(cfg["max_steps"])
VERBOSE     = bool(cfg["verbose"])
DEF_LIMIT   = cfg["default_limit"]          # "10" (string)
MEMORY_DIR  = Path(__file__).parent / cfg.get("memory_dir", "memory/")

# MCP server URL (HTTP/Streamable-HTTP transport)
MCP_URL = cfg["mcp"]["url"]                 # e.g. http://127.0.0.1:3333/mcp

# ── Memory loader ─────────────────────────────────────────────────────────────
def load_memory() -> str:
    """
    Load MEMORY.md index + all .md files from the memory/ directory.
    Returns a concatenated string suitable for injecting into the system prompt.
    """
    if not MEMORY_DIR.exists():
        return ""

    sections: list[str] = []

    # Load MEMORY.md index (in agent's own dir)
    memory_index = Path(__file__).parent / "MEMORY.md"
    if memory_index.exists():
        text = memory_index.read_text().strip()
        if text:
            sections.append(f"=== Memory Index (MEMORY.md) ===\n{text}")

    # Load each individual memory file
    for md_file in sorted(MEMORY_DIR.glob("*.md")):
        text = md_file.read_text().strip()
        if text:
            sections.append(f"=== Memory: {md_file.name} ===\n{text}")

    return "\n\n".join(sections)


# ── System prompt ─────────────────────────────────────────────────────────────
SYSTEM_PROMPT_TEMPLATE = """\
You are the Bridges & Inspections Intelligence Agent.
You have access to live bridge and inspection data via tool calls.

═══════════════════════════════════════════════════════
TOOL CALL CONTRACT — follow exactly
═══════════════════════════════════════════════════════
1. ALWAYS call a tool before answering any bridge/inspection question.
   Never answer from memory or assumptions.

2. When calling a tool, respond ONLY with a raw JSON object.
   No prose before or after. No markdown fences. Example:
   {{"tool": "get_bridge_statistics", "arguments": {{}}}}

3. After receiving a tool result, reason about it and either call another
   tool OR give your final answer in plain text — never both in one response.

4. ALL argument values are STRINGS. Pass "10" not 10, "true" not true.

5. Do NOT pass tenantUuid unless the user explicitly specifies a tenant.

6. Use withDetails="true" ONLY when the user explicitly asks for defects,
   evaluations, or photos. Default is withDetails="false".

7. Default page size "10" unless the user asks for more (max "50").
   Use "page" for pagination (e.g. "2" for the second page) — NEVER "offset".

8. On empty results: say so, suggest a broader query, retry with relaxed filters.

9. On errors: report the error message verbatim and suggest checking
   API_BASE_URL and API_KEY environment variables.

═══════════════════════════════════════════════════════
TOOL CHAINING — broad overview queries
═══════════════════════════════════════════════════════
For overview/dashboard queries, chain these tools in order:
  1. get_bridge_statistics          — aggregate counts
  2. list_bridges (inspectionStatuses: "in_progress,pending")  — active work
  3. list_inspections (status: "in_review")  — awaiting sign-off

═══════════════════════════════════════════════════════
DOMAIN VOCABULARY
═══════════════════════════════════════════════════════
Condition Grades (soundAssesment):
  A = Excellent — no intervention needed, monitor annually
  B = Minor deterioration — schedule maintenance
  C = Moderate deterioration — maintenance required soon
  D = Severe deterioration — URGENT intervention required
  - = Not yet assessed — prioritise inspection

Inspection Statuses:
  pending     = Assigned but not started
  in_progress = Inspector actively working
  in_review   = Submitted, awaiting reviewer sign-off
  completed   = Fully reviewed and closed

═══════════════════════════════════════════════════════
REPORT FORMAT — mandatory
═══════════════════════════════════════════════════════
1. Lead with the most critical finding (grade D or in_review/pending first).
2. Bold key numbers and UUIDs.
3. Bullet lists for multiple bridges or inspections.
4. NEVER dump raw JSON — summarise in tables or bullets.
5. Close every report with an "Insight or Recommendation" block.
6. For galleries: list entries with index, filename, and description.

═══════════════════════════════════════════════════════
AVAILABLE TOOLS
═══════════════════════════════════════════════════════
{tool_descriptions}

{memory_section}
"""


# ── Helpers ───────────────────────────────────────────────────────────────────
def log(msg: str) -> None:
    if VERBOSE:
        print(msg, flush=True)


def strip_think(text: str) -> str:
    """Remove QwenV3 <think>…</think> reasoning blocks."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def parse_tool_call(text: str) -> dict | None:
    """
    Return a parsed tool-call dict, or None if the text is a plain answer.
    Handles: raw JSON, ```json fences, and prose-before-JSON patterns.
    """
    clean = re.sub(r"```(?:json)?", "", text).strip("` \n")
    match = re.search(r"\{[\s\S]*\}", clean)
    if not match:
        return None
    try:
        obj = json.loads(match.group())
        if "tool" in obj:
            return obj
    except json.JSONDecodeError:
        pass
    return None


def build_tool_descriptions(tools: list) -> str:
    """Build rich tool descriptions including accepted values for key params."""
    PARAM_HINTS: dict[str, str] = {
        "inspectionStatuses": 'comma-separated: "pending,in_progress,in_review,completed"',
        "soundAssesment":     'comma-separated grades: "A,B,C,D,-"',
        "withDetails":        '"true" or "false" — use true only for defects/evaluations/photos',
        "limit":              'default "10", max "50" (galleries: max "2")',
        "page":               'pagination page number, default "1" (NOT offset)',
        "inspectorUuids":     "comma-separated, max 25",
        "companyUuids":       "comma-separated, max 25",
        "status":             '"pending" | "in_progress" | "in_review" | "completed"',
    }

    lines = []
    for t in tools:
        props = t.inputSchema.get("properties", {}) if hasattr(t, "inputSchema") else {}
        req   = t.inputSchema.get("required", [])   if hasattr(t, "inputSchema") else []
        desc  = getattr(t, "description", "")

        param_lines = []
        for k, schema in props.items():
            required_marker = "*" if k in req else "?"
            hint = PARAM_HINTS.get(k, schema.get("description", "string"))
            param_lines.append(f"    {k}{required_marker}: {hint}")

        params_str = "\n".join(param_lines) if param_lines else "    (no parameters)"
        lines.append(f"• {t.name}: {desc}\n{params_str}")

    return "\n\n".join(lines)


def coerce_args_to_strings(args: dict) -> dict:
    """Ensure all argument values are strings (MCP SDK requirement)."""
    return {k: str(v) for k, v in args.items()}


def apply_smart_defaults(name: str, args: dict) -> dict:
    """Inject sensible defaults the model often forgets."""
    list_tools = {
        "list_bridges",
        "list_inspections",
        "list_bridge_inspection_galleries",
    }
    if name in list_tools and "limit" not in args:
        args["limit"] = DEF_LIMIT

    # Default withDetails to false for list_inspections unless model set it
    if name == "list_inspections" and "withDetails" not in args:
        args["withDetails"] = "false"

    return args


# ── Single-query agentic loop ─────────────────────────────────────────────────
async def run_agent_turn(
    user_query: str,
    session: ClientSession,
    tools: list,
    tool_names: set[str],
    system_prompt: str,
    history: list[dict],
) -> tuple[str, list[dict]]:
    """
    Run one user turn against the MCP session.

    Returns (final_answer, updated_history).
    history is the running conversation (excluding the system message).
    """
    messages = (
        [{"role": "system", "content": system_prompt}]
        + history
        + [{"role": "user", "content": user_query}]
    )

    final_answer = "⚠️  Max steps reached without a final answer."
    turn_messages: list[dict] = [{"role": "user", "content": user_query}]

    for step in range(1, MAX_STEPS + 1):
        log(f"── Step {step} " + "─" * 50)

        response = ollama.chat(
            model=MODEL,
            messages=messages,
            options={"temperature": TEMPERATURE},
        )
        raw_reply = response["message"]["content"]
        reply     = strip_think(raw_reply)
        log(f"🤖 {reply[:700]}\n")

        # Model output only a <think> block with no actual content — nudge it
        if not reply:
            log("⚠️  Empty reply after stripping think — nudging model.")
            messages.append({"role": "assistant", "content": ""})
            messages.append({
                "role": "user",
                "content": (
                    "Your previous response was empty. "
                    "Please provide your final answer in plain text, "
                    "or call a tool if you need more data."
                ),
            })
            continue

        tool_call = parse_tool_call(reply)

        if tool_call:
            name = tool_call.get("tool", "")
            args = tool_call.get("arguments", {})

            # Validate tool name
            if name not in tool_names:
                log(f"⚠️  Unknown tool '{name}' — asking model to retry.")
                messages.append({"role": "assistant", "content": reply})
                correction = (
                    f"'{name}' is not a valid tool. "
                    f"Valid tools: {', '.join(sorted(tool_names))}. "
                    "Please retry with a valid tool name."
                )
                messages.append({"role": "user", "content": correction})
                turn_messages.append({"role": "assistant", "content": reply})
                turn_messages.append({"role": "user", "content": correction})
                continue

            args = apply_smart_defaults(name, args)
            args = coerce_args_to_strings(args)

            log(f"🛠  {name}({json.dumps(args, ensure_ascii=False)})")

            try:
                result = await session.call_tool(name, args)
            except Exception as exc:
                error_msg = str(exc)
                log(f"❌ Tool error: {error_msg}")
                messages.append({"role": "assistant", "content": reply})
                err_content = (
                    f"Tool '{name}' raised an error: {error_msg}\n"
                    "Report this error verbatim, suggest the user checks "
                    "API_BASE_URL and API_KEY environment variables."
                )
                messages.append({"role": "user", "content": err_content})
                turn_messages.append({"role": "assistant", "content": reply})
                turn_messages.append({"role": "user", "content": err_content})
                continue

            # Flatten content blocks
            parts = []
            for block in result.content:
                if hasattr(block, "text"):
                    parts.append(block.text)
            tool_output = "\n".join(parts) if parts else "(empty result)"

            preview = tool_output[:600] + "…" if len(tool_output) > 600 else tool_output
            log(f"📤 {preview}\n")

            tool_result_msg = (
                f"Tool '{name}' returned:\n{tool_output}\n\n"
                "Continue — call another tool if needed, "
                "or give your final answer in plain text (no JSON)."
            )
            messages.append({"role": "assistant", "content": reply})
            messages.append({"role": "user", "content": tool_result_msg})
            turn_messages.append({"role": "assistant", "content": reply})
            turn_messages.append({"role": "user", "content": tool_result_msg})

        else:
            # Plain-text final answer
            final_answer = reply
            turn_messages.append({"role": "assistant", "content": reply})
            break

    updated_history = history + turn_messages
    return final_answer, updated_history


# ── Single-query entry point ───────────────────────────────────────────────────
async def run_agent(user_query: str) -> str:
    async with streamablehttp_client(MCP_URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools_result      = await session.list_tools()
            tools             = tools_result.tools
            tool_descriptions = build_tool_descriptions(tools)
            tool_names        = {t.name for t in tools}

            log(f"🔧 Tools: {sorted(tool_names)}\n")

            memory_text = load_memory()
            memory_section = (
                f"═══════════════════════════════════════════════════════\n"
                f"PERSISTENT MEMORY\n"
                f"═══════════════════════════════════════════════════════\n"
                f"{memory_text}"
                if memory_text else ""
            )

            system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
                tool_descriptions=tool_descriptions,
                memory_section=memory_section,
            )

            answer, _ = await run_agent_turn(
                user_query=user_query,
                session=session,
                tools=tools,
                tool_names=tool_names,
                system_prompt=system_prompt,
                history=[],
            )
            return answer


# ── Interactive REPL with persistent conversation history ─────────────────────
async def interactive() -> None:
    print("🌉 Bridges & Inspections Intel Agent  [QwenV3 · local]")
    print("   Type 'quit' or press Ctrl-C to exit.\n")

    async with streamablehttp_client(MCP_URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools_result      = await session.list_tools()
            tools             = tools_result.tools
            tool_descriptions = build_tool_descriptions(tools)
            tool_names        = {t.name for t in tools}

            log(f"🔧 Tools: {sorted(tool_names)}\n")

            memory_text = load_memory()
            memory_section = (
                f"═══════════════════════════════════════════════════════\n"
                f"PERSISTENT MEMORY\n"
                f"═══════════════════════════════════════════════════════\n"
                f"{memory_text}"
                if memory_text else ""
            )

            system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
                tool_descriptions=tool_descriptions,
                memory_section=memory_section,
            )

            # Persistent conversation history across turns
            history: list[dict] = []

            while True:
                try:
                    query = input("You: ").strip()
                except (KeyboardInterrupt, EOFError):
                    print("\nBye.")
                    break
                if not query:
                    continue
                if query.lower() in ("quit", "exit", "q"):
                    break

                answer, history = await run_agent_turn(
                    user_query=query,
                    session=session,
                    tools=tools,
                    tool_names=tool_names,
                    system_prompt=system_prompt,
                    history=history,
                )
                print(f"\nAgent:\n{answer}\n{'─'*60}\n")


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    flags    = {a for a in sys.argv[1:] if a.startswith("-")}
    non_flag = [a for a in sys.argv[1:] if not a.startswith("-")]

    if flags & {"--interactive", "-i"}:
        asyncio.run(interactive())
    else:
        query = " ".join(non_flag) or "Show me statistics of all bridges."
        print(asyncio.run(run_agent(query)))

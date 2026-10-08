"""Read Codex rollout response messages; event_msg mirrors are deliberately ignored."""
import json
from pathlib import Path
from redact import redact


INJECTED_PREFIXES = (
    "# AGENTS.md instructions", "<environment_context>", "<permissions instructions>",
    "<user_instructions>", "<system_reminder>", "<skill>", "<hook_prompt", "<external_codex_apps_open_page>",
)


def read_messages(path: Path, offset: int = 0):
    """Yield (end byte offset, text); leave an incomplete final line for the next hook."""
    with path.open("rb") as handle:
        if offset > path.stat().st_size:
            raise ValueError("Transcript shrank; refusing to reuse its capture cursor")
        handle.seek(offset)
        while line := handle.readline():
            if not line.endswith(b"\n"):
                break
            entry = json.loads(line)
            text = ""
            if isinstance(entry, dict) and entry.get("type") == "response_item":
                item = entry.get("payload", {})
                if isinstance(item, dict):
                    item = item.get("item", item.get("message", item))
                if isinstance(item, dict) and item.get("type") == "message":
                    role = item.get("role")
                    if role in ("user", "assistant") and item.get("phase") != "analysis":
                        content = item.get("content", [])
                        if isinstance(content, str):
                            text = content.strip()
                        elif isinstance(content, list):
                            text = "\n".join(
                                block["text"] for block in content
                                if isinstance(block, dict)
                                and block.get("type") in ("input_text", "output_text", "text")
                                and isinstance(block.get("text"), str)
                            ).strip()
                        if text.startswith(INJECTED_PREFIXES):
                            text = ""
                        if text:
                            text = f"**{role.title()}:** {redact(text)}"
            yield handle.tell(), text

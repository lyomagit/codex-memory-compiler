"""Best-effort local secret filtering before model input and persisted output."""
import re

_PATTERNS = (
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)",
    r"\b(?:sk-(?:proj-|ant-|live-|test-)?|gh[pousr]_|github_pat_)[A-Za-z0-9_-]{16,}\b",
    r"\bAKIA[0-9A-Z]{16}\b",
    r"(?i:\bBearer\s+)[^\s`'\"<>]+",
    r'''(?i:\b(?:[A-Z0-9_]*(?:api[_-]?key|token|secret|password|passwd|passphrase|private[_-]?key|database_url)[A-Z0-9_]*))['"]?\s*[:=]\s*(?:"[^"\n]*"|'[^'\n]*'|[^\s,;&`<>]+)''',
)


def redact(text: str) -> str:
    for pattern in _PATTERNS:
        text = re.sub(pattern, "[REDACTED]", text, flags=re.DOTALL)
    return text

#!/usr/bin/env bash
#
# Redact credentials from a chat transcript before it is shared or committed.
#
#   bash logs/scrub_secrets.sh logs/chat-transcript.md
#
# Rewrites the file in place and keeps <file>.orig as a backup. Pattern matching
# catches the common key formats below; it is a safety net, not a guarantee.
# Read the diff, and rotate any key that was ever pasted into a chat regardless.
set -euo pipefail

FILE="${1:-}"
[ -n "$FILE" ] || { echo "usage: bash $0 <transcript-file>" >&2; exit 1; }
[ -f "$FILE" ] || { echo "no such file: $FILE" >&2; exit 1; }

cp "$FILE" "$FILE.orig"

python3 - "$FILE" <<'PY'
import re, sys

path = sys.argv[1]
text = open(path, encoding="utf-8", errors="replace").read()

PATTERNS = [
    # provider-specific key formats
    (r'sk-ant-[A-Za-z0-9_\-]{20,}',            '<REDACTED:ANTHROPIC_KEY>'),
    (r'\bAIza[A-Za-z0-9_\-]{30,}',             '<REDACTED:GOOGLE_API_KEY>'),
    (r'\bAQ\.[A-Za-z0-9_\-]{20,}',             '<REDACTED:GOOGLE_OAUTH_TOKEN>'),
    (r'\bya29\.[A-Za-z0-9_\-]{20,}',           '<REDACTED:GOOGLE_OAUTH_TOKEN>'),
    (r'\bsk-[A-Za-z0-9]{32,}',                 '<REDACTED:OPENAI_KEY>'),
    (r'\bgh[pousr]_[A-Za-z0-9]{30,}',          '<REDACTED:GITHUB_TOKEN>'),
    (r'\bhf_[A-Za-z0-9]{30,}',                 '<REDACTED:HUGGINGFACE_TOKEN>'),
    (r'\bxox[baprs]-[A-Za-z0-9\-]{10,}',       '<REDACTED:SLACK_TOKEN>'),
    (r'\bAKIA[0-9A-Z]{16}\b',                  '<REDACTED:AWS_ACCESS_KEY_ID>'),
    # assignments like GEMINI_API_KEY=... or "api_key": "..."
    (r'((?:API_KEY|APIKEY|api_key|TOKEN|token|SECRET|secret)\s*[=:]\s*["\']?)'
     r'([A-Za-z0-9_\-\.]{16,})', r'\1<REDACTED>'),
]

total = 0
for pat, repl in PATTERNS:
    text, n = re.subn(pat, repl, text)
    total += n

open(path, "w", encoding="utf-8").write(text)
print(f"redactions applied: {total}")
if total == 0:
    print("NOTE: nothing matched. Confirm by eye that no credential is present.")
PY

echo "backup kept at $FILE.orig -- review the diff, then delete the backup:"
echo "  diff \"$FILE.orig\" \"$FILE\" | head -40"
echo "  rm \"$FILE.orig\""

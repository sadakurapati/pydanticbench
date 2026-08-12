# logs/

This directory accompanies the AI-use declaration. There are
two things in this folder, and it matters which is which.

| File | What it is |
|---|---|
| `SESSION_LOG.md` | An engineering log of the build: decisions, measurements, bugs found, and what changed as a result. Written by the assistant from its own working context. **A reconstruction, not a verbatim transcript.** |
| `chat-transcript.md` | **You must add this.** The actual conversation, exported from the Claude desktop app. |
| `scrub_secrets.sh` | Run this over the transcript before committing it. See below. |

## Adding the transcript

The assistant could not export the conversation itself — the transcript is
stored in an application-internal directory outside the folders it had access
to. Export it manually:

1. In the Claude desktop app, open this conversation.
2. Use the conversation menu to copy or export the full contents.
3. Save it here as `logs/chat-transcript.md`.

## Before you commit it — scrub credentials

**An API key was pasted into this conversation.** It is therefore in the raw
transcript. Two things need to happen:

1. **Rotate that key.** Revoke it at the provider and issue a new one. Scrubbing
   the file does not undo the exposure — the key sat in a chat log, and treating
   it as compromised is the only safe assumption.
2. **Run the scrubber** before the transcript goes anywhere:

   ```bash
   bash logs/scrub_secrets.sh logs/chat-transcript.md
   ```

   It rewrites the file in place (keeping a `.orig` backup you should delete
   once you have checked the result) and reports how many redactions it made.
   Read the diff before you trust it; pattern matching is not a guarantee.

No credential was ever written into the benchmark code, configs, or task files.
`run.sh` reads keys from the environment or from a hidden prompt and never
persists them.

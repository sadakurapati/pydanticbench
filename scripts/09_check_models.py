#!/usr/bin/env python3
"""
PydanticBench -- verify the configured models, and help pick replacements.

Model identifiers rot. Providers retire them without warning, and a dead id
fails only when the first agent calls it -- after the image has built and a
budget has been approved. Worse, the failure text ("NotFoundError") looks
nothing like "your model list is out of date".

So this runs first, costs one token per model, and distinguishes three outcomes
that deserve different treatment:

  OK        the model answered
  QUOTA     429 -- the model EXISTS, the key is rate-limited or out of credit.
            Not a bad identifier. Often fine on a retry or a different tier, so
            it is offered rather than discarded.
  MISSING   404 or similar -- the identifier is wrong or retired.

When something is unusable it queries the provider for what the key can actually
reach, filters out models that cannot do coding work at all (image, audio, TTS,
video, embedding, robotics), ranks the rest into lite / flash / pro tiers
preferring stable releases over previews and newer versions over older, and
offers a ready-made three-model ladder to accept or override.

Usage:
    python3 scripts/09_check_models.py --select gemini --models "a b c"
        -> prints the final, verified model list on stdout; UI goes to stderr
    python3 scripts/09_check_models.py --check gemini/gemini-3.5-flash
"""

from __future__ import annotations

import argparse
import os
import re
import sys

# Models that exist but cannot do software engineering.
_EXCLUDE = re.compile(
    r"(image|tts|audio|speech|embedding|robotics|lyria|banana|veo|imagen|"
    r"computer-use|deep-research|antigravity|omni|live|gemma)",
    re.I,
)

OK, QUOTA, MISSING = "ok", "quota", "missing"


def classify(exc: Exception) -> str:
    text = f"{type(exc).__name__}: {exc}".lower()
    if "ratelimit" in text or "429" in text or "quota" in text:
        return QUOTA
    return MISSING


def probe(model: str) -> tuple[str, str]:
    import litellm
    litellm.suppress_debug_info = True
    try:
        litellm.completion(model=model, messages=[{"role": "user", "content": "hi"}],
                           max_tokens=1, timeout=60)
        return OK, ""
    except Exception as e:
        return classify(e), str(e).replace("\n", " ")[:200]


def available(provider: str) -> list[str]:
    try:
        import httpx
    except ImportError:
        return []
    try:
        if provider == "gemini":
            key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")
            if not key:
                return []
            r = httpx.get("https://generativelanguage.googleapis.com/v1beta/models",
                          headers={"x-goog-api-key": key}, timeout=30)
            r.raise_for_status()
            return [m["name"].removeprefix("models/") for m in r.json().get("models", [])
                    if "generateContent" in m.get("supportedGenerationMethods", [])]
        if provider == "anthropic":
            key = os.environ.get("ANTHROPIC_API_KEY", "")
            if not key:
                return []
            r = httpx.get("https://api.anthropic.com/v1/models",
                          headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                          timeout=30)
            r.raise_for_status()
            return [m["id"] for m in r.json().get("data", [])]
    except Exception:
        return []
    return []


def tier_of(name: str) -> str:
    n = name.lower()
    if "lite" in n or "haiku" in n:
        return "lite"
    if "pro" in n or "opus" in n:
        return "pro"
    return "flash"


def version_of(name: str) -> float:
    m = re.search(r"(\d+(?:\.\d+)?)", name)
    return float(m.group(1)) if m else 0.0


def rank(names: list[str]) -> dict[str, list[str]]:
    """Group usable models by tier, best first."""
    out: dict[str, list[str]] = {"lite": [], "flash": [], "pro": []}
    for n in names:
        if _EXCLUDE.search(n):
            continue
        out[tier_of(n)].append(n)
    for tier in out:
        # stable before preview, newer before older, then alphabetical
        out[tier].sort(key=lambda n: (("preview" in n.lower()), -version_of(n), n))
    return out


def ladder(provider: str, names: list[str]) -> list[str]:
    r = rank(names)
    picked = [r[t][0] for t in ("lite", "flash", "pro") if r[t]]
    return [f"{provider}/{n}" for n in picked]


def read_tty(prompt: str) -> str:
    sys.stderr.write(prompt)
    sys.stderr.flush()
    try:
        with open("/dev/tty") as tty:
            return tty.readline().strip()
    except OSError:
        return ""


def select(provider: str, models: list[str]) -> list[str]:
    results = [(m, *probe(m)) for m in models]
    for m, status, _ in results:
        label = {OK: "[ok]  ", QUOTA: "[quota]", MISSING: "[FAIL]"}[status]
        print(f"  {label} {m}", file=sys.stderr)

    if all(s == OK for _, s, _ in results):
        return models

    for m, status, msg in results:
        if status == QUOTA:
            print(f"\n  {m} exists but the key is rate-limited or out of quota.",
                  file=sys.stderr)
        elif status == MISSING:
            print(f"\n  {m} is not available:\n    {msg}", file=sys.stderr)

    names = available(provider)
    if not names:
        print("\n  Could not list this provider's models. Set MODELS_OVERRIDE "
              "manually.", file=sys.stderr)
        return []

    r = rank(names)
    suggestion = ladder(provider, names)

    print("\n  Usable coding models for this key, best first:", file=sys.stderr)
    numbered: list[str] = []
    for tier in ("lite", "flash", "pro"):
        if not r[tier]:
            continue
        print(f"\n    {tier}:", file=sys.stderr)
        for n in r[tier][:6]:
            numbered.append(f"{provider}/{n}")
            print(f"      {len(numbered):2d}) {provider}/{n}", file=sys.stderr)

    print(f"\n  Recommended ladder: {' '.join(suggestion)}", file=sys.stderr)
    reply = read_tty("\n  Press Enter to accept, or enter three numbers "
                     "(e.g. 1 4 7): ")
    if not reply:
        chosen = suggestion
    else:
        try:
            idx = [int(x) for x in reply.replace(",", " ").split()]
            chosen = [numbered[i - 1] for i in idx]
        except (ValueError, IndexError):
            print("  Could not parse that selection.", file=sys.stderr)
            return []

    print(f"\n  Verifying: {' '.join(chosen)}", file=sys.stderr)
    final = []
    for m in chosen:
        status, msg = probe(m)
        label = {OK: "[ok]  ", QUOTA: "[quota]", MISSING: "[FAIL]"}[status]
        print(f"  {label} {m}", file=sys.stderr)
        if status == MISSING:
            print(f"    {msg}", file=sys.stderr)
            return []
        final.append(m)  # quota-limited models are kept; they may recover
    return final


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--select", metavar="PROVIDER")
    ap.add_argument("--models", default="")
    ap.add_argument("--check", nargs="*", default=[])
    args = ap.parse_args()

    if args.select:
        models = args.models.split()
        final = select(args.select, models)
        if not final:
            return 1
        print(" ".join(final))          # stdout: machine-readable result
        return 0

    bad = False
    for m in args.check:
        status, msg = probe(m)
        print(f"  {'[ok]  ' if status == OK else '[FAIL]'} {m}", file=sys.stderr)
        if status == MISSING:
            print(f"    {msg}", file=sys.stderr)
            bad = True
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())

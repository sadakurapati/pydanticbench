#!/usr/bin/env bash
#
# PydanticBench -- single-command interactive runner.
#
#   ./run.sh
#
# Checks prerequisites, installs dependencies, builds the evaluation image,
# verifies the scorer, runs the benchmark across three model configurations,
# scores every run, and prints a results summary.
#
# Safe to re-run: each stage detects existing work and skips or resumes it.
#
# Non-interactive use (CI):
#   PROVIDER=anthropic ANTHROPIC_API_KEY=sk-... SCOPE=full ASSUME_YES=1 ./run.sh
#
# API keys are read into shell variables only. They are never written to disk,
# never echoed, and never passed on a command line where `ps` could see them.

set -euo pipefail
cd "$(dirname "$0")"

# macOS ships bash 3.2, so: no associative arrays, no mapfile, no ${x,,}.

# ---------------------------------------------------------------- presentation
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  B=$'\033[1m'; DIM=$'\033[2m'; R_=$'\033[31m'; G=$'\033[32m'; Y=$'\033[33m'
  C=$'\033[36m'; N=$'\033[0m'
else
  B=""; DIM=""; R_=""; G=""; Y=""; C=""; N=""
fi

step()  { printf "\n${B}${C}==> %s${N}\n" "$*"; }
ok()    { printf "  ${G}[ok]${N}   %s\n" "$*"; }
warn()  { printf "  ${Y}[warn]${N} %s\n" "$*"; }
fail()  { printf "  ${R_}[fail]${N} %s\n" "$*"; }
info()  { printf "  ${DIM}%s${N}\n" "$*"; }
die()   { printf "\n${R_}${B}ERROR:${N} %s\n\n" "$*" >&2; exit 1; }

INTERACTIVE=1
[ -t 0 ] || [ -e /dev/tty ] || INTERACTIVE=0

ask() { # ask <prompt> <default> -> echoes answer
  local prompt="$1" default="${2:-}" reply
  if [ -n "${ASSUME_YES:-}" ] || [ "$INTERACTIVE" = "0" ]; then echo "$default"; return; fi
  if [ -n "$default" ]; then
    printf "  %s ${DIM}[%s]${N}: " "$prompt" "$default" >&2
  else
    printf "  %s: " "$prompt" >&2
  fi
  read -r reply </dev/tty 2>/dev/null || reply=""
  echo "${reply:-$default}"
}

confirm() { # confirm <prompt>  -> returns 0/1
  [ -n "${ASSUME_YES:-}" ] && return 0
  [ "$INTERACTIVE" = "0" ] && return 1
  local reply
  printf "  %s ${DIM}[y/N]${N}: " "$1" >&2
  read -r reply </dev/tty 2>/dev/null || reply=""
  case "$(printf '%s' "$reply" | tr '[:upper:]' '[:lower:]')" in y|yes) return 0;; *) return 1;; esac
}

read_secret() { # read_secret <prompt> -> echoes value, never displayed
  local prompt="$1" value
  printf "  %s: " "$prompt" >&2
  stty -echo 2>/dev/null || true
  read -r value </dev/tty 2>/dev/null || value=""
  stty echo 2>/dev/null || true
  printf "\n" >&2
  echo "$value"
}

pkg_mgr() {
  if command -v brew    >/dev/null 2>&1; then echo brew
  elif command -v apt-get >/dev/null 2>&1; then echo apt
  elif command -v dnf   >/dev/null 2>&1; then echo dnf
  else echo none; fi
}

# Consent for installing software is deliberately SEPARATE from `confirm`.
# ASSUME_YES means "don't ask me about running the benchmark"; it must not also
# mean "install system packages and use sudo without asking". Automated callers
# that genuinely want that must opt in with ALLOW_INSTALL=1.
confirm_install() {
  [ -n "${ALLOW_INSTALL:-}" ] && return 0
  [ "$INTERACTIVE" = "0" ] && return 1
  local reply
  printf "  ${Y}?${N} %s ${DIM}[y/N]${N}: " "$1" >&2
  read -r reply </dev/tty 2>/dev/null || reply=""
  case "$(printf '%s' "$reply" | tr '[:upper:]' '[:lower:]')" in y|yes) return 0;; *) return 1;; esac
}

confirm_yes() { # like confirm, but defaults to YES
  [ -n "${ASSUME_YES:-}" ] && return 0
  [ "$INTERACTIVE" = "0" ] && return 0
  local reply
  printf "  %s ${DIM}[Y/n]${N}: " "$1" >&2
  read -r reply </dev/tty 2>/dev/null || reply=""
  case "$(printf '%s' "$reply" | tr '[:upper:]' '[:lower:]')" in n|no) return 1;; *) return 0;; esac
}

try_install() { # try_install <label> <command...>
  local label="$1"; shift
  printf "  ${DIM}would run:${N} %s\n" "$*" >&2
  if confirm_install "Install ${label} now?"; then
    if eval "$*"; then ok "${label} installed"; return 0; fi
    fail "installing ${label} failed"; return 1
  fi
  info "skipped -- install ${label} yourself and re-run"
  return 1
}

printf "\n${B}  PydanticBench${N} -- unsaturated coding-agent benchmark\n"
printf "${DIM}  100 tasks - pydantic/pydantic v2.13.4 - mini-swe-agent${N}\n"

# ------------------------------------------------------------------- preflight
step "Checking prerequisites"

if ! command -v docker >/dev/null 2>&1; then
  fail "docker not found"
  case "$(uname -s)" in
    Darwin)
      if [ "$(pkg_mgr)" = "brew" ]; then
        try_install "Docker Desktop" "brew install --cask docker" || true
      else
        info "install Docker Desktop: https://docs.docker.com/desktop/install/mac-install/"
      fi ;;
    Linux)
      case "$(pkg_mgr)" in
        apt) try_install "Docker engine" "sudo apt-get update && sudo apt-get install -y docker.io" || true ;;
        dnf) try_install "Docker engine" "sudo dnf install -y docker" || true ;;
        *)   info "install Docker: https://docs.docker.com/engine/install/" ;;
      esac ;;
    *) info "install Docker: https://docs.docker.com/get-docker/" ;;
  esac
  command -v docker >/dev/null 2>&1 || die "docker is still not available -- install it and re-run ./run.sh"
fi
ok "docker found"

if ! docker info >/dev/null 2>&1; then
  fail "docker daemon is not running"
  started=0
  case "$(uname -s)" in
    Darwin)
      if confirm_install "Start Docker Desktop now?"; then
        open -a Docker 2>/dev/null || true
        printf "  waiting for the docker daemon "
        i=0
        while [ $i -lt 30 ]; do
          if docker info >/dev/null 2>&1; then started=1; break; fi
          printf "."; sleep 3; i=$((i+1))
        done
        printf "\n"
      fi ;;
    Linux)
      if confirm_install "Start the docker service now? (sudo)"; then
        sudo systemctl start docker 2>/dev/null || sudo service docker start 2>/dev/null || true
        docker info >/dev/null 2>&1 && started=1
      fi ;;
  esac
  [ "$started" = "1" ] || die "the docker daemon is not reachable. Start Docker and re-run ./run.sh"
fi
ok "docker daemon is running"

# mini-swe-agent requires Python >= 3.10. `python3` is frequently an older
# system interpreter (macOS ships 3.9 at /usr/bin/python3), and pip's failure
# mode for that is the deeply unhelpful "from versions: none" -- it means no
# release matches this interpreter, not that the package is missing. So search
# explicitly for a new enough interpreter instead of trusting `python3`.
PYBIN="${PYBIN:-}"
PYV=""
if [ -n "$PYBIN" ]; then
  PYV=$("$PYBIN" -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null || echo "")
  [ -n "$PYV" ] || die "PYBIN=$PYBIN is not a working Python interpreter"
fi
for cand in python3.14 python3.13 python3.12 python3.11 python3.10 python3 python; do
  [ -n "$PYBIN" ] && break
  command -v "$cand" >/dev/null 2>&1 || continue
  v=$("$cand" -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null) || continue
  maj=${v%%.*}; min=${v#*.}
  if [ "$maj" = "3" ] && [ "$min" -ge 10 ] 2>/dev/null; then
    PYBIN="$cand"; PYV="$v"; break
  fi
done

if [ -z "$PYBIN" ]; then
  found=$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null || echo "none")
  fail "no Python >= 3.10 found (python3 reports: $found)"
  info "mini-swe-agent requires Python 3.10 or newer"
  case "$(pkg_mgr)" in
    brew) try_install "Python 3.12" "brew install python@3.12" || true ;;
    apt)  try_install "Python 3.12" "sudo apt-get update && sudo apt-get install -y python3.12 python3.12-venv" || true ;;
    dnf)  try_install "Python 3.12" "sudo dnf install -y python3.12" || true ;;
    *)    info "install from https://www.python.org/downloads/" ;;
  esac
  # re-run discovery after a possible install
  hash -r 2>/dev/null || true
  for cand in python3.14 python3.13 python3.12 python3.11 python3.10 python3; do
    [ -n "$PYBIN" ] && break
    command -v "$cand" >/dev/null 2>&1 || continue
    v=$("$cand" -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null) || continue
    maj=${v%%.*}; min=${v#*.}
    if [ "$maj" = "3" ] && [ "$min" -ge 10 ] 2>/dev/null; then PYBIN="$cand"; PYV="$v"; fi
  done
fi

if [ -z "$PYBIN" ]; then
  cat >&2 <<'HINT'

  Still no Python >= 3.10. Install one of these, then re-run ./run.sh:

    macOS (Homebrew)   brew install python@3.12
    macOS (python.org) https://www.python.org/downloads/macos/
    Debian/Ubuntu      sudo apt install python3.12 python3.12-venv
    pyenv              pyenv install 3.12 && pyenv local 3.12

  If it is installed but not first on PATH, point the script at it directly:

    PYBIN=/opt/homebrew/bin/python3.12 ./run.sh

HINT
  exit 1
fi
ok "python $PYV ($(command -v "$PYBIN"))"

FREE_GB=$(df -Pg . 2>/dev/null | awk 'NR==2{print $4}' || echo "?")
if [ "$FREE_GB" != "?" ] && [ "$FREE_GB" -lt 6 ] 2>/dev/null; then
  warn "only ${FREE_GB}GB free; the image needs ~6GB"
else
  ok "disk space ok (${FREE_GB}GB free)"
fi

[ -f tasks/tasks.jsonl ] || die "tasks/tasks.jsonl is missing -- run this script from the pydanticbench folder"
ok "task set present ($(wc -l < tasks/tasks.jsonl | tr -d ' ') tasks)"

# -------------------------------------------------------------------- provider
step "Choose models to evaluate"

PROVIDER="${PROVIDER:-}"
if [ -z "$PROVIDER" ]; then
  cat <<'MENU'
    1) Anthropic  -- Haiku 4.5 / Sonnet 4.5 / Opus 4.1     (recommended)
    2) Gemini     -- 2.5 Flash-Lite / 2.5 Flash / 2.5 Pro
    3) Mixed      -- Haiku / Sonnet / Gemini 2.5 Pro       (cross-vendor)
MENU
  case "$(ask 'Selection' '1')" in
    1) PROVIDER=anthropic ;;
    2) PROVIDER=gemini ;;
    3) PROVIDER=mixed ;;
    *) die "invalid selection" ;;
  esac
fi
ok "provider set: $PROVIDER"

# Keys live in the environment for this process only.
case "$PROVIDER" in
  anthropic)
    [ -n "${ANTHROPIC_API_KEY:-}" ] || ANTHROPIC_API_KEY=$(read_secret "Anthropic API key (input hidden)")
    [ -n "$ANTHROPIC_API_KEY" ] || die "no Anthropic API key provided"
    export ANTHROPIC_API_KEY
    NAMES="haiku sonnet opus"
    MODELS="anthropic/claude-haiku-4-5-20251001 anthropic/claude-sonnet-5 anthropic/claude-opus-5"
    ;;
  gemini)
    [ -n "${GEMINI_API_KEY:-}" ] || GEMINI_API_KEY=$(read_secret "Gemini API key (input hidden)")
    [ -n "$GEMINI_API_KEY" ] || die "no Gemini API key provided"
    export GEMINI_API_KEY
    # Gemini ids move fast -- 2.5 Flash-Lite was retired for new keys. The
    # model preflight below will tell you exactly what your key can reach.
    NAMES="gemini-flash-lite gemini-flash gemini-pro"
    MODELS="gemini/gemini-3.5-flash-lite gemini/gemini-3.6-flash gemini/gemini-3-pro"
    ;;
  mixed)
    [ -n "${ANTHROPIC_API_KEY:-}" ] || ANTHROPIC_API_KEY=$(read_secret "Anthropic API key (input hidden)")
    [ -n "${GEMINI_API_KEY:-}"    ] || GEMINI_API_KEY=$(read_secret "Gemini API key (input hidden)")
    export ANTHROPIC_API_KEY GEMINI_API_KEY
    NAMES="haiku sonnet gemini-pro"
    MODELS="anthropic/claude-haiku-4-5-20251001 anthropic/claude-sonnet-5 gemini/gemini-3-pro"
    ;;
  *) die "unknown PROVIDER: $PROVIDER" ;;
esac

# Override without editing this file:
#   MODELS="gemini/a gemini/b gemini/c" ./run.sh
# Useful when a provider retires an id -- which they do, without warning.
if [ -n "${MODELS_OVERRIDE:-}" ]; then
  MODELS="$MODELS_OVERRIDE"
  NAMES="${NAMES_OVERRIDE:-$(echo "$MODELS" | tr ' ' '\n' | sed 's|.*/||' | tr '\n' ' ')}"
  warn "using MODELS_OVERRIDE: $MODELS"
fi
ok "credentials captured (not written to disk)"

# ---------------------------------------------------------------- dependencies
step "Installing Python dependencies"

# Install into a project-local virtualenv rather than the system interpreter.
# This avoids three common failure modes at once: PEP 668 "externally managed
# environment" errors, a `pip3` that belongs to a different interpreter than
# `python3`, and polluting the user's global site-packages. Set USE_VENV=0 to
# install into the current environment instead.
VENV="$PWD/.venv"
if [ "${USE_VENV:-1}" = "0" ]; then
  PY="$PYBIN"
  info "USE_VENV=0 -- installing into $("$PYBIN" -c 'import sys;print(sys.prefix)')"
else
  if [ ! -x "$VENV/bin/python" ]; then
    info "creating virtualenv at .venv"
    if ! "$PYBIN" -m venv "$VENV" 2>/dev/null; then
      fail "could not create a virtualenv (the venv module is probably missing)"
      case "$(pkg_mgr)" in
        apt) try_install "python3-venv" "sudo apt-get install -y python3-venv" || true ;;
        dnf) try_install "python3-venv" "sudo dnf install -y python3-virtualenv" || true ;;
      esac
      if ! "$PYBIN" -m venv "$VENV" 2>/dev/null; then
        cat >&2 <<'HINT'

  Still cannot create a virtualenv. Either install the venv module for your
  interpreter, or skip the virtualenv and install into the current environment:

      USE_VENV=0 ./run.sh

HINT
        exit 1
      fi
    fi
  fi
  PY="$VENV/bin/python"
  ok "virtualenv ready (.venv)"
fi

if "$PY" -c "import minisweagent" >/dev/null 2>&1; then
  ok "mini-swe-agent already installed"
else
  info "installing mini-swe-agent and datasets ..."
  "$PY" -m pip install --quiet --disable-pip-version-check --upgrade pip >/dev/null 2>&1 || true
  if ! "$PY" -m pip install --quiet --disable-pip-version-check --no-warn-script-location \
        mini-swe-agent datasets; then
    fail "installation failed"
    cat >&2 <<'HINT'

  If pip reported "from versions: none", the interpreter is too old --
  mini-swe-agent needs Python >= 3.10. Check what this venv is using:

      .venv/bin/python --version

  Then remove .venv and re-run pointing at a newer interpreter:

      rm -rf .venv && PYBIN=/opt/homebrew/bin/python3.12 ./run.sh

HINT
    exit 1
  fi
  ok "mini-swe-agent installed"
fi

# Prefer the venv's console script; fall back to module invocation.
#
# This MUST be an array, not a string. A string expanded unquoted is subject to
# word splitting, so any space in the install path -- and on macOS the default
# location contains "Application Support" -- tears the command in half and
# produces "No such file or directory" on a path fragment.
if [ -x "$VENV/bin/mini-extra" ]; then
  MSWEA=( "$VENV/bin/mini-extra" swebench )
elif command -v mini-extra >/dev/null 2>&1 && [ "${USE_VENV:-1}" = "0" ]; then
  MSWEA=( mini-extra swebench )
else
  MSWEA=( "$PY" -m minisweagent.run.benchmarks.swebench )
  info "using module invocation for mini-swe-agent"
fi

# ------------------------------------------------------------- model selection
step "Checking the models are reachable"
# One token per model. Distinguishes "identifier is dead" from "key is out of
# quota", and if anything is unusable it lists what this key CAN reach, ranks it
# into lite/flash/pro tiers and offers a ready-made ladder. Runs before the
# image build so a stale model list costs seconds, not a benchmark run.
SELECTED=$("$PY" scripts/09_check_models.py --select "$PROVIDER" --models "$MODELS") || \
  die "no usable models selected. Nothing was spent."
if [ "$SELECTED" != "$MODELS" ]; then
  MODELS="$SELECTED"
  warn "model list updated"
fi
# Names label the results directories; derive them from whatever was chosen.
NAMES=$(printf '%s' "$MODELS" | tr ' ' '\n' | sed 's|.*/||' | tr '\n' ' ')
ok "models: $MODELS"

# Cost per task, inferred from the tier in the model name. A projection for the
# estimate below, not a quote -- the per-task cap is the real ceiling.
rate_for() {
  case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')" in
    *lite*|*haiku*) echo 0.05 ;;
    *pro*|*opus*)   echo 0.70 ;;
    *flash*|*sonnet*) echo 0.20 ;;
    *) echo 0.30 ;;
  esac
}
RATES=""
for m in $MODELS; do RATES="$RATES $(rate_for "$m")"; done
RATES="${RATES# }"



# ----------------------------------------------------------------------- scope
step "Choose scope"

SCOPE="${SCOPE:-}"
if [ -z "$SCOPE" ]; then
  cat <<'MENU'
    1) smoke  --   5 tasks per model   (~10 min, a few dollars) -- do this first
    2) subset --  25 tasks per model   (~40 min)
    3) full   -- 100 tasks per model   (~2.5 h)
MENU
  case "$(ask 'Selection' '1')" in
    1) SCOPE=smoke ;; 2) SCOPE=subset ;; 3) SCOPE=full ;; *) die "invalid selection" ;;
  esac
fi
case "$SCOPE" in
  smoke)  NTASKS=5;   SLICE=( --slice 0:5 ) ;;
  subset) NTASKS=25;  SLICE=( --slice 0:25 ) ;;
  full)   NTASKS=100; SLICE=() ;;
  *) die "unknown SCOPE: $SCOPE" ;;
esac
ok "scope: $SCOPE ($NTASKS tasks per model)"

WORKERS=$(ask "Parallel workers" "${WORKERS:-8}")
OUT_ROOT="results"

# Cost estimate, so nobody is surprised by the bill.
TOTAL=0
i=1
printf "\n  ${B}Estimated cost${N} ${DIM}(projection from per-task caps, not a quote)${N}\n"
for name in $NAMES; do
  rate=$(echo "$RATES" | cut -d' ' -f$i)
  sub=$("$PYBIN" -c "print(f'{$rate*$NTASKS:.2f}')")
  TOTAL=$("$PYBIN" -c "print(f'{$TOTAL+$sub:.2f}')")
  printf "    %-20s %3d tasks x \$%s = ${B}\$%s${N}\n" "$name" "$NTASKS" "$rate" "$sub"
  i=$((i+1))
done
printf "    %-20s %26s${B}\$%s${N}\n" "TOTAL" "" "$TOTAL"
printf "  ${DIM}Per-task caps: step_limit 80, cost_limit \$1.00 (configs/pydanticbench.yaml)${N}\n\n"

export MSWEA_GLOBAL_COST_LIMIT="${MSWEA_GLOBAL_COST_LIMIT:-$("$PYBIN" -c "print(int(float($TOTAL)*2)+10)")}"
info "global spend circuit-breaker set to \$$MSWEA_GLOBAL_COST_LIMIT"

confirm "Proceed?" || { printf "\n  Aborted. Nothing was spent.\n\n"; exit 0; }

# ----------------------------------------------------------------- image build
step "Building the evaluation environment"
IMAGE=pydanticbench:base
if docker image inspect "$IMAGE" >/dev/null 2>&1; then
  ok "image $IMAGE already exists (delete it to force a rebuild)"
else
  info "this takes ~10 minutes and ends by running pydantic's full test suite"
  docker build -f docker/Dockerfile --build-arg BASE_TAG=v2.13.4 -t "$IMAGE" . \
    || die "image build failed -- see README.md section 7"
  ok "image built"
fi

# The baseline must be green or every score is wrong. Verify in the built image.
step "Verifying the image baseline"
# Trust pytest's EXIT CODE, not a string match on its summary line. Parsing
# text here has burned this script twice: once matching "23 xfailed" as a
# failure, and once silently continuing when the suite could not start at all
# and printed nothing.
BASE_OUT=$(docker run --rm "$IMAGE" bash -lc \
  "cd /testbed && python -m pytest tests/ -q -p no:cacheprovider --tb=short \
   --ignore=tests/pydantic_core --ignore=tests/test_docs.py --ignore=tests/benchmarks \
   2>&1; echo \"PYTEST_EXIT=\$?\"" 2>&1)
# `|| true` is load-bearing: grep exits 1 when it matches nothing, and under
# `set -e` a failing command substitution aborts the script instantly -- which
# silently skipped the very error handling below on a broken image.
BASE_CODE=$(printf '%s' "$BASE_OUT" | sed -n 's/^PYTEST_EXIT=//p' | tail -1 || true)
BASE_SUMMARY=$(printf '%s' "$BASE_OUT" | grep -E '[0-9]+ (passed|failed|error)' | tail -1 || true)

if [ -z "$BASE_CODE" ]; then
  fail "could not run the test suite inside the image at all"
  printf '%s\n' "$BASE_OUT" | tail -25 | sed 's/^/      /'
  die "the image is unusable. Rebuild it: docker rmi $IMAGE && ./run.sh"
elif [ "$BASE_CODE" != "0" ]; then
  fail "the image baseline is NOT green (pytest exit $BASE_CODE)"
  printf '%s\n' "$BASE_OUT" | tail -25 | sed 's/^/      /'
  cat >&2 <<EOF

  Every score would be meaningless against a broken baseline. This usually means
  the image was built before a dependency fix. Rebuild it:

      docker rmi $IMAGE && ./run.sh

EOF
  exit 1
else
  ok "baseline green -- ${BASE_SUMMARY:-suite passed}"
fi

# ------------------------------------------------------- task/image agreement
step "Verifying the task set matches the image"
if "$PY" scripts/08_verify_tasks_apply.py --backend docker --image "$IMAGE"; then
  :
else
  die "task patches do not apply inside the image -- see the message above"
fi

# ------------------------------------------------------------------- integrity
step "Checking a task does not leak its own answer"
if ! "$PY" scripts/10_check_leaks.py --image "$IMAGE" -n 3; then
  die "the environment hands the agent the answer -- results would be meaningless"
fi

# -------------------------------------------------------------------- selftest
step "Verifying the scorer"
if [ -n "${SKIP_SELFTEST:-}" ]; then
  warn "SKIP_SELFTEST set -- results will be unverified"
elif confirm "Run the scorer self-test? (~2 min, strongly recommended)"; then
  "$PY" scripts/07_selftest.py --backend docker --image "$IMAGE" -n 2 \
    && ok "scorer verified -- see results/selftest.md" \
    || die "scorer self-test FAILED; do not trust results until this passes"
else
  warn "skipped -- results are unverified"
fi

# ------------------------------------------------------------------------- run
step "Running the benchmark"
i=1
for name in $NAMES; do
  model=$(echo "$MODELS" | cut -d' ' -f$i)
  printf "\n  ${B}[%d/3] %s${N} ${DIM}(%s)${N}\n" "$i" "$name" "$model"
  outdir="$OUT_ROOT/$name"
  mkdir -p "$outdir"

  # Resume vs. retry.
  #
  # mini-swe-agent treats any instance present in preds.json as finished, even
  # if it finished by crashing. A previous failed run therefore makes the next
  # run silently skip everything and report all-zero scores -- which looks like
  # three terrible models rather than a stale directory. Detect that case and
  # offer to clear it, instead of relying on the operator to remember `rm -rf`.
  if [ -f "$outdir/preds.json" ]; then
    counts=$("$PY" - "$outdir/preds.json" <<'PYCOUNT' 2>/dev/null || echo "0 0"
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    print(0, 0); raise SystemExit
total = len(d)
empty = sum(1 for v in d.values() if not (v.get("model_patch") or "").strip())
print(total, empty)
PYCOUNT
)
    done_n=$(printf '%s' "$counts" | awk '{print $1}')
    empty_n=$(printf '%s' "$counts" | awk '{print $2}')

    if [ "${done_n:-0}" -gt 0 ] && [ "${done_n:-0}" = "${empty_n:-0}" ]; then
      warn "a previous run recorded $done_n instances and every one produced an empty patch"
      info "that is the signature of a failed run, not of a bad model"
      if [ -n "${FRESH:-}" ] || confirm_yes "Discard those results and re-run $name from scratch?"; then
        rm -f "$outdir/preds.json"
        rm -rf "$outdir"/pydanticbench__*
        ok "cleared stale results for $name"
      else
        warn "keeping them -- this model will be skipped entirely"
      fi
    elif [ "${done_n:-0}" -gt 0 ]; then
      info "resuming: $done_n instances already complete ($empty_n with no patch)"
    fi
  fi

  # ${SLICE[@]+...} guards against bash 3.2 (macOS default) treating an empty
  # array as an unbound variable under `set -u`.
  "${MSWEA[@]}" --subset tasks/hf --split train \
      --model "$model" \
      --config configs/pydanticbench.yaml \
      --environment-class docker \
      --workers "$WORKERS" \
      ${SLICE[@]+"${SLICE[@]}"} \
      --output "$outdir" \
    || warn "$name run ended early; scoring whatever completed"

  if [ ! -f "$outdir/preds.json" ]; then
    fail "$name produced no predictions -- skipping scoring"
    info "the agent never completed an instance; check the error above"
    i=$((i+1))
    continue
  fi

  printf "  ${DIM}scoring %s ...${N}\n" "$name"
  "$PY" scripts/05_score.py \
      --tasks tasks/tasks.jsonl \
      --preds "$outdir/preds.json" \
      --baseline tasks/baseline_failures.json \
      --backend docker \
      --out "$outdir/scores.json" 2>&1 | tail -1
  i=$((i+1))
done

# ---------------------------------------------------------------------- report
step "Building the results summary"
if ! "$PY" scripts/06_report.py --results "$OUT_ROOT" --out "$OUT_ROOT" >/dev/null 2>&1; then
  die "no runs produced scores -- nothing to summarise. See the errors above."
fi
ok "written to $OUT_ROOT/summary.md"

printf "\n"
cat "$OUT_ROOT/summary.md"
cat <<EOF

${B}Done.${N}

  Summary      $OUT_ROOT/summary.md
  Per-model    $OUT_ROOT/<model>/scores.json  (per-task detail)
  Trajectories $OUT_ROOT/<model>/*/           (what each agent actually did)

${B}How to read this:${N} the by-tier table is the one that matters. Monotonic decay
from easy to hard, with the strongest model well under 50% on hard, means the
benchmark discriminates. If all three cluster high, it is saturated -- see
REPORT.md section 5.

EOF

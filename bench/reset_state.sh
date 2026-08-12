#!/usr/bin/env bash
# Return /testbed to the pristine base state. Used before applying a task's
# setup patch, and before every scoring run.
set -euo pipefail
cd /testbed
git checkout -f pydanticbench-base >/dev/null 2>&1 || true
git clean -fdx -e .git >/dev/null 2>&1 || true
rsync -a --delete /opt/pristine/tests/ /testbed/tests/
rsync -a --delete /opt/pristine/pydantic/ /testbed/pydantic/

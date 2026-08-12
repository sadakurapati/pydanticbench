#!/usr/bin/env bash
# Return /testbed to the pristine base state, before a task's setup patch is
# applied and before every scoring run.
#
# Source is restored from the git tag rather than from a filesystem snapshot: a
# readable clean copy of pydantic/ inside the image would let an agent recover
# the injected defect with a single `diff -r`.
set -euo pipefail
cd /testbed
git checkout -f pydanticbench-base >/dev/null 2>&1
git clean -fdx -e .git >/dev/null 2>&1
rsync -a --delete /opt/pristine/tests/ /testbed/tests/

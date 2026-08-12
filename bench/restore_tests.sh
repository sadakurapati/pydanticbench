#!/usr/bin/env bash
# Restore the grading criteria from the pristine snapshot. Run AFTER the model
# patch is applied and BEFORE any test executes, so edits to tests/ are inert.
set -euo pipefail
rsync -a --delete /opt/pristine/tests/ /testbed/tests/

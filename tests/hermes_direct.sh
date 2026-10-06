#!/usr/bin/env bash
# Invoke the Hermes checkout without going through the shared launcher, so a run
# under a different HERMES_HOME cannot republish ~/.hermes/bin/hermes with a
# baked-in path (which would break every later `hermes` invocation).
PY=$(echo /home/rumlyne/.hermes/tools/python-*/bin/python3)
REPO_ROOT="${HERMES_REPO_ROOT:-/home/rumlyne/.hermes/hermes-agent}"
exec "$PY" -I -c '
import os, sys
os.environ.pop("PYTHONHOME", None)
os.environ.pop("PYTHONPATH", None)
sys.path.insert(0, os.environ["HERMES_REPO_ROOT"])
import hermes_bootstrap
sys.argv[0] = "hermes"
from hermes_cli.main import main
sys.exit(main())
' "$@"

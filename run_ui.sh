#!/bin/bash
# Deforestation Alerts: sources the .env file and runs ui.ipynb via voila — the SEPAL app entry path.
# Open http://127.0.0.1:PORT/voila/render/ui.ipynb
# Usage: ./run_ui.sh [--port PORT]
# If no port is provided, defaults to 8911 (`nox -s app` runs plain `solara run` on 8900)

PORT="8911"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port)
      PORT="$2"
      shift 2
      ;;
    --port=*)
      PORT="${1#--port=}"
      shift
      ;;
    -h|--help)
      echo "Usage: $0 [--port PORT]"
      exit 0
      ;;
    *)
      echo "Unknown argument: $1"
      exit 1
      ;;
  esac
done

# Run from the script's own directory so relative paths resolve
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Make the module root importable (so `import gui...` works)
export PYTHONPATH="$SCRIPT_DIR${PYTHONPATH:+:$PYTHONPATH}"

if [[ -f .env ]]; then
  while IFS= read -r line || [[ -n $line ]]; do
    [[ $line =~ ^#.*$ || -z $line ]] && continue

    if [[ $line =~ ^([^=]+)=(.*)$ ]]; then
      name="${BASH_REMATCH[1]}"
      value="${BASH_REMATCH[2]}"

      # Remove quotes if present
      value="${value#\'}"
      value="${value%\'}"
      value="${value#\"}"
      value="${value%\"}"

      export "$name=$value"
    fi
  done < .env
else
  echo "Note: no .env file found in $SCRIPT_DIR, skipping env load."
fi

# GDAL writes its temp files to the CWD when CPL_TMPDIR/TMPDIR/TEMP are unset,
# and on SEPAL the CWD is the read-only shared module mount -- so point it at a
# writable per-user scratch dir. Set after .env so an operator value wins; the
# uid suffix keeps us off another user's directory on a shared /tmp.
export CPL_TMPDIR="${CPL_TMPDIR:-${TMPDIR:-/tmp}/deforestation_alerts_gdal_$(id -u)}"
mkdir -p "$CPL_TMPDIR"

# Download links. On SEPAL the sandbox's own jupyter serves the user's $HOME
# under /api/sandbox/jupyter/files, whichever port this script renders on.
# That is the app's default (DEFORESTATION_ALERTS_DOWNLOAD_BASE/_ROOT unset):
# the URL is root-relative, so it follows whichever SEPAL (sepal.io,
# test.sepal.io) is serving the page, and SEPAL's login cookie authorizes it.
# The sandbox sets LOCALTILESERVER_CLIENT_PREFIX before this script runs,
# which is how it knows it is on SEPAL and leaves downloads alone there.
# Operator values always win.
#
# Locally this jupyter serves SCRIPT_DIR at /files. When the project data
# lives outside it (the default ~/module_results), a read-only file server on
# loopback serves the data folder instead, reached through jupyter-server-proxy
# at /proxy/<port> like the map tiles, and stopped when this script exits.
if [[ -z ${LOCALTILESERVER_CLIENT_PREFIX:-} ]]; then
  DATA_DIR="$(python -c 'from deforestation_alerts.project.paths import data_dir; print(data_dir())')"
  if [[ -z ${DEFORESTATION_ALERTS_DOWNLOAD_BASE:-} && -z ${DEFORESTATION_ALERTS_DOWNLOAD_ROOT:-} \
        && -n $DATA_DIR && $DATA_DIR != "$SCRIPT_DIR"/* ]]; then
    mkdir -p "$DATA_DIR"
    FILES_PORT="$(python -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')"
    python -m http.server "$FILES_PORT" --bind 127.0.0.1 --directory "$DATA_DIR" >/dev/null 2>&1 &
    FILES_PID=$!
    trap 'kill "$FILES_PID" 2>/dev/null' EXIT
    export DEFORESTATION_ALERTS_DOWNLOAD_BASE="/proxy/$FILES_PORT"
    export DEFORESTATION_ALERTS_DOWNLOAD_ROOT="$DATA_DIR"
    echo "Downloads: serving $DATA_DIR at /proxy/$FILES_PORT"
  fi
  export DEFORESTATION_ALERTS_DOWNLOAD_BASE="${DEFORESTATION_ALERTS_DOWNLOAD_BASE:-/files}"
  export DEFORESTATION_ALERTS_DOWNLOAD_ROOT="${DEFORESTATION_ALERTS_DOWNLOAD_ROOT:-$SCRIPT_DIR}"
fi

# Serve voila from inside jupyter-server, as SEPAL does, and route map tiles
# through jupyter-server-proxy's /proxy/{port} like SEPAL's
# LOCALTILESERVER_CLIENT_PREFIX. Standalone `voila ui.ipynb` loads no server
# extensions, so that route would 404 there. Local only: no token, loopback bind.
export LOCALTILESERVER_CLIENT_PREFIX="${LOCALTILESERVER_CLIENT_PREFIX:-/proxy/{port}}"
jupyter server --port="$PORT" --no-browser \
  --ServerApp.ip=127.0.0.1 --IdentityProvider.token= \
  --ServerApp.root_dir="$SCRIPT_DIR" \
  --ServerApp.default_url=/voila/render/ui.ipynb

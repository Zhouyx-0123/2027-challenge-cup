#!/bin/sh
set -eu
cd "$(dirname "$0")"
if command -v python3 >/dev/null 2>&1; then
    exec python3 -m carbon_disclosure "$@"
elif command -v node >/dev/null 2>&1; then
    exec node carbon_disclosure/server.cjs
else
    printf '%s\n' 'Install Python 3.10+ or Node.js 18+, or open the offline HTML file.'
    exit 1
fi

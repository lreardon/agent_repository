#!/bin/bash
# Simple script to serve the Agent Registry documentation site locally

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEB_DIR="$(dirname "$SCRIPT_DIR")/web"

echo "🚀 Starting Agent Registry Documentation site..."
echo "📁 Serving from: $WEB_DIR"
echo ""
echo "Open your browser and visit:"
echo "  http://localhost:8000"
echo ""
echo "Press Ctrl+C to stop"
echo ""

# Check if Python 3 is available
if command -v python3 &> /dev/null; then
    python3 -m http.server 8000 --directory "$WEB_DIR"
# Fall back to python
elif command -v python &> /dev/null; then
    python -m http.server 8000 --directory "$WEB_DIR"
else
    echo "❌ Error: Neither python3 nor python found in PATH"
    exit 1
fi

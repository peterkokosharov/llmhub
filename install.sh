#!/usr/bin/env bash
set -e
DEST="$HOME/.local/share/llmhub"
mkdir -p "$DEST"
cp llmhub.py models.json secret.py "$DEST/"
echo "Installed to $DEST"
echo ""
echo "Store your API keys (once):"
echo "  python3 $DEST/secret.py set nvidia/personal/api_key"
echo "  python3 $DEST/secret.py set gemini/personal/api_key"
echo "  python3 $DEST/secret.py set openai/personal/api_key"
echo ""
echo "Then close opencode, run: python3 $DEST/llmhub.py, start opencode."

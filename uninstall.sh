#!/usr/bin/env bash
DEST="$HOME/.local/share/llmhub"
if [ -d "$DEST" ]; then
    rm -rf "$DEST"
    echo "Removed $DEST"
else
    echo "Not installed at $DEST"
fi

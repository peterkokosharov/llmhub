#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
"""llmhub — write free-tier LLM providers into opencode's config.

Usage:
    python3 llmhub.py              # write all providers
    python3 llmhub.py --dry-run    # show what would change
    python3 llmhub.py --list       # list available models
"""
from __future__ import annotations

import json
import os
import sys
import shutil
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
MODELS_FILE = HERE / "models.json"

# opencode config location
def _config_path() -> Path:
    if os.environ.get("OPENCODE_CONFIG"):
        return Path(os.environ["OPENCODE_CONFIG"])
    if sys.platform == "win32":
        base = Path(os.environ.get("USERPROFILE", "~")).expanduser()
        p = base / ".config" / "opencode" / "opencode.json"
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        base = Path(xdg) if xdg else Path("~").expanduser() / ".config"
        p = base / "opencode" / "opencode.json"
    return p


def _read_config(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  warn: could not read {path}: {e}")
        return {}


def _write_config(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    bak = path.with_suffix(f".bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
    if path.exists():
        shutil.copy2(path, bak)
        print(f"  backup: {bak}")
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _get_key(env_name: str) -> str | None:
    val = os.environ.get(env_name)
    if val:
        return val
    # try secret.py if present
    secret_py = HERE / "secret.py"
    if secret_py.exists():
        import importlib.util
        spec = importlib.util.spec_from_file_location("secret", secret_py)
        mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        try:
            return mod.get(env_name.lower().replace("_api_key", "/personal/api_key"))
        except Exception:
            pass
    return None


def _build_provider_entry(name: str, info: dict, key: str | None) -> dict:
    entry: dict = {
        "name": name.upper(),
        "package": "@opencode/ai/providers/openai-compatible",
        "env": [info["key_env"]],
        "settings": {"baseURL": info["base_url"]},
        "models": {},
    }
    if key:
        entry["settings"]["apiKey"] = key
    for model_id, meta in info["models"].items():
        model_entry: dict = {"name": meta.get("name", model_id)}
        if "modelID" in meta:
            model_entry["modelID"] = meta["modelID"]
        entry["models"][model_id] = model_entry
    return entry


def main(argv: list[str]) -> int:
    dry_run = "--dry-run" in argv
    list_only = "--list" in argv

    models_data: dict = json.loads(MODELS_FILE.read_text(encoding="utf-8"))

    if list_only:
        for prov, info in models_data.items():
            print(f"\n[{prov.upper()}]  base: {info['base_url']}")
            for mid, meta in info["models"].items():
                print(f"  {mid:55s}  {meta.get('class','?'):8s}  {meta.get('name','')}")
        return 0

    config_path = _config_path()
    config = _read_config(config_path)
    providers = config.setdefault("providers", {})

    added = 0
    for prov_name, prov_info in models_data.items():
        key = _get_key(prov_info["key_env"])
        if not key:
            print(f"  skip {prov_name}: {prov_info['key_env']} not set")
            print(f"    set it with:  python3 secret.py set "
                  f"{prov_info['key_env'].lower().replace('_api_key', '/personal/api_key')}")
            continue
        entry = _build_provider_entry(prov_name, prov_info, key)
        providers[prov_name] = entry
        n = len(prov_info["models"])
        print(f"  {'(dry) ' if dry_run else ''}add {prov_name}: {n} models")
        added += n

    if added == 0:
        print("\nNo providers added. Store your keys first:")
        for _, info in models_data.items():
            k = info["key_env"].lower().replace("_api_key", "/personal/api_key")
            print(f"  python3 secret.py set {k}")
        return 1

    if not dry_run:
        _write_config(config_path, config)
        print(f"\n  written: {config_path}")
        print("  run:     opencode  (or python3 run_opencode.py)")
    else:
        print(f"\n  would write: {config_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

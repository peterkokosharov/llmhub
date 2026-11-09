# llmhub

Adds free-tier LLM providers (NVIDIA, Gemini) to opencode's config in one command.

## Start

```bash
# store keys once (goes into OS keyring — never into a file)
python3 secret.py set nvidia/personal/api_key
python3 secret.py set gemini/personal/api_key

# write providers into opencode.json
python3 llmhub.py

# open opencode
opencode
```

## What you get

| Class  | Model                                | Provider |
|--------|--------------------------------------|----------|
| OPUS   | Nemotron Ultra 253B                  | NVIDIA   |
| SONNET | Nemotron Super 49B, Llama 3.3 70B    | NVIDIA   |
| HAIKU  | Gemini Flash Lite, GPT-5 Nano        | Gemini/OpenAI |
| FABLE  | Gemini 2.5 Pro, GPT-6 Luna           | Gemini/OpenAI |

All free. NVIDIA requires a free account at build.nvidia.com.
Gemini requires a free account at aistudio.google.com.
OpenAI requires opt-in to data-sharing at platform.openai.com/settings/privacy
(1M tokens/day on large models, 10M/day on mini — prompts may be used for training).

## Update model list

Edit `models.json`. Run `python3 llmhub.py --list` to see all configured models.
Run `python3 llmhub.py` again after editing.

NVIDIA models rotate — verify names at build.nvidia.com before adding.

## Files

- `llmhub.py` — writes opencode.json
- `models.json` — provider and model list
- `secret.py` — OS keyring helper (copy from ai-first-workspace, or use env vars)

## License

MIT — Peter Kokosharov <peter.kokosharov@gmail.com>

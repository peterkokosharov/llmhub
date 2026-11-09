# llmhub

Adds free-tier LLM providers (NVIDIA, Gemini, OpenAI) to opencode's config in one command.

## Step 1 — Get your API keys

You need up to three keys. All are free.

### NVIDIA (20 models, no daily limit)

1. Go to **https://build.nvidia.com**
2. Sign up for a free account
3. Click your avatar → **API Keys** → **Generate API Key**
4. Copy the key — it starts with `nvapi-`

### Gemini (Google AI Studio, free tier)

1. Go to **https://aistudio.google.com**
2. Sign in with your Google account
3. Click **Get API key** → **Create API key**
4. Copy the key — it starts with `AIzaSy`

### OpenAI (optional — data-sharing, 1M tokens/day on large models)

1. Go to **https://platform.openai.com**
2. Sign up or sign in
3. Click your avatar → **API keys** → **Create new secret key**
4. Copy the key — it starts with `sk-`
5. Enable free data-sharing tier at **https://platform.openai.com/settings/privacy**
   (your prompts may be used for training — do not use for sensitive data)

---

## Step 2 — Save the keys on your computer

### Windows

Open **Command Prompt** (Win + R → type `cmd` → Enter) and run:

```bat
setx NVIDIA_API_KEY "nvapi-xxxxxxxxxxxxxxxx"
setx GEMINI_API_KEY "AIzaSyxxxxxxxxxxxxxxx"
setx OPENAI_API_KEY "sk-xxxxxxxxxxxxxxxx"
```

Replace the placeholder values with your real keys.
Close the Command Prompt window and open a new one — `setx` only takes effect in new windows.

### Linux / Q4OS

Open a terminal and add these lines to `~/.bashrc` (or `~/.profile`):

```bash
export NVIDIA_API_KEY="nvapi-xxxxxxxxxxxxxxxx"
export GEMINI_API_KEY="AIzaSyxxxxxxxxxxxxxxx"
export OPENAI_API_KEY="sk-xxxxxxxxxxxxxxxx"
```

Then run `source ~/.bashrc` (or log out and back in).

---

## Step 3 — Install llmhub

```bash
git clone https://github.com/peterkokosharov/llmhub.git
cd llmhub

# Linux / Q4OS:
bash install.sh

# Windows:
install.bat
```

Files are copied to:
- **Windows:** `%USERPROFILE%\llmhub\`
- **Linux / Q4OS:** `~/.local/share/llmhub/`

---

## Step 4 — Add the models to opencode

```bash
# 1. Close opencode (desktop app or CLI)
# 2. Run llmhub:
python3 ~/.local/share/llmhub/llmhub.py        # Linux
python  %USERPROFILE%\llmhub\llmhub.py          # Windows
# 3. Start opencode
opencode
```

**See the models in opencode:**
- Desktop: open the model listbox in the prompt area
- CLI: type `/models`

---

## What you get

| Class  | Model                             | Provider      |
|--------|-----------------------------------|---------------|
| OPUS   | Nemotron Ultra 253B               | NVIDIA        |
| SONNET | Nemotron Super 49B, Llama 3.3 70B | NVIDIA        |
| HAIKU  | Gemini 3.5 Flash Lite, GPT-5 Nano | Gemini/OpenAI |
| FABLE  | Gemini 3.1 Pro, GPT-6 Luna        | Gemini/OpenAI |

---

## Update model list

Edit `models.json`. Run `python3 llmhub.py --list` to see all configured models.
NVIDIA models rotate — verify names at build.nvidia.com before adding.

---

## Files

- `llmhub.py` — writes opencode's config
- `models.json` — provider and model list
- `secret.py` — optional keyring helper (env vars are enough)
- `install.sh` / `install.bat` — copy files to the standard location
- `uninstall.sh` / `uninstall.bat` — remove them

---

## License

MIT — Peter Kokosharov <peter.kokosharov@gmail.com>

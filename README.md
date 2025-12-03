# Calm Mail 🛡️

## Credit
Created by **Arzius**.

**The Sovereign AI Email Agent.**

Calm Mail is a local, privacy-first email agent that runs entirely on your Windows machine. It uses a local Large Language Model (via Ollama) to automatically sort, label, and clean your Gmail inbox.

**Zero Data leaves your device.** No subscription fees. No cloud servers reading your emails.

## 🚀 Features

- **🔒 100% Sovereign:** Powered by your local hardware (Llama 3 / Qwen via Ollama).
- **🎯 Hybrid Routing:**
  - **Sniper Mode:** Hardcode specific emails to folders (e.g., `boss@corp.com` → `Work`) for instant, zero-latency sorting.
  - **AI Mode:** Unrecognized emails are analyzed by the LLM to detect context (Bills, Newsletters, Spam).
- **🗑️ The Kill List:** Automatically incinerates spam from Quora, Reddit, and tracking bots before you even see them.
- **⚡ Zero-Code UI:** A modern dashboard to manage your rules, whitelist, and monitoring.

## 📦 Installation (For Users)

1. **Install Ollama:** Download and install from [ollama.com](https://ollama.com).
2. **Pull a Model:** Open terminal and run: ollama pull qwen3:8b *(Or `llama3`, `mistral`, etc. You can change this in Settings).*
3. **Download Calm Mail:** Go to the [Releases Page](../../releases) and download `CalmMail.exe`.
4. **Setup Gmail API:**
- Go to Google Cloud Console -> Enable Gmail API.
- Create OAuth Credentials (Desktop App).
- Download `credentials.json`.
5. **Run Calm Mail:**
- Launch the app.
- Go to **Settings** -> **Import credentials.json**.
- Click **START CALM MAIL**.

## 🛠️ Build from Source (For Developers)

If you want to modify the code or build it yourself:
1. Clone the repo:
git clone https://github.com/Arzius/Calm-Mail.git
cd Calm-Mail
2. Install dependencies
pip install -r requirements.txt
3. Run the app
python main.py
4. Build .exe (Optional)
pip install pyinstaller
pyinstaller --name "CalmMail" --onefile --noconsole main.py

## 🛡️ Privacy Policy

Calm Mail is **Local-Only Software**.
- It connects **directly** from your computer to Google's Gmail API.
- It sends email text **directly** to your local Ollama instance (localhost:xxxxx).
- No data is ever sent to us or any third-party server.
- Your `credentials.json` and `token.json` stay on your hard drive.

## 📄 License

MIT License. Free to use, modify, and distribute.


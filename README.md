# VoxCap 🎬

Offline, browser-based **speech → CapCut-style captions** generator.

- **Whisper large-v3** (faster-whisper) — runs fully offline on your GPU (RTX), CPU fallback built in
- Word-level timestamps → short punchy CapCut-style caption chunks
- Karaoke-style live preview (current word highlighted) over your video/audio
- Hindi, English + 99 languages, auto-detect
- Export **SRT / VTT / TXT / JSON**
- Mic recording directly in the browser
- FastAPI backend + vanilla JS frontend — easy to deploy to a real website later

## Run

- **`setup.bat`** — one-time setup on a new PC (installs Python if missing + dependencies)
- **`start-local.bat`** — local only: server + browser at http://127.0.0.1:8765 (runs setup automatically first time)
- **`start-live.bat`** — LIVE on the internet at **https://srt.aipoint.online** (server + Cloudflare tunnel; keep the window open)

The large-v3 model is already downloaded to the Hugging Face cache — transcription is fully offline. Only the tunnel needs internet.

## Kisi aur ko dena ho (share)

Download link (hamesha latest): https://github.com/soubickdas-lab/VoxCap/releases/latest/download/VoxCap-share.zip

Receiver bas:

1. Zip extract kare (kahin bhi — paths relative hain)
2. `start-local.bat` double-click kare — setup apne aap ho jayega
3. Pehli transcription par model download hota hai: NVIDIA GPU wale PC par large-v3 (~3 GB), bina GPU wale par medium (~1.5 GB, CPU par slow but works)
4. **Update lene ke liye:** `update.bat` double-click — latest version GitHub se aa jayega (settings/venv/model sab safe rehte hain)

Naya version dena ho to bas `publish.bat` chalao — zip rebuild + push + release update, sab ek saath.

`start-live.bat` aur `cloudflare-config.yml` share zip me nahi hain — wo aapke Cloudflare account/domain se bandhe hain.

## Cloudflare tunnel

- Tunnel name: `voxcap` (id `20e5e83d-7a02-4ab4-914b-e2731dfe021b`), config: `cloudflare-config.yml`
- DNS: CNAME `srt.aipoint.online` → tunnel (already routed)
- Credentials: `C:\Users\Soubickdas\.cloudflared\` (keep secret)

## Config

- `VOXCAP_MODEL` env var — model name (`large-v3` default; use `distil-large-v3` or `medium` for lower VRAM / CPU)
- Words per caption + language are set in the UI per job.

## Structure

- `server.py` — FastAPI app: upload → background transcription job → word timestamps + chunking
- `static/index.html` — the whole frontend (dark CapCut-style UI)

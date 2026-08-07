# VoxCap 🎬

**Offline speech → CapCut-style SRT captions.** Audio ya video daalo, chhote punchy caption chunks wali SRT file apne aap ban ke download ho jati hai — bilkul CapCut auto-captions jaisi. Sab kuch aapke apne PC par chalta hai (Whisper large-v3), koi API key nahi, koi per-minute charge nahi.

**Live demo:** https://srt.aipoint.online
**Direct download (latest):** https://github.com/soubickdas-lab/VoxCap/releases/latest/download/VoxCap-share.zip

---

## Features

| Feature | Detail |
|---|---|
| 🎯 CapCut-style captions | 3–4 word ke chhote chunks, pause aur sentence ke hisaab se break |
| 🧩 SRT Matcher mode | Apna script/beats do — har line ek caption banti hai, audio se time-match hoke |
| 🗣 99+ languages | Hindi, English, Urdu, Punjabi, Bengali, Tamil, Telugu + auto-detect |
| 🖥 Fully offline | Whisper model aapke PC par chalta hai — audio kahin upload nahi hota (apne hi server par jata hai) |
| ⚡ GPU accelerated | NVIDIA GPU par large-v3 (best quality); GPU nahi to CPU par medium model |
| 📦 Big file support | 20 MB chunked upload — GB-size videos bhi chalti hain (Cloudflare limit bypass) |
| 🎤 Mic recording | Browser me directly record karke captions banao |
| ✏️ Live SRT preview | Download se pehle preview + copy button |
| 🔄 One-click update | `update.bat` se hamesha latest version |

---

## Do Modes

### 🎙 Audio → SRT (default)

Audio/video drop karo — bas. Transcription apne aap start hoti hai, CapCut-style chunks bante hain, SRT auto-download hoti hai aur preview dikh jata hai.

- **Words per caption** slider (1–8) se chunk size control karo
- Chunk break hota hai: max words par, lambi silence (0.6s+) par, ya sentence khatam hone par

### 🧩 SRT Matcher

Jab aapke paas pehle se script ya beat-sheet hai aur chahte ho ki **SRT ki har entry aapki text line se match kare** — timing audio se aaye.

Text box me aisa kuch daalo (serial numbers aur `SN Beat` header apne aap hat jate hain):

```
SN	Beat
1	Hook – attention grab
2	Topic introduce
3	Main problem
...
```

Phir audio drop karo. Timing do tarike se milti hai (apne aap decide hota hai):

1. **Text = actual bola gaya script** → transcript se word-by-word alignment, har line ka **exact time span** milta hai. (Best result isi me aata hai.)
2. **Text = sirf beat labels** (jo audio me bole nahi gaye) → audio lines ki lambai ke hisaab se **proportionally** divide hota hai.

> 💡 Tip: beats ke saath actual script bhi likho (`1  Hook - Kya aapne kabhi socha hai...`) to exact timing milegi.

---

## Setup (naye PC par)

**Requirements:** Windows 10/11, internet (sirf setup + pehli baar model download ke liye). NVIDIA GPU optional hai — ho to best, na ho to CPU par bhi chalta hai.

1. [Zip download karo](https://github.com/soubickdas-lab/VoxCap/releases/latest/download/VoxCap-share.zip) aur kahin bhi extract karo
2. **`start-local.bat`** double-click karo
3. Pehli baar me apne aap hoga: Python 3.12 install (agar nahi hai), dependencies install (~1 GB), phir browser me http://127.0.0.1:8765 khul jayega
4. Pehli transcription par Whisper model download hota hai:
   - NVIDIA GPU wale PC par: **large-v3** (~3 GB)
   - Bina GPU: **medium** (~1.5 GB) — CPU par slow hai lekin chalta hai

Uske baad sab **fully offline** hai.

### Files kya hain

| File | Kaam |
|---|---|
| `start-local.bat` | Server start + browser open (pehli baar setup bhi) |
| `setup.bat` | Manual setup (Python + dependencies) — normally zaroorat nahi |
| `update.bat` | GitHub se latest version le aao (venv/model safe rehte hain) |
| `server.py` | FastAPI backend — transcription, chunking, alignment |
| `static/index.html` | Pura frontend (single file) |
| `requirements.txt` | Python dependencies |

*(Sirf owner ke PC par: `start-live.bat` + `cloudflare-config.yml` — public website chalane ke liye, repo me nahi hain.)*

---

## Update kaise kare

**Users:** `update.bat` double-click — latest code GitHub se aa jata hai, dependencies auto-check hoti hain. Aapka venv aur downloaded model dobara download **nahi** hote.

**Developer (owner):** `publish.bat` double-click → commit message likho → done. Wo khud: fresh `VoxCap-share.zip` banata hai → GitHub push → release asset replace. Download link kabhi nahi badalta (`releases/latest/download/...`), isliye sabko hamesha latest milta hai.

---

## Live website (owner only)

Site aapke PC se Cloudflare Tunnel ke through serve hoti hai:

- **`start-live.bat`** — server + tunnel dono start (window khuli rakhni hai; PC hi server hai)
- Domain: `srt.aipoint.online` → CNAME → tunnel `voxcap`
- Tunnel config: `cloudflare-config.yml` (local-only, gitignored), credentials `%USERPROFILE%\.cloudflared\`
- Cloudflare free plan ~100 MB/request limit karta hai — isliye frontend 20 MB chunks me upload karta hai, server jod deta hai

---

## Architecture

```
Browser (static/index.html)
  │  1. POST /api/upload/init            → upload_id
  │  2. POST /api/upload/chunk (xN)      → 20 MB pieces append
  │  3. POST /api/upload/finish          → job_id  (language, max_words, script)
  │  4. GET  /api/jobs/{job_id}          → status/progress poll (800ms)
  ▼
FastAPI (server.py)
  └─ background thread
       ├─ faster-whisper transcribe (word_timestamps=True, vad_filter=True)
       ├─ script diya hai?
       │    ├─ HAAN → align_script_to_words()  [SRT Matcher]
       │    │         edit-distance DP: script tokens × transcript words
       │    │         match <30% → proportional split fallback
       │    └─ NAHI → chunk_words()  [CapCut chunking]
       │              break: max_words / max_chars(32) / gap>0.6s / sentence end
       └─ result: words[] + chunks[] → frontend SRT banata hai
```

**Model selection** (`server.py`):
- CUDA mila → `large-v3` float16 (env `VOXCAP_MODEL` se badal sakte ho)
- CUDA nahi → `medium` int8 CPU (env `VOXCAP_CPU_MODEL`)
- Windows par CUDA DLLs pip ke `nvidia-cublas-cu12`/`nvidia-cudnn-cu12` packages se load hoti hain (`_register_cuda_dlls`)

### API endpoints

| Method | Path | Body (form) | Return |
|---|---|---|---|
| POST | `/api/transcribe` | `file`, `language`, `max_words` | `{job_id}` (chhoti files, single request) |
| POST | `/api/upload/init` | `filename` | `{upload_id}` |
| POST | `/api/upload/chunk` | `upload_id`, `chunk` | `{ok, size}` |
| POST | `/api/upload/finish` | `upload_id`, `language`, `max_words`, `script` | `{job_id}` |
| GET | `/api/jobs/{id}` | — | `{status, progress, device, result?}` |

`status`: `queued` → `loading_model` → `transcribing` → `done` / `error`. `result` me: `mode` (`auto`/`matcher`), `language`, `duration`, `words[]` (word-level timestamps), `chunks[]` (`{start, end, text}`).

---

## Config (environment variables)

| Var | Default | Kaam |
|---|---|---|
| `VOXCAP_MODEL` | `large-v3` | GPU model (`distil-large-v3`, `medium`, `small`...) |
| `VOXCAP_CPU_MODEL` | `medium` | CPU fallback model |

Port badalna ho to `server.py` me last line (`port=8765`).

---

## Troubleshooting

- **"Python install nahi ho paya"** → https://python.org se Python 3.12 install karo (Add to PATH ✓), phir `setup.bat`
- **GPU hai par CPU use ho raha hai** → server window me `[voxcap] CUDA unavailable` wala reason dekho; NVIDIA driver update karo; `pip install -r requirements.txt` dobara
- **Pehli transcription bahut der tak "Model load..."** → model download ho raha hai (3 GB / 1.5 GB) — sirf pehli baar
- **Site pe upload fail (live)** → `start-live.bat` wali dono windows khuli hain? PC online hai?
- **Update ke baad kuch toota** → `update.bat` dobara chalao; phir bhi issue ho to folder delete karke fresh zip extract karo (model cache `%USERPROFILE%\.cache\huggingface` me hai, wo safe rahega)

---

## Tech stack

Python 3.12 · FastAPI · [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (CTranslate2) · Whisper large-v3 · vanilla JS frontend (no framework) · Cloudflare Tunnel

Built with [Claude Code](https://claude.com/claude-code) 🤖

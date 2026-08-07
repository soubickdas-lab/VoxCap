"""VoxCap — browser-based CapCut-style caption generator.

FastAPI backend: accepts an audio/video upload, transcribes it offline with
faster-whisper (large-v3 on GPU when available), and returns word-level
timestamps grouped into short CapCut-style caption chunks.
"""

import os
import re
import sys
import site
import tempfile
import threading
import uuid
from pathlib import Path

# ctranslate2 on Windows needs the cuBLAS / cuDNN DLLs shipped in the nvidia
# pip packages — they are not on PATH by default.
def _register_cuda_dlls() -> None:
    if sys.platform != "win32":
        return
    for base in site.getsitepackages():
        nvidia = Path(base) / "nvidia"
        if not nvidia.is_dir():
            continue
        for sub in nvidia.iterdir():
            for dll_dir in (sub / "bin", sub / "lib"):
                if dll_dir.is_dir():
                    os.add_dll_directory(str(dll_dir))
                    os.environ["PATH"] = str(dll_dir) + os.pathsep + os.environ["PATH"]


_register_cuda_dlls()

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

APP_DIR = Path(__file__).parent
MODEL_NAME = os.environ.get("VOXCAP_MODEL", "large-v3")
# GPU na ho to chhota model — CPU par large-v3 bahut slow hota hai
CPU_MODEL_NAME = os.environ.get("VOXCAP_CPU_MODEL", "medium")

app = FastAPI(title="VoxCap")

_model = None
_model_lock = threading.Lock()
_model_device = None

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _get_model():
    global _model, _model_device
    with _model_lock:
        if _model is not None:
            return _model
        from faster_whisper import WhisperModel

        try:
            _model = WhisperModel(MODEL_NAME, device="cuda", compute_type="float16")
            _model_device = "cuda"
        except Exception as exc:  # no CUDA / missing DLLs → CPU fallback
            print(f"[voxcap] CUDA unavailable ({exc}); falling back to CPU int8 ({CPU_MODEL_NAME})")
            _model = WhisperModel(CPU_MODEL_NAME, device="cpu", compute_type="int8")
            _model_device = "cpu"
        return _model


def _update_job(job_id: str, **fields) -> None:
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id].update(fields)


_SENT_END = (".", "?", "!", "。", "؟", "।")
_SOFT_END = (",", ";", ":", "،", "—", "-")


def chunk_words(words: list[dict], max_words: int, max_chars: int = 42,
                hard_gap: float = 0.45) -> list[dict]:
    """CapCut-style chunking: pehle saans/pause par todo, phir zaroorat ho to.

    1) Hard breaks — jahan speaker ruk raha hai (gap >= hard_gap) ya sentence
       khatam hua, wahan phrase cut hota hai. Yehi natural breaks hain.
    2) Lambi phrase ho to usko best pause point par split karte hain —
       sabse bada gap / comma, beech ke aas-paas — recursively, jab tak
       har chunk max_words/max_chars ke andar na aa jaye.
    """
    if not words:
        return []

    phrases: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        if cur and (w["start"] - cur[-1]["end"]) >= hard_gap:
            phrases.append(cur)
            cur = []
        cur.append(w)
        if w["word"].rstrip().endswith(_SENT_END):
            phrases.append(cur)
            cur = []
    if cur:
        phrases.append(cur)

    def too_big(ph: list[dict]) -> bool:
        return len(ph) > max_words or sum(len(w["word"]) + 1 for w in ph) - 1 > max_chars

    def split(ph: list[dict]) -> list[list[dict]]:
        if len(ph) <= 1 or not too_big(ph):
            return [ph]
        best_i, best_score = len(ph) // 2, -1.0
        for i in range(1, len(ph)):
            gap = ph[i]["start"] - ph[i - 1]["end"]
            score = gap * 10
            if ph[i - 1]["word"].rstrip().endswith(_SOFT_END):
                score += 2.0
            score += 1 - abs(i - len(ph) / 2) / len(ph)  # beech ke paas ho to behtar
            if score > best_score:
                best_score, best_i = score, i
        return split(ph[:best_i]) + split(ph[best_i:])

    chunks: list[dict] = []
    for ph in phrases:
        for part in split(ph):
            chunks.append({
                "start": part[0]["start"],
                "end": part[-1]["end"],
                "text": " ".join(w["word"] for w in part),
                "words": part,
            })
    return chunks


def parse_script_lines(text: str) -> list[str]:
    """User ke text box se lines nikaalo — 'SN  Beat' jaise header aur
    aage laga serial number (1., 2), 3 -, tab…) hata do."""
    lines = []
    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            continue
        if re.match(r"^(sn|s\.?\s*no\.?|serial|#)\s*\t?\s*beats?$", s, re.I):
            continue
        s = re.sub(r"^\d+\s*[.\)\-:\t]?\s+", "", s).strip()
        if s:
            lines.append(s)
    return lines


def _norm_tok(w: str) -> str:
    return re.sub(r"[^\w']+", "", w.lower())


def align_script_to_words(words: list[dict], lines: list[str]) -> list[dict]:
    """Har script line ko audio ke word timestamps se align karo.

    Script ke tokens aur transcript ke words par edit-distance DP chalta hai;
    jis line ke tokens transcript me match hue, uska time span wahi hai.
    Match bahut kam mile (beat-labels jaisa input) to audio ko line ki
    lambai ke hisaab se proportionally baant dete hain.
    """
    if not lines:
        return []

    def proportional() -> list[dict]:
        if not words:
            return []
        t0, t1 = words[0]["start"], words[-1]["end"]
        span = max(t1 - t0, 0.01)
        total = sum(len(l) for l in lines) or 1
        chunks, acc = [], 0
        for line in lines:
            s = t0 + span * acc / total
            acc += len(line)
            e = t0 + span * acc / total
            chunks.append({"start": round(s, 3), "end": round(e, 3),
                           "text": line, "words": []})
        return chunks

    script = [(li, _norm_tok(t)) for li, line in enumerate(lines)
              for t in re.findall(r"[\w']+", line.lower())]
    word_toks = [_norm_tok(w["word"]) for w in words]
    S, M = len(script), len(word_toks)
    if not script or not words or S * M > 20_000_000:
        return proportional()

    # DP over script tokens x transcript words (match 0 / sub 1 / gap 1)
    INF = float("inf")
    prev = list(range(M + 1))
    back = [[0] * (M + 1) for _ in range(S + 1)]  # 1=diag, 2=up(skip tok), 3=left(skip word)
    for i in range(1, S + 1):
        cur = [i] + [0] * M
        back[i][0] = 2
        tok = script[i - 1][1]
        for j in range(1, M + 1):
            diag = prev[j - 1] + (0 if tok == word_toks[j - 1] else 1)
            up = prev[j] + 1
            left = cur[j - 1] + 1
            best = min(diag, up, left)
            cur[j] = best
            back[i][j] = 1 if best == diag else (2 if best == up else 3)
        prev = cur

    # backtrack — har line ke matched words ka span nikalo
    spans: dict[int, list[int]] = {}
    exact = 0
    i, j = S, M
    while i > 0 or j > 0:
        move = back[i][j] if i > 0 else 3
        if move == 1:
            if script[i - 1][1] == word_toks[j - 1]:
                li = script[i - 1][0]
                spans.setdefault(li, [j - 1, j - 1])
                spans[li][0] = min(spans[li][0], j - 1)
                spans[li][1] = max(spans[li][1], j - 1)
                exact += 1
            i, j = i - 1, j - 1
        elif move == 2:
            i -= 1
        else:
            j -= 1

    if exact / S < 0.3:  # script bola hi nahi gaya — labels honge
        return proportional()

    # known spans → times; missing lines ko gap me char-proportion se bhar do
    starts: list[float | None] = [None] * len(lines)
    ends: list[float | None] = [None] * len(lines)
    for li, (j0, j1) in spans.items():
        starts[li] = words[j0]["start"]
        ends[li] = words[j1]["end"]

    audio_start, audio_end = words[0]["start"], words[-1]["end"]
    k = 0
    while k < len(lines):
        if starts[k] is not None:
            k += 1
            continue
        run_start = k
        while k < len(lines) and starts[k] is None:
            k += 1
        gap_s = ends[run_start - 1] if run_start > 0 else audio_start
        gap_e = starts[k] if k < len(lines) else audio_end
        total = sum(len(lines[x]) for x in range(run_start, k)) or 1
        acc = 0
        for x in range(run_start, k):
            starts[x] = gap_s + (gap_e - gap_s) * acc / total
            acc += len(lines[x])
            ends[x] = gap_s + (gap_e - gap_s) * acc / total

    # monotonic + no overlap
    chunks = []
    prev_end = audio_start
    for li, line in enumerate(lines):
        s = max(starts[li], prev_end)
        e = max(ends[li], s + 0.2)
        prev_end = e
        chunks.append({"start": round(s, 3), "end": round(e, 3),
                       "text": line, "words": []})
    return chunks


def _run_job(job_id: str, path: str, language: str | None, max_words: int,
             script: str = "") -> None:
    try:
        _update_job(job_id, status="loading_model")
        model = _get_model()
        _update_job(job_id, status="transcribing", device=_model_device)

        segments, info = model.transcribe(
            path,
            language=language,
            word_timestamps=True,
            vad_filter=True,
        )

        words: list[dict] = []
        for seg in segments:
            with _jobs_lock:
                cancelled = _jobs.get(job_id, {}).get("cancel")
            if cancelled:
                _update_job(job_id, status="cancelled")
                return
            for w in seg.words or []:
                words.append({
                    "word": w.word.strip(),
                    "start": round(w.start, 3),
                    "end": round(w.end, 3),
                })
            if info.duration:
                _update_job(job_id, progress=min(seg.end / info.duration, 1.0))

        script_lines = parse_script_lines(script)
        if script_lines:
            chunks = align_script_to_words(words, script_lines)
            mode = "matcher"
        else:
            chunks = chunk_words(words, max_words=max_words)
            mode = "auto"
        _update_job(
            job_id,
            status="done",
            progress=1.0,
            result={
                "mode": mode,
                "language": info.language,
                "language_probability": round(info.language_probability, 3),
                "duration": round(info.duration, 3),
                "words": words,
                "chunks": chunks,
            },
        )
    except Exception as exc:
        _update_job(job_id, status="error", error=str(exc))
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _start_job(path: str, language: str, max_words: int, script: str = "") -> str:
    job_id = uuid.uuid4().hex[:12]
    with _jobs_lock:
        _jobs[job_id] = {"status": "queued", "progress": 0.0}
    lang = None if language == "auto" else language
    threading.Thread(
        target=_run_job,
        args=(job_id, path, lang, max(1, min(max_words, 12)), script),
        daemon=True,
    ).start()
    return job_id


@app.post("/api/transcribe")
async def transcribe(
    file: UploadFile = File(...),
    language: str = Form("auto"),
    max_words: int = Form(4),
):
    suffix = Path(file.filename or "audio").suffix or ".bin"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name
    return {"job_id": _start_job(tmp_path, language, max_words)}


# ---- chunked upload (Cloudflare limits a single request to ~100 MB, so the
# frontend slices big files and sends them piece by piece) ----

_uploads: dict[str, str] = {}
_uploads_lock = threading.Lock()


@app.post("/api/upload/init")
async def upload_init(filename: str = Form("audio")):
    suffix = Path(filename).suffix or ".bin"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp_path = tmp.name
    upload_id = uuid.uuid4().hex[:12]
    with _uploads_lock:
        _uploads[upload_id] = tmp_path
    return {"upload_id": upload_id}


@app.post("/api/upload/chunk")
async def upload_chunk(upload_id: str = Form(...), chunk: UploadFile = File(...)):
    with _uploads_lock:
        path = _uploads.get(upload_id)
    if path is None:
        raise HTTPException(404, "upload not found")
    with open(path, "ab") as f:
        while True:
            piece = await chunk.read(4 * 1024 * 1024)
            if not piece:
                break
            f.write(piece)
    return {"ok": True, "size": os.path.getsize(path)}


@app.post("/api/upload/finish")
async def upload_finish(
    upload_id: str = Form(...),
    language: str = Form("auto"),
    max_words: int = Form(4),
    script: str = Form(""),
):
    with _uploads_lock:
        path = _uploads.pop(upload_id, None)
    if path is None:
        raise HTTPException(404, "upload not found")
    return {"job_id": _start_job(path, language, max_words, script)}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job


@app.post("/api/jobs/{job_id}/cancel")
def job_cancel(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        job["cancel"] = True
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(APP_DIR / "static" / "index.html")


app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8765)

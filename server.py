"""VoxCap — browser-based CapCut-style caption generator.

FastAPI backend: accepts an audio/video upload, transcribes it offline with
faster-whisper (large-v3 on GPU when available), and returns word-level
timestamps grouped into short CapCut-style caption chunks.
"""

import difflib
import multiprocessing as mp
import os
import queue as queue_mod
import re
import subprocess
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
# 0 = precise mode (default): sequential decoding, word timestamps 100% accurate.
# >0 = fast mode: batched inference — bahut fast lekin timestamps thoda
# aage-peeche ho sakte hain (batched pipeline ki limitation).
BATCH_SIZE = int(os.environ.get("VOXCAP_BATCH", "0"))

app = FastAPI(title="VoxCap")

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()

# Transcription alag process me chalti hai taaki Stop par process ko kill
# karke pura kaam (GPU compute, decode, sab) turant band kiya ja sake.
_worker = None      # mp.Process
_task_q = None      # mp.Queue
_result_q = None    # mp.Queue
_worker_lock = threading.Lock()


def _load_model():
    """Default: sequential decoding — word timestamps sabse accurate.
    VOXCAP_BATCH>0 set karne par batched fast mode (timing thodi loose)."""
    from faster_whisper import BatchedInferencePipeline, WhisperModel

    try:
        model = WhisperModel(MODEL_NAME, device="cuda", compute_type="float16")
        if BATCH_SIZE > 0:
            return BatchedInferencePipeline(model=model), "cuda", BATCH_SIZE
        return model, "cuda", None
    except Exception as exc:  # no CUDA / missing DLLs → CPU fallback
        print(f"[voxcap] CUDA unavailable ({exc}); falling back to CPU int8 ({CPU_MODEL_NAME})")
        return WhisperModel(CPU_MODEL_NAME, device="cpu", compute_type="int8"), "cpu", None


_SENT_END = (".", "?", "!", "。", "؟", "।")
_CLAUSE_END = (",", ";", ":", "،", "—", ")", "”", '"', "'")
# In words se NAYI line shuru hona natural lagta hai (clause boundaries)
_CONNECTORS = {
    "and", "but", "or", "so", "because", "when", "while", "where", "which",
    "that", "who", "whose", "after", "before", "until", "though", "although",
    "since", "if", "unless", "as", "than", "then", "instead", "meaning",
    # Hindi / Urdu (Devanagari + common romanized)
    "और", "लेकिन", "क्योंकि", "जब", "तो", "पर", "मगर", "कि", "जो", "अगर",
    "aur", "lekin", "kyunki", "jab", "toh", "par", "magar", "ki", "jo", "agar",
}


def _text_len(ws: list[dict]) -> int:
    return sum(len(w["word"]) for w in ws) + max(len(ws) - 1, 0)


def chunk_words(words: list[dict], max_chars: int = 70,
                max_gap: float = 0.6) -> list[dict]:
    """CapCut-style captions jisme har line ka apna matlab bane.

    1) Pehle sentences banao — full stop / sawal / lambi saans (gap) par.
    2) Jo sentence max_chars me fit ho, wo puri ek caption.
    3) Lambi sentence ko sabse MEANINGFUL jagah par todo:
       comma/clause ke baad, "when/and/but/ki/lekin" jaise connector se
       pehle, ya pause par — dono taraf balanced length rakhte hue.
       Kabhi bhi phrase ke beech me andha cut nahi.
    """
    if not words:
        return []

    sentences: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        if cur and (w["start"] - cur[-1]["end"]) >= max_gap:
            sentences.append(cur)
            cur = []
        cur.append(w)
        if w["word"].rstrip().endswith(_SENT_END):
            sentences.append(cur)
            cur = []
    if cur:
        sentences.append(cur)

    MIN_CHARS = 12  # itni chhoti line ka matlab nahi banta

    def best_split(ws: list[dict]) -> int:
        total = _text_len(ws)
        best_i, best_score = None, float("-inf")
        left = 0
        for i in range(1, len(ws)):
            left += len(ws[i - 1]["word"]) + (1 if i > 1 else 0)
            right = total - left - 1
            if left < MIN_CHARS or right < MIN_CHARS:
                continue
            score = 0.0
            if ws[i - 1]["word"].rstrip().endswith(_CLAUSE_END):
                score += 4.0
            if ws[i]["word"].strip().lower().strip('"“”') in _CONNECTORS:
                score += 2.5
            score += (ws[i]["start"] - ws[i - 1]["end"]) * 8  # pause = best cut
            score += 1 - abs(left - right) / max(total, 1)    # balance bonus
            if score > best_score:
                best_score, best_i = score, i
        return best_i if best_i is not None else max(1, len(ws) // 2)

    def split(ws: list[dict]) -> list[list[dict]]:
        if len(ws) <= 1 or _text_len(ws) <= max_chars:
            return [ws]
        i = best_split(ws)
        return split(ws[:i]) + split(ws[i:])

    chunks: list[dict] = []
    for sent in sentences:
        for part in split(sent):
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

    # Fuzzy match — transcription ki chhoti galtiyan (spelling, endings)
    # bhi match maani jaati hain, isse alignment kaafi accurate hota hai.
    _fuzzy_cache: dict[tuple[str, str], bool] = {}

    def is_match(a: str, b: str) -> bool:
        if a == b:
            return True
        if not a or not b or abs(len(a) - len(b)) > max(2, len(a) // 2):
            return False
        key = (a, b)
        hit = _fuzzy_cache.get(key)
        if hit is None:
            hit = difflib.SequenceMatcher(None, a, b).ratio() >= 0.75
            _fuzzy_cache[key] = hit
        return hit

    # DP over script tokens x transcript words (match 0/0.25 / sub 1 / gap 1)
    prev = list(range(M + 1))
    back = [[0] * (M + 1) for _ in range(S + 1)]  # 1=diag, 2=up(skip tok), 3=left(skip word)
    for i in range(1, S + 1):
        cur = [i] + [0] * M
        back[i][0] = 2
        tok = script[i - 1][1]
        for j in range(1, M + 1):
            wt = word_toks[j - 1]
            if tok == wt:
                sub = 0.0
            elif is_match(tok, wt):
                sub = 0.25
            else:
                sub = 1.0
            diag = prev[j - 1] + sub
            up = prev[j] + 1
            left = cur[j - 1] + 1
            best = min(diag, up, left)
            cur[j] = best
            back[i][j] = 1 if best == diag else (2 if best == up else 3)
        prev = cur

    # backtrack — alignment path ka HAR word (match ya substitution) uski
    # line ke span me jata hai. Isse line ka start bilkul wahi hota hai
    # jahan uska pehla word bola gaya, aur end wahi jahan aakhri word
    # khatam hua — chahe transcription me spelling thodi alag ho.
    spans: dict[int, list[int]] = {}
    exact = 0
    i, j = S, M
    while i > 0 or j > 0:
        move = back[i][j] if i > 0 else 3
        if move == 1:
            li = script[i - 1][0]
            spans.setdefault(li, [j - 1, j - 1])
            spans[li][0] = min(spans[li][0], j - 1)
            spans[li][1] = max(spans[li][1], j - 1)
            if is_match(script[i - 1][1], word_toks[j - 1]):
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


def _worker_main(task_q, result_q) -> None:
    """Alag process: model ek baar load hota hai, jobs process karta rehta hai."""
    model, device, batch = None, None, None
    while True:
        task = task_q.get()
        if task is None:
            return
        job_id, path, language, max_chars, script = task
        try:
            if model is None:
                result_q.put((job_id, "status", "loading_model"))
                model, device, batch = _load_model()
            result_q.put((job_id, "device", device))
            result_q.put((job_id, "status", "transcribing"))

            kwargs = dict(language=language, word_timestamps=True, vad_filter=True)
            if batch:
                # without_timestamps=True (batched default) punctuation kha jata hai
                kwargs["batch_size"] = batch
                kwargs["without_timestamps"] = False
            segments, info = model.transcribe(path, **kwargs)

            words: list[dict] = []
            for seg in segments:
                for w in seg.words or []:
                    words.append({
                        "word": w.word.strip(),
                        "start": round(w.start, 3),
                        "end": round(w.end, 3),
                    })
                if info.duration:
                    result_q.put((job_id, "progress", min(seg.end / info.duration, 1.0)))

            script_lines = parse_script_lines(script)
            if script_lines:
                chunks = align_script_to_words(words, script_lines)
                mode = "matcher"
            else:
                chunks = chunk_words(words, max_chars=max_chars)
                mode = "auto"
            result_q.put((job_id, "result", {
                "mode": mode,
                "language": info.language,
                "language_probability": round(info.language_probability, 3),
                "duration": round(info.duration, 3),
                "words": words,
                "chunks": chunks,
            }))
        except Exception as exc:
            result_q.put((job_id, "error", str(exc)))
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass


def _listener(result_q, worker) -> None:
    """Worker ke updates ko _jobs me bharta hai; worker mar jaye to jobs ko error."""
    while True:
        try:
            job_id, kind, val = result_q.get(timeout=1.0)
        except queue_mod.Empty:
            if result_q is not _result_q:
                return  # ye worker replace ho chuka hai
            if not worker.is_alive():
                with _jobs_lock:
                    for job in _jobs.values():
                        if job.get("status") in ("queued", "loading_model", "transcribing"):
                            job.update(status="error", error="worker process died")
                return
            continue
        except (EOFError, OSError):
            return
        with _jobs_lock:
            job = _jobs.get(job_id)
            if job is None or job.get("status") == "cancelled":
                continue
            if kind == "status":
                job["status"] = val
            elif kind == "device":
                job["device"] = val
            elif kind == "progress":
                job["progress"] = val
            elif kind == "result":
                job.update(status="done", progress=1.0, result=val)
            elif kind == "error":
                job.update(status="error", error=val)


def _ensure_worker() -> None:
    global _worker, _task_q, _result_q
    with _worker_lock:
        if _worker is not None and _worker.is_alive():
            return
        _task_q = mp.Queue()
        _result_q = mp.Queue()
        _worker = mp.Process(target=_worker_main, args=(_task_q, _result_q), daemon=True)
        _worker.start()
        threading.Thread(target=_listener, args=(_result_q, _worker), daemon=True).start()


def _kill_worker() -> None:
    """Stop = worker process ko kill karo — GPU/decode sab turant band.
    Agla job naya worker banayega (model dobara load hoga)."""
    global _worker
    with _worker_lock:
        if _worker is not None and _worker.is_alive():
            _worker.terminate()
            _worker.join(timeout=5)
        _worker = None


def _start_job(path: str, language: str, max_chars: int, script: str = "") -> str:
    job_id = uuid.uuid4().hex[:12]
    with _jobs_lock:
        _jobs[job_id] = {"status": "queued", "progress": 0.0, "path": path}
    _ensure_worker()
    lang = None if language == "auto" else language
    _task_q.put((job_id, path, lang, max(30, min(max_chars, 120)), script))
    return job_id


@app.post("/api/transcribe")
async def transcribe(
    file: UploadFile = File(...),
    language: str = Form("auto"),
    max_chars: int = Form(70),
):
    suffix = Path(file.filename or "audio").suffix or ".bin"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name
    return {"job_id": _start_job(tmp_path, language, max_chars)}


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
    max_chars: int = Form(70),
    script: str = Form(""),
):
    with _uploads_lock:
        path = _uploads.pop(upload_id, None)
    if path is None:
        raise HTTPException(404, "upload not found")
    return {"job_id": _start_job(path, language, max_chars, script)}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        return {k: v for k, v in job.items() if k != "path"}


@app.post("/api/jobs/{job_id}/cancel")
def job_cancel(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        if job.get("status") in ("done", "error", "cancelled"):
            return {"ok": True}
        job["status"] = "cancelled"
        path = job.get("path")
    _kill_worker()  # process kill = GPU/decode sab kuch turant band
    if path:
        try:
            os.unlink(path)
        except OSError:
            pass
    return {"ok": True}


@app.get("/api/stats")
def system_stats():
    """CPU / GPU / RAM usage % — UI ke top-right monitor ke liye."""
    import psutil

    cpu = psutil.cpu_percent(interval=None)
    ram = psutil.virtual_memory().percent
    gpu = vram = None
    try:
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2, creationflags=flags,
        )
        util, mem_used, mem_total = out.stdout.strip().splitlines()[0].split(", ")
        gpu = int(util)
        vram = round(int(mem_used) / int(mem_total) * 100)
    except Exception:
        pass  # GPU nahi hai ya nvidia-smi nahi mila
    return {"cpu": round(cpu), "ram": round(ram), "gpu": gpu, "vram": vram}


@app.get("/")
def index():
    # no-cache: UI update hote hi sabko fresh page mile, purana cache na atke
    return FileResponse(APP_DIR / "static" / "index.html",
                        headers={"Cache-Control": "no-cache, must-revalidate"})


app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8765)

"""VoxCap — browser-based CapCut-style caption generator.

FastAPI backend: accepts an audio/video upload, transcribes it offline with
faster-whisper (large-v3 on GPU when available), and returns word-level
timestamps grouped into short CapCut-style caption chunks.
"""

import os
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


def chunk_words(words: list[dict], max_words: int, max_chars: int = 32,
                max_gap: float = 0.6) -> list[dict]:
    """Group word timestamps into short CapCut-style caption chunks.

    A chunk closes when it hits max_words / max_chars, the sentence ends,
    or there is a silence gap longer than max_gap seconds.
    """
    chunks: list[dict] = []
    current: list[dict] = []

    def close() -> None:
        if not current:
            return
        chunks.append({
            "start": current[0]["start"],
            "end": current[-1]["end"],
            "text": " ".join(w["word"] for w in current),
            "words": list(current),
        })
        current.clear()

    for word in words:
        if current:
            gap = word["start"] - current[-1]["end"]
            length = sum(len(w["word"]) + 1 for w in current) + len(word["word"])
            if gap > max_gap or len(current) >= max_words or length > max_chars:
                close()
        current.append(word)
        if word["word"].rstrip().endswith((".", "?", "!", "。", "؟", "।")):
            close()
    close()
    return chunks


def _run_job(job_id: str, path: str, language: str | None, max_words: int) -> None:
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
            for w in seg.words or []:
                words.append({
                    "word": w.word.strip(),
                    "start": round(w.start, 3),
                    "end": round(w.end, 3),
                })
            if info.duration:
                _update_job(job_id, progress=min(seg.end / info.duration, 1.0))

        chunks = chunk_words(words, max_words=max_words)
        _update_job(
            job_id,
            status="done",
            progress=1.0,
            result={
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


def _start_job(path: str, language: str, max_words: int) -> str:
    job_id = uuid.uuid4().hex[:12]
    with _jobs_lock:
        _jobs[job_id] = {"status": "queued", "progress": 0.0}
    lang = None if language == "auto" else language
    threading.Thread(
        target=_run_job,
        args=(job_id, path, lang, max(1, min(max_words, 12))),
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
):
    with _uploads_lock:
        path = _uploads.pop(upload_id, None)
    if path is None:
        raise HTTPException(404, "upload not found")
    return {"job_id": _start_job(path, language, max_words)}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job


@app.get("/")
def index():
    return FileResponse(APP_DIR / "static" / "index.html")


app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8765)

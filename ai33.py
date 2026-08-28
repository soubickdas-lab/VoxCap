"""ai33.pro (OpenSpeaker) TTS client — audio generate karke VoxCap me feed karta hai.

API key kabhi repo me nahi jaati: env var AI33_API_KEY, ya local file
ai33-key.txt (gitignored). Doc: https://ai33.pro/app/api-document
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

API_BASE = "https://api.ai33.pro"
APP_DIR = Path(__file__).parent
KEY_FILE = APP_DIR / "ai33-key.txt"
SAVED_FILE = APP_DIR / "ai33-voices.json"

PROVIDERS = ["elevenlabs", "minimax", "edge", "kokoro", "clone", "vbee", "fishaudio"]


class Ai33Error(Exception):
    pass


def get_key() -> str:
    key = (os.environ.get("AI33_API_KEY") or "").strip()
    if not key and KEY_FILE.exists():
        key = KEY_FILE.read_text(encoding="utf-8").strip()
    return key


def masked_key() -> str:
    """Key ka sirf hissa dikhao — poori key kabhi bahar nahi jaati."""
    k = get_key()
    if not k:
        return ""
    return k[:7] + "…" + k[-4:] if len(k) > 14 else "…" + k[-4:]


def set_key(key: str) -> int:
    """Key check karke local file me save karo. Credits return karta hai.
    Ek baar save hone ke baad hamesha rehti hai — badalne tak."""
    key = (key or "").strip()
    if not key:
        raise Ai33Error("key khaali hai")

    old = os.environ.get("AI33_API_KEY")
    os.environ["AI33_API_KEY"] = key          # test ke liye temporarily
    try:
        c = credits()
    except Ai33Error as exc:
        if "401" in str(exc) or "Unauthorized" in str(exc):
            raise Ai33Error("Ye key kaam nahi kar rahi — galat hai ya "
                            "credits khatam hain. ai33.pro se check karo.") from exc
        raise
    finally:
        if old is None:
            os.environ.pop("AI33_API_KEY", None)
        else:
            os.environ["AI33_API_KEY"] = old

    KEY_FILE.write_text(key, encoding="utf-8")
    os.environ.pop("AI33_API_KEY", None)      # ab file hi source of truth
    return c


def clear_key() -> None:
    try:
        KEY_FILE.unlink()
    except FileNotFoundError:
        pass
    os.environ.pop("AI33_API_KEY", None)


def _request(method: str, path: str, *, params=None, form=None, body=None,
             timeout=60, retries=3):
    """HTTP call with retries — ai33 se connection toote to dobara try karta hai."""
    key = get_key()
    if not key:
        raise Ai33Error("ai33 API key set nahi hai (ai33-key.txt ya AI33_API_KEY)")

    url = API_BASE + path
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items()
                                             if v not in (None, "")})
    headers = {"xi-api-key": key, "Accept": "application/json"}
    data = None
    if form is not None:
        boundary = "----voxcap" + uuid.uuid4().hex
        parts = []
        for k, v in form.items():
            if v is None:
                continue
            parts.append(f"--{boundary}\r\nContent-Disposition: form-data; "
                         f'name="{k}"\r\n\r\n{v}\r\n')
        parts.append(f"--{boundary}--\r\n")
        data = "".join(parts).encode("utf-8")
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    elif body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            if e.code < 500:  # client error — retry se fayda nahi
                raise Ai33Error(f"ai33 {e.code}: {detail}") from e
            last = Ai33Error(f"ai33 {e.code}: {detail}")
        except Exception as e:
            last = Ai33Error(f"ai33 connection error: {e}")
        time.sleep(1.5 * (attempt + 1))
    raise last or Ai33Error("ai33 request fail")


def credits() -> int:
    return _request("GET", "/v1/credits").get("credits", 0)


def health() -> dict:
    return _request("GET", "/v1/health-check").get("data", {})


def voices(provider="elevenlabs", search="", page=1, page_size=30,
           language="", gender="") -> dict:
    r = _request("GET", "/v3/voices", params={
        "provider": provider, "search": search, "page": page,
        "page_size": page_size, "language": language, "gender": gender,
    })
    out = []
    for v in r.get("data", []):
        out.append({
            "voice_id": v.get("voice_id"),
            "name": v.get("name"),
            "description": v.get("description"),
            "language": v.get("language") or v.get("locale"),
            "gender": v.get("gender"),
            "preview_url": v.get("preview_url"),
        })
    return {"voices": out, "pagination": r.get("pagination", {})}


def resolve_voice(voice_id: str) -> dict:
    """Sirf voice_id se us voice ki detail (naam, preview) nikaalo.

    voice_id ka prefix hi provider batata hai (elevenlabs_, minimax_,
    edge_, kokoro_, clone_, vbee_, fishaudio_), aur baaki hissa search
    me daal dete hain.
    """
    voice_id = (voice_id or "").strip()
    if "_" not in voice_id:
        raise Ai33Error("voice_id me provider prefix hona chahiye "
                        "(jaise elevenlabs_… / minimax_… / edge_…)")
    provider, raw = voice_id.split("_", 1)
    if provider not in PROVIDERS:
        raise Ai33Error(f"unknown provider prefix: {provider}_")
    found = voices(provider=provider, search=raw, page_size=20)["voices"]
    for v in found:
        if v["voice_id"] == voice_id:
            return v
    if found:
        return found[0]
    raise Ai33Error("ye voice nahi mili")


def submit_tts(text: str, voice_id: str, speed: float = 1.0) -> str:
    """TTS task banao — task_id return karta hai."""
    r = _request("POST", "/v3/text-to-speech", form={
        "text": text,
        "voice_id": voice_id,
        "speed": f"{max(0.5, min(float(speed), 1.5)):g}",
        "with_transcript": "false",
    }, timeout=120)
    task_id = r.get("task_id")
    if not task_id:
        raise Ai33Error(f"ai33 ne task_id nahi diya: {json.dumps(r)[:200]}")
    return task_id


def wait_for_task(task_id: str, on_progress=None, max_wait=3600) -> dict:
    """Task done hone tak poll karo.

    Connection toot jaye to job fail NAHI hoti — ai33 udhar audio bana hi raha
    hota hai, isliye dobara connect karke polling chalti rehti hai.
    """
    started = time.time()
    net_fails = 0
    while time.time() - started < max_wait:
        try:
            t = _request("GET", f"/v1/task/{task_id}", timeout=30, retries=1)
            net_fails = 0
        except Ai33Error as e:
            net_fails += 1
            if net_fails > 60:  # ~5 min tak lagataar fail
                raise Ai33Error(f"ai33 se connection nahi ban paya: {e}")
            if on_progress:
                on_progress(None, f"ai33 se dobara connect ho rahe hain ({net_fails})…")
            time.sleep(5)
            continue

        status = t.get("status")
        if on_progress:
            on_progress(t.get("progress"), None)
        if status == "done":
            return t
        if status == "error":
            raise Ai33Error(t.get("error_message") or "ai33 task error")
        time.sleep(3)
    raise Ai33Error("ai33 task timeout")


def download(url: str, dest: str, retries=5) -> str:
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "VoxCap"})
            with urllib.request.urlopen(req, timeout=180) as r, open(dest, "wb") as f:
                while True:
                    buf = r.read(1 << 20)
                    if not buf:
                        break
                    f.write(buf)
            if os.path.getsize(dest) > 0:
                return dest
            last = Ai33Error("downloaded file khaali hai")
        except Exception as e:
            last = Ai33Error(f"audio download fail: {e}")
        time.sleep(2 * (attempt + 1))
    raise last


# ---- saved voices (local only, repo me nahi jaate) ----

def load_saved() -> list:
    if not SAVED_FILE.exists():
        return []
    try:
        return json.loads(SAVED_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_voice(voice_id: str, name: str, speed: float = 1.0,
               preview_url: str = "") -> list:
    items = [v for v in load_saved() if v.get("voice_id") != voice_id]
    items.insert(0, {"voice_id": voice_id, "name": name or voice_id,
                     "speed": speed, "preview_url": preview_url})
    SAVED_FILE.write_text(json.dumps(items[:50], indent=2), encoding="utf-8")
    return items[:50]


def delete_saved(voice_id: str) -> list:
    items = [v for v in load_saved() if v.get("voice_id") != voice_id]
    SAVED_FILE.write_text(json.dumps(items, indent=2), encoding="utf-8")
    return items

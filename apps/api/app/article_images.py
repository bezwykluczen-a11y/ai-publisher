"""Manual article illustration via AI Horde. No text regeneration or WordPress upload."""
import base64
import binascii
import json
import logging
import os
from pathlib import Path
import time
import urllib.error
import urllib.request
import uuid

import psycopg
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from redis import Redis
from rq import Queue
from rq.job import Job
from rq.exceptions import NoSuchJobError

logger = logging.getLogger(__name__)
DATABASE_URL = os.environ["DATABASE_URL"]
REDIS_URL = os.environ["REDIS_URL"]
IMAGE_DIR = Path(os.getenv("ARTICLE_IMAGE_DIR", "/app/article-images"))
API_BASE = "https://aihorde.net/api/v2"
IMAGE_TIMEOUT = 900
ACTIVE = {"queued", "requesting", "generating"}
router = APIRouter(tags=["article-images"])
queue = Queue("images", connection=Redis.from_url(REDIS_URL))


def init_image_db():
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    with psycopg.connect(DATABASE_URL) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS article_images (
                article_id INTEGER PRIMARY KEY REFERENCES articles(id) ON DELETE CASCADE,
                generation_id UUID NOT NULL,
                status TEXT NOT NULL,
                rq_job_id TEXT NOT NULL,
                external_id TEXT,
                prompt TEXT NOT NULL,
                filename TEXT,
                model TEXT,
                alt_text TEXT NOT NULL DEFAULT '',
                error TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)


def image_path(filename):
    if not filename or Path(filename).name != filename:
        raise ValueError("Invalid image filename")
    path = (IMAGE_DIR / filename).resolve()
    if path.parent != IMAGE_DIR.resolve():
        raise ValueError("Invalid image path")
    return path


def image_before_delete(cur, article_id):
    cur.execute("SELECT status,filename FROM article_images WHERE article_id=%s FOR UPDATE", (article_id,))
    row = cur.fetchone()
    if row and row[0] in ACTIVE:
        raise HTTPException(409, "Trwa generowanie grafiki. Poczekaj na zakończenie przed usunięciem artykułu.")
    return row[1] if row else None


def remove_local_image(filename):
    if not filename:
        return
    try:
        image_path(filename).unlink(missing_ok=True)
    except Exception:
        logger.exception("Could not remove local article image")


def fetch_image_row(article_id):
    with psycopg.connect(DATABASE_URL) as conn:
        if not conn.execute("SELECT id FROM articles WHERE id=%s", (article_id,)).fetchone():
            raise HTTPException(404, "Article not found")
        row = conn.execute("""SELECT status,generation_id,external_id,filename,model,alt_text,error,
                           rq_job_id,EXTRACT(EPOCH FROM (NOW()-updated_at))
                           FROM article_images WHERE article_id=%s""", (article_id,)).fetchone()
    return row


@router.get("/articles/{article_id}/image")
def get_article_image(article_id: int):
    row = fetch_image_row(article_id)
    if not row:
        return {"article_id": article_id, "status": "not_started"}
    status, generation_id, external_id, filename, model, alt_text, error, job_id, age = row
    if status in ACTIVE:
        try:
            job = Job.fetch(job_id, connection=queue.connection)
            state = job.get_status(refresh=True)
            state = getattr(state, "value", state)
            dead = state in {"failed", "stopped", "canceled", "finished"}
        except NoSuchJobError:
            dead = age > 120
        if dead:
            status = "failed"
            error = "Zadanie grafiki zakończyło się bez zapisanego obrazu. Sprawdź logi image-worker."
            with psycopg.connect(DATABASE_URL) as conn:
                conn.execute("""UPDATE article_images SET status='failed',error=%s,updated_at=NOW()
                                WHERE article_id=%s AND generation_id=%s
                                AND status IN ('queued','requesting','generating')""", (error,article_id,generation_id))
            # Re-read after conditional update to avoid overwriting a concurrent success.
            return get_article_image(article_id)
    if status == "ready" and (not filename or not image_path(filename).is_file()):
        status = "failed"
        error = "Brakuje pliku obrazu w magazynie. Sprawdź wolumen article_images."
    return {"article_id":article_id,"status":status,"generation_id":str(generation_id),
            "external_id":external_id,"model":model,"alt_text":alt_text,"error":error,
            "image_url":f"/articles/{article_id}/image/file?v={generation_id}" if status=="ready" else None}


@router.post("/articles/{article_id}/image")
def start_article_image(article_id: int):
    token = str(uuid.uuid4())
    job_id = "article-image-" + token
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT title FROM articles WHERE id=%s FOR UPDATE", (article_id,))
            article = cur.fetchone()
            if not article:
                raise HTTPException(404, "Article not found")
            cur.execute("SELECT status FROM article_images WHERE article_id=%s FOR UPDATE", (article_id,))
            existing = cur.fetchone()
            if existing and existing[0] in ACTIVE:
                raise HTTPException(409, "Grafika jest już w kolejce lub w trakcie generowania.")
            if existing and existing[0] == "ready":
                raise HTTPException(409, "Grafika już istnieje. Ten etap nie nadpisuje gotowego obrazu.")
            title = article[0][:400]
            prompt = ("A clean editorial illustration about this article title: " + title +
                      ". Conceptual illustration, friendly professional style, blue and teal palette, "
                      "simple composition. No text, no letters, no logos, no charts, no statistics.")
            cur.execute("""INSERT INTO article_images(article_id,generation_id,status,rq_job_id,prompt,alt_text)
                           VALUES(%s,%s,'queued',%s,%s,%s)
                           ON CONFLICT(article_id) DO UPDATE SET generation_id=EXCLUDED.generation_id,
                           status='queued',rq_job_id=EXCLUDED.rq_job_id,prompt=EXCLUDED.prompt,
                           alt_text=EXCLUDED.alt_text,external_id=NULL,filename=NULL,model=NULL,error=NULL,
                           created_at=NOW(),updated_at=NOW()""", (article_id,token,job_id,prompt,"Ilustracja do artykułu: " + title))
        conn.commit()
    try:
        queue.enqueue("app.article_images.generate_image", article_id, token,
                      job_id=job_id, job_timeout=1200, result_ttl=86400)
    except Exception as exc:
        with psycopg.connect(DATABASE_URL) as conn:
            conn.execute("UPDATE article_images SET status='failed',error=%s,updated_at=NOW() WHERE article_id=%s AND generation_id=%s",
                         ("Nie udało się zlecić grafiki. Sprawdź Redis.", article_id,token))
        raise HTTPException(503, "Nie udało się dodać grafiki do kolejki.") from exc
    return {"article_id":article_id,"status":"queued","generation_id":token,"job_id":job_id}


@router.get("/articles/{article_id}/image/file")
def get_article_image_file(article_id: int):
    row = fetch_image_row(article_id)
    if not row or row[0] != "ready" or not row[3]:
        raise HTTPException(404, "Image not ready")
    path = image_path(row[3])
    if not path.is_file():
        raise HTTPException(404, "Image file missing")
    mime = {".webp":"image/webp", ".png":"image/png", ".jpg":"image/jpeg"}.get(path.suffix)
    return FileResponse(path, media_type=mime, headers={"Cache-Control":"no-store"})


def horde_request(method, path, payload=None):
    headers = {"Accept":"application/json", "Client-Agent":"AIPublisher:0.2:local-test",
               "User-Agent":"AIPublisher/0.2"}
    if method == "POST":
        headers["apikey"] = os.getenv("AIHORDE_API_KEY", "0000000000").strip() or "0000000000"
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(API_BASE+path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read(20*1024*1024+1)
    except urllib.error.HTTPError as exc:
        detail = exc.read(2000).decode("utf-8",errors="replace")
        raise RuntimeError(f"AI Horde HTTP {exc.code}: {detail}") from exc
    if len(raw) > 20*1024*1024:
        raise RuntimeError("AI Horde response too large")
    return json.loads(raw)


def decode_image(generation):
    if generation.get("censored"):
        raise RuntimeError("AI Horde ocenzurowało obraz.")
    encoded = generation.get("img")
    if not isinstance(encoded,str) or not encoded:
        raise RuntimeError("AI Horde nie zwróciło obrazu.")
    if encoded.startswith(("http://","https://")):
        raise RuntimeError("Otrzymano URL zamiast danych obrazu. Przerwano pobieranie.")
    if encoded.startswith("data:"):
        encoded = encoded.split(",",1)[-1]
    try:
        image = base64.b64decode(encoded,validate=True)
    except (ValueError,binascii.Error) as exc:
        raise RuntimeError("Niepoprawny obraz Base64.") from exc
    if len(image)<32:
        raise RuntimeError("Obraz jest zbyt krótki.")
    if image[:4]==b"RIFF" and image[8:12]==b"WEBP":
        extension=".webp"
    elif image.startswith(b"\x89PNG\r\n\x1a\n"):
        extension=".png"
    elif image.startswith(b"\xff\xd8\xff"):
        extension=".jpg"
    else:
        raise RuntimeError("Nieznany format obrazu.")
    return image,extension


def generate_image(article_id, token):
    external_id=None
    saved_path=None
    completed=False
    try:
        with psycopg.connect(DATABASE_URL) as conn:
            row=conn.execute("""UPDATE article_images SET status='requesting',updated_at=NOW()
                               WHERE article_id=%s AND generation_id=%s AND status='queued'
                               RETURNING prompt""", (article_id,token)).fetchone()
        if not row:
            return {"skipped":True}
        logger.info("Submitting one AI Horde image: article=%s generation=%s",article_id,token)
        accepted=horde_request("POST","/generate/async",{
            "prompt":row[0],"params":{"width":512,"height":512,"steps":20,"cfg_scale":7.0,"sampler_name":"k_euler","n":1},
            "nsfw":False,"censor_nsfw":True,"r2":False,"shared":False,
            "trusted_workers":True,"slow_workers":True})
        if not accepted.get("id"):
            raise RuntimeError("AI Horde nie zwróciło identyfikatora zlecenia.")
        external_id=str(uuid.UUID(accepted["id"]))
        with psycopg.connect(DATABASE_URL) as conn:
            conn.execute("UPDATE article_images SET external_id=%s,status='generating',updated_at=NOW() WHERE article_id=%s AND generation_id=%s",(external_id,article_id,token))
        deadline=time.monotonic()+IMAGE_TIMEOUT
        while time.monotonic()<deadline:
            state=horde_request("GET",f"/generate/check/{external_id}")
            logger.info("Article image %s: done=%s queue=%s processing=%s",article_id,state.get("done"),state.get("queue_position"),state.get("processing"))
            if state.get("faulted"):
                raise RuntimeError("AI Horde oznaczyło zlecenie jako błędne.")
            if state.get("done"):
                result=horde_request("GET",f"/generate/status/{external_id}")
                generations=result.get("generations") or []
                if not generations:
                    raise RuntimeError("Zlecenie zakończone bez obrazu.")
                image,ext=decode_image(generations[0])
                IMAGE_DIR.mkdir(parents=True,exist_ok=True)
                saved_path=image_path(str(uuid.UUID(token))+ext)
                temp=saved_path.with_suffix(ext+".part")
                temp.write_bytes(image)
                temp.replace(saved_path)
                with psycopg.connect(DATABASE_URL) as conn:
                    cur=conn.execute("""UPDATE article_images SET status='ready',filename=%s,model=%s,error=NULL,updated_at=NOW()
                                        WHERE article_id=%s AND generation_id=%s AND status='generating'""",
                                     (saved_path.name,generations[0].get("model"),article_id,token))
                    if cur.rowcount!=1:
                        raise RuntimeError("Zlecenie nie jest już aktywne; wynik nie został przypisany.")
                completed=True
                logger.info("Article image saved: article=%s file=%s",article_id,saved_path.name)
                return {"article_id":article_id,"status":"ready"}
            time.sleep(10)
        raise RuntimeError("Przekroczono 15 minut oczekiwania na obraz.")
    except Exception as exc:
        logger.exception("Article image failed: article=%s",article_id)
        with psycopg.connect(DATABASE_URL) as conn:
            conn.execute("UPDATE article_images SET status='failed',error=%s,updated_at=NOW() WHERE article_id=%s AND generation_id=%s AND status<>'ready'",
                         (str(exc)[:2000],article_id,token))
        raise
    finally:
        if not completed:
            if saved_path:
                remove_local_image(saved_path.name)
            if external_id:
                try:
                    horde_request("DELETE",f"/generate/status/{external_id}")
                except Exception:
                    logger.warning("Could not confirm AI Horde cancellation for %s",external_id)

from __future__ import annotations

import base64
import binascii
import io
import json
import hashlib
import os
import sys
import queue
import re
import shutil
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.yaml"
CONFIG = yaml.safe_load((CONFIG_PATH if CONFIG_PATH.exists() else ROOT / "config.example.yaml").read_text())
DEFAULT_CARD_SEVERITY = {"warning_width_px": 8.3938, "critical_width_px": 8.7876}
DATA_ROOT = Path(os.environ.get("CRACK_DATA_ROOT", ROOT / CONFIG.get("data_dir", "data"))).resolve()
SESSION_ID = "_idle"
DATA = DATA_ROOT / SESSION_ID
ORIGINALS, RESULTS = DATA / "originals", DATA / "results"
for folder in (ORIGINALS, RESULTS): folder.mkdir(parents=True, exist_ok=True)
DB = DATA / "inspection.sqlite3"
PREDICTOR = None
PREDICTOR_ERROR: str | None = None
MODEL_LOADING = False
PREDICTOR_LOCK = threading.Lock()
WORK_QUEUE: queue.Queue[tuple[str, str]] = queue.Queue()
CATALOG = DATA_ROOT / "catalog.sqlite3"
CATALOG_LOCK = threading.RLock()
INBOX = "_inbox"

app = FastAPI(title="Drone Crack Inspection")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB, factory=ClosingConnection)
    con.row_factory = sqlite3.Row
    return con


def session_db(session: str) -> sqlite3.Connection:
    con = sqlite3.connect(DATA_ROOT / session / "inspection.sqlite3", factory=ClosingConnection)
    con.row_factory = sqlite3.Row
    return con


def catalog_db() -> sqlite3.Connection:
    con = sqlite3.connect(CATALOG, timeout=30, factory=ClosingConnection)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con


def init_catalog() -> None:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    with CATALOG_LOCK, catalog_db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL)""")
        con.execute("""CREATE TABLE IF NOT EXISTS flights (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
            session TEXT NOT NULL UNIQUE, started_at TEXT NOT NULL, stopped_at TEXT,
            legacy INTEGER NOT NULL DEFAULT 0)""")
        con.execute("""CREATE TABLE IF NOT EXISTS uploads (
            digest TEXT PRIMARY KEY, session TEXT NOT NULL, image_id TEXT NOT NULL)""")
        con.execute("INSERT OR IGNORE INTO projects VALUES ('legacy', 'Imported inspections', ?)",
                    (datetime.now(timezone.utc).isoformat(),))
        con.execute("UPDATE projects SET name='Imported inspections' WHERE id='legacy' AND name='ข้อมูลรอบเดิม'")
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_active_capture ON flights((1)) WHERE legacy=0 AND stopped_at IS NULL")
        for folder in DATA_ROOT.iterdir():
            if not folder.is_dir() or not re.fullmatch(r"\d{8}_\d{6}", folder.name):
                continue
            if not (folder / "inspection.sqlite3").is_file():
                continue
            try:
                with session_db(folder.name) as old_con:
                    if old_con.execute("SELECT COUNT(*) FROM images").fetchone()[0] == 0:
                        continue
            except sqlite3.DatabaseError:
                continue
            con.execute("INSERT OR IGNORE INTO flights VALUES (?, 'legacy', ?, ?, ?, 1)",
                        (folder.name, folder.name, datetime.strptime(folder.name, "%Y%m%d_%H%M%S")
                         .astimezone().astimezone(timezone.utc).isoformat(),
                         datetime.strptime(folder.name, "%Y%m%d_%H%M%S")
                         .astimezone().astimezone(timezone.utc).isoformat()))
    (DATA_ROOT / INBOX / "originals").mkdir(parents=True, exist_ok=True)
    init_session_db(INBOX)


def init_session_db(session: str) -> None:
    (DATA_ROOT / session / "results").mkdir(parents=True, exist_ok=True)
    with session_db(session) as con:
        con.execute("""CREATE TABLE IF NOT EXISTS images (
          id TEXT PRIMARY KEY, filename TEXT NOT NULL, received_at TEXT NOT NULL, captured_at TEXT,
          status TEXT NOT NULL, metadata TEXT NOT NULL, measurement TEXT, error TEXT,
          review_status TEXT NOT NULL DEFAULT 'pending', reviewer_note TEXT,
          reviewer_name TEXT, reviewed_at TEXT, reviewer_mask_path TEXT,
          reviewer_brush_mask_path TEXT, review_vector TEXT,
          review_requested INTEGER NOT NULL DEFAULT 0, review_requested_at TEXT)""")


def capture_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except ValueError:
        return None


def target_session(con: sqlite3.Connection, captured: datetime | None) -> str:
    if captured is None:
        return INBOX
    rows = con.execute("SELECT * FROM flights WHERE legacy=0 ORDER BY started_at DESC").fetchall()
    for row in rows:
        start, end = capture_time(row["started_at"]), capture_time(row["stopped_at"])
        if start and captured >= start and (end is None or captured < end):
            return row["session"]
    return INBOX


def select_dashboard_session(session: str) -> None:
    """Keep the existing dashboard APIs pointed at the selected live/last flight."""
    global SESSION_ID, DATA, ORIGINALS, RESULTS, DB
    SESSION_ID = session
    DATA = DATA_ROOT / session
    ORIGINALS, RESULTS = DATA / "originals", DATA / "results"
    DB = DATA / "inspection.sqlite3"


def init_db() -> None:
    with db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS images (
          id TEXT PRIMARY KEY, filename TEXT NOT NULL, received_at TEXT NOT NULL, captured_at TEXT,
          status TEXT NOT NULL, metadata TEXT NOT NULL, measurement TEXT, error TEXT
        )""")
        columns = {row["name"] for row in con.execute("PRAGMA table_info(images)")}
        migrations = {
            "review_status": "TEXT NOT NULL DEFAULT 'pending'",
            "reviewer_note": "TEXT",
            "reviewer_name": "TEXT",
            "reviewed_at": "TEXT",
            "reviewer_mask_path": "TEXT",
            "reviewer_brush_mask_path": "TEXT",
            "review_vector": "TEXT",
            "review_requested": "INTEGER NOT NULL DEFAULT 0",
            "review_requested_at": "TEXT",
        }
        for name, definition in migrations.items():
            if name not in columns:
                con.execute(f"ALTER TABLE images ADD COLUMN {name} {definition}")


def serialize(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["metadata"] = json.loads(d["metadata"])
    d["measurement"] = json.loads(d["measurement"]) if d["measurement"] else None
    d["original_url"] = f"/api/images/{d['id']}/original"
    d["overlay_url"] = f"/api/images/{d['id']}/overlay" if d["status"] == "done" else None
    d["mask_url"] = f"/api/images/{d['id']}/mask" if d["status"] == "done" else None
    d["review_mask_url"] = (f"/api/images/{d['id']}/review-mask"
                            if d.get("reviewer_mask_path") else None)
    d["reviewer_brush_mask_url"] = (f"/api/images/{d['id']}/review-brush-mask"
                                  if d.get("reviewer_brush_mask_path") else None)
    d["review_vector"] = json.loads(d["review_vector"]) if d.get("review_vector") else []
    return d


def latest_completed_from(session_dir: Path) -> sqlite3.Row | None:
    database = session_dir / "inspection.sqlite3"
    if not database.is_file():
        return None
    try:
        con = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            return con.execute("SELECT * FROM images WHERE status='done' "
                               "ORDER BY received_at DESC LIMIT 1").fetchone()
        finally:
            con.close()
    except sqlite3.DatabaseError:
        return None


def get_predictor() -> CrackPredictor:
    global PREDICTOR, PREDICTOR_ERROR, MODEL_LOADING
    with PREDICTOR_LOCK:
        if PREDICTOR is None:
            MODEL_LOADING = True
            try:
                # Torch/MPS can take a long time to initialise on macOS. Do
                # not hold dashboard startup hostage to it; load only when
                # the first received image reaches the inference worker.
                from .pipeline import CrackPredictor, InferenceConfig
                PREDICTOR = CrackPredictor(InferenceConfig(
                    checkpoint=(ROOT / CONFIG["checkpoint"]).resolve(), device=CONFIG.get("device", "auto"),
                    threshold=float(CONFIG.get("threshold", 0.1)), tile_size=int(CONFIG.get("tile_size", 448)),
                    tile_overlap=float(CONFIG.get("tile_overlap", .25)), min_component_px=int(CONFIG.get("min_component_px", 20)),
                ))
                PREDICTOR_ERROR = None
            except Exception as exc:
                PREDICTOR_ERROR = str(exc)
                raise
            finally:
                MODEL_LOADING = False
        return PREDICTOR


def preload_model() -> None:
    """Load the checkpoint at server start while the API stays responsive."""
    try:
        get_predictor()
        print("[startup] crack model loaded and ready", flush=True)
    except Exception as exc:
        print(f"[startup] crack model failed to load: {exc}", flush=True)


def process(session: str, image_id: str) -> None:
    base = DATA_ROOT / session
    with session_db(session) as con:
        row = con.execute("SELECT * FROM images WHERE id = ?", (image_id,)).fetchone()
        con.execute("UPDATE images SET status = 'processing' WHERE id = ?", (image_id,))
    try:
        metadata = json.loads(row["metadata"])
        metadata["inference_model"] = str(CONFIG.get("checkpoint", "unknown"))
        metadata["inference_threshold"] = CONFIG.get("threshold", 0.1)
        image_path = base / "originals" / image_id / row["filename"]
        image, mask, measurement = get_predictor().predict(image_path, metadata.get("gsd_mm_per_px"))
        from .pipeline import write_visuals
        write_visuals(image, mask, measurement, base / "results" / image_id)
        with session_db(session) as con:
            con.execute("UPDATE images SET status='done', measurement=?, metadata=? WHERE id=?", (json.dumps(measurement), json.dumps(metadata), image_id))
    except Exception as exc:
        with session_db(session) as con:
            con.execute("UPDATE images SET status='failed', error=? WHERE id=?", (str(exc), image_id))


def worker() -> None:
    """One worker preserves upload order and avoids multiple GPU/MPS models competing."""
    while True:
        session, image_id = WORK_QUEUE.get()
        try:
            process(session, image_id)
        finally:
            WORK_QUEUE.task_done()


@app.on_event("startup")
def startup() -> None:
    init_db()
    init_catalog()
    with catalog_db() as con:
        latest = con.execute("SELECT session FROM flights WHERE legacy=0 ORDER BY started_at DESC LIMIT 1").fetchone()
    if latest:
        select_dashboard_session(latest["session"])
    # A computer restart must not leave previously accepted images invisible forever.
    with db() as con:
        con.execute("UPDATE images SET status='queued', error=NULL WHERE status='processing'")
        pending = con.execute("SELECT id FROM images WHERE status='queued' ORDER BY received_at").fetchall()
    for row in pending:
        WORK_QUEUE.put((SESSION_ID, row["id"]))
    with catalog_db() as con:
        sessions = [row[0] for row in con.execute("SELECT session FROM flights WHERE legacy=0")]
    for session in sessions:
        if session == SESSION_ID:
            continue
        with session_db(session) as con:
            con.execute("UPDATE images SET status='queued', error=NULL WHERE status='processing'")
            for row in con.execute("SELECT id FROM images WHERE status='queued' ORDER BY received_at"):
                WORK_QUEUE.put((session, row["id"]))
    threading.Thread(target=worker, name="inference-worker", daemon=True).start()
    threading.Thread(target=preload_model, name="model-loader", daemon=True).start()


@app.get("/", response_class=HTMLResponse)
def dashboard():
    return (ROOT / "static" / "workspace.html").read_text()


@app.get("/review", response_class=HTMLResponse)
def review_dashboard():
    return (ROOT / "static" / "workspace.html").read_text()


@app.get("/projects", response_class=HTMLResponse)
def projects_page():
    return (ROOT / "static" / "workspace.html").read_text()


@app.get("/projects/{project_id}/{view}", response_class=HTMLResponse)
def workspace_page(project_id: str, view: str):
    return (ROOT / "static" / "workspace.html").read_text()


@app.get("/api/projects")
def list_projects():
    with catalog_db() as con:
        projects = [dict(row) for row in con.execute("SELECT * FROM projects ORDER BY id='legacy', created_at DESC")]
        flights = [dict(row) for row in con.execute("SELECT * FROM flights ORDER BY started_at DESC")]
    for flight in flights:
        database = DATA_ROOT / flight["session"] / "inspection.sqlite3"
        try:
            with session_db(flight["session"]) as con:
                flight["image_count"] = con.execute("SELECT COUNT(*) FROM images").fetchone()[0]
                flight["done_count"] = con.execute("SELECT COUNT(*) FROM images WHERE status='done'").fetchone()[0]
        except sqlite3.DatabaseError:
            flight["image_count"], flight["done_count"] = 0, 0
    for project in projects:
        project["flights"] = [flight for flight in flights if flight["project_id"] == project["id"]]
    return projects


@app.post("/api/projects", status_code=201)
async def create_project(request: Request):
    payload = await request.json()
    name = str(payload.get("name", "")).strip()[:100]
    if not name:
        raise HTTPException(422, "Project name is required")
    project = {"id": uuid.uuid4().hex, "name": name, "created_at": datetime.now(timezone.utc).isoformat()}
    with catalog_db() as con:
        con.execute("INSERT INTO projects VALUES (:id, :name, :created_at)", project)
    return project


@app.post("/api/flights/start", status_code=201)
async def start_flight(request: Request):
    payload = await request.json()
    project_id = str(payload.get("project_id", ""))
    if project_id == "legacy":
        raise HTTPException(422, "Choose a new project for a new flight")
    with CATALOG_LOCK, catalog_db() as con:
        if not con.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
            raise HTTPException(404, "Project not found")
        if con.execute("SELECT 1 FROM flights WHERE legacy=0 AND stopped_at IS NULL").fetchone():
            raise HTTPException(409, "Stop the active flight first")
        now = datetime.now(timezone.utc).isoformat()
        flight_id = uuid.uuid4().hex
        session = "flight_" + flight_id[:12]
        init_session_db(session)
        con.execute("INSERT INTO flights VALUES (?, ?, ?, ?, NULL, 0)", (flight_id, project_id, session, now))
        select_dashboard_session(session)
    return {"id": flight_id, "project_id": project_id, "session": session, "started_at": now, "stopped_at": None}


@app.post("/api/flights/{flight_id}/stop")
def stop_flight(flight_id: str):
    with CATALOG_LOCK, catalog_db() as con:
        row = con.execute("SELECT * FROM flights WHERE id=? AND legacy=0", (flight_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Flight not found")
        if row["stopped_at"]:
            return dict(row)
        stopped_at = datetime.now(timezone.utc).isoformat()
        con.execute("UPDATE flights SET stopped_at=? WHERE id=?", (stopped_at, flight_id))
        result = dict(row)
        result["stopped_at"] = stopped_at
        return result


@app.get("/api/flights/active")
def active_flight():
    with catalog_db() as con:
        row = con.execute("SELECT * FROM flights WHERE legacy=0 AND stopped_at IS NULL LIMIT 1").fetchone()
    return dict(row) if row else None


def valid_session(session: str) -> bool:
    return session == INBOX or bool(re.fullmatch(r"(?:\d{8}_\d{6}|flight_[0-9a-f]{12})", session))


@app.get("/api/flights/{session}/images")
def flight_images(session: str, response: Response, limit: int = 48, offset: int = 0, q: str = ""):
    if not valid_session(session) or not (DATA_ROOT / session / "inspection.sqlite3").is_file():
        raise HTTPException(404, "Flight not found")
    limit, offset = min(max(limit, 1), 100), max(offset, 0)
    q = q.strip()[:100]
    where = " WHERE filename LIKE ? ESCAPE '\\'" if q else ""
    params = ("%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%",) if q else ()
    with session_db(session) as con:
        total = con.execute("SELECT COUNT(*) FROM images" + where, params).fetchone()[0]
        rows = con.execute("SELECT * FROM images" + where + " ORDER BY COALESCE(captured_at, received_at) DESC "
                           "LIMIT ? OFFSET ?", params + (limit, offset)).fetchall()
    response.headers["X-Total-Count"] = str(total)
    result = []
    for row in rows:
        data = serialize(row)
        base = f"/api/flights/{session}/image/{row['id']}"
        data.update(session=session, original_url=f"{base}/original",
                    overlay_url=f"{base}/clean-overlay" if row["status"] == "done" else None,
                    mask_url=f"{base}/mask" if row["status"] == "done" else None)
        result.append(data)
    return result


@app.get("/api/flights/{session}/image/{image_id}/{kind}")
def flight_image_file(session: str, image_id: str, kind: str):
    if not valid_session(session) or kind not in {"original", "overlay", "mask", "clean-overlay"}:
        raise HTTPException(404, "Image not found")
    try:
        uuid.UUID(image_id)
    except ValueError:
        raise HTTPException(404, "Image not found")
    database = DATA_ROOT / session / "inspection.sqlite3"
    if not database.is_file():
        raise HTTPException(404, "Image not found")
    with session_db(session) as con:
        row = con.execute("SELECT filename, status FROM images WHERE id=?", (image_id,)).fetchone()
    if not row or (kind != "original" and row["status"] != "done"):
        raise HTTPException(404, "Image not found")
    base = DATA_ROOT / session
    original = base / "originals" / image_id / row["filename"]
    if kind == "clean-overlay":
        mask = base / "results" / image_id / "mask.png"
        if not original.is_file() or not mask.is_file():
            raise HTTPException(404, "Image not found")
        with Image.open(original) as source, Image.open(mask) as raw_mask:
            canvas = source.convert("RGBA")
            if raw_mask.size != canvas.size:
                raise HTTPException(422, "mask size does not match image")
            tint = Image.new("RGBA", canvas.size, (237, 78, 64, 0))
            tint.putalpha(raw_mask.convert("L").point(lambda value: 170 if value >= 128 else 0))
            output = io.BytesIO()
            Image.alpha_composite(canvas, tint).convert("RGB").save(output, "JPEG", quality=88)
        return Response(content=output.getvalue(), media_type="image/jpeg")
    path = original if kind == "original" else base / "results" / image_id / ("overlay.jpg" if kind == "overlay" else "mask.png")
    if not path.is_file():
        raise HTTPException(404, "Image not found")
    return FileResponse(path)


@app.put("/api/flights/{session}/image/{image_id}/review")
async def review_flight_image(session: str, image_id: str, request: Request):
    if not valid_session(session) or session == INBOX or not (DATA_ROOT / session / "inspection.sqlite3").is_file():
        raise HTTPException(404, "Flight not found")
    payload = await request.json()
    decision = payload.get("decision")
    if decision not in {"confirmed", "rejected", "uncertain"}:
        raise HTTPException(422, "Choose confirmed, rejected, or uncertain")
    reviewer = str(payload.get("reviewer", "")).strip()[:120]
    note = str(payload.get("note", "")).strip()[:4000]
    if not reviewer:
        raise HTTPException(422, "Reviewer name is required")
    with session_db(session) as con:
        row = con.execute("SELECT * FROM images WHERE id=?", (image_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Image not found")
        if row["status"] != "done":
            raise HTTPException(409, "AI result is not ready")
        con.execute("UPDATE images SET review_status=?, reviewer_name=?, reviewer_note=?, reviewed_at=?, "
                    "review_requested=1 WHERE id=?",
                    (decision, reviewer, note or None, datetime.now(timezone.utc).isoformat(), image_id))
        updated = con.execute("SELECT * FROM images WHERE id=?", (image_id,)).fetchone()
    return serialize(updated)


@app.get("/api/health")
def health():
    return {"status": "ok", "model_loaded": PREDICTOR is not None,
            "model_loading": MODEL_LOADING, "model_error": PREDICTOR_ERROR,
            "session": SESSION_ID}


@app.get("/api/settings")
def settings():
    severity = {**DEFAULT_CARD_SEVERITY, **(CONFIG.get("card_severity") or {})}
    return {"live_stream_url": CONFIG.get("live_stream_url", ""), "threshold": CONFIG.get("threshold", 0.1),
            "card_severity": severity}


@app.post("/api/images", status_code=202)
async def upload_image(request: Request):
    form = await request.form()
    file = form.get("file")
    metadata = form.get("metadata", "{}")
    if not file or not hasattr(file, "filename"):
        raise HTTPException(422, "multipart field 'file' is required")
    try:
        meta = json.loads(metadata)
    except json.JSONDecodeError as exc:
        raise HTTPException(422, "metadata must be valid JSON") from exc
    if not isinstance(meta, dict):
        raise HTTPException(422, "metadata must be a JSON object")
    if not file.filename or Path(file.filename).suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise HTTPException(415, "Upload a JPG, PNG, or WebP image")
    payload = await file.read()
    await form.close()
    if not payload or len(payload) > 40 * 1024 * 1024:
        raise HTTPException(413, "Image must be between 1 byte and 40 MB")
    digest = hashlib.sha256(payload + json.dumps(meta, sort_keys=True).encode()).hexdigest()
    safe_name = Path(file.filename).name
    received = datetime.now(timezone.utc).isoformat()
    with CATALOG_LOCK, catalog_db() as catalog:
        existing = catalog.execute("SELECT session, image_id FROM uploads WHERE digest=?", (digest,)).fetchone()
        if existing:
            return {"image_id": existing["image_id"], "session": existing["session"], "status": "duplicate"}
        session = target_session(catalog, capture_time(meta.get("captured_at")))
        init_session_db(session)
        image_id = str(uuid.uuid4())
        destination = DATA_ROOT / session / "originals" / image_id / safe_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
        status = "held" if session == INBOX else "queued"
        try:
            with session_db(session) as con:
                con.execute("INSERT INTO images (id, filename, received_at, captured_at, status, metadata, measurement, error) "
                            "VALUES (?, ?, ?, ?, ?, ?, NULL, NULL)",
                            (image_id, safe_name, received, meta.get("captured_at"), status, json.dumps(meta)))
            catalog.execute("INSERT INTO uploads VALUES (?, ?, ?)", (digest, session, image_id))
        except Exception:
            destination.unlink(missing_ok=True)
            raise
    if status == "queued":
        WORK_QUEUE.put((session, image_id))
    return {"image_id": image_id, "session": session, "status": status}


@app.get("/api/images/summary")
def image_summary():
    with db() as con:
        counts = dict(con.execute("SELECT status, COUNT(*) FROM images GROUP BY status").fetchall())
        reviews = dict(con.execute("SELECT review_status, COUNT(*) FROM images "
                                   "WHERE status = 'done' AND review_requested = 1 GROUP BY review_status").fetchall())
        queued_for_review = con.execute("SELECT COUNT(*) FROM images WHERE review_requested = 1").fetchone()[0]
        unsubmitted = con.execute("SELECT COUNT(*) FROM images WHERE review_requested = 0 AND review_status = 'pending'").fetchone()[0]
        reviewed = con.execute("SELECT COUNT(*) FROM images WHERE review_status != 'pending'").fetchone()[0]
        pending = con.execute("SELECT COUNT(*) FROM images WHERE review_requested = 1 AND review_status = 'pending'").fetchone()[0]
    return {"all": sum(counts.values()), "queued": counts.get("queued", 0),
            "processing": counts.get("processing", 0), "done": counts.get("done", 0),
            "failed": counts.get("failed", 0), "review": {
                "pending": pending, "confirmed": reviews.get("confirmed", 0),
                "corrected": reviews.get("corrected", 0), "rejected": reviews.get("rejected", 0),
                "uncertain": reviews.get("uncertain", 0), "queued": queued_for_review,
                "unsubmitted": unsubmitted, "reviewed": reviewed, "all": sum(counts.values()),
            }}


@app.get("/api/overview/latest")
def latest_overview_image():
    """Preview the newest completed drone image, including earlier sessions."""
    with catalog_db() as con:
        sessions = [row[0] for row in con.execute("SELECT session FROM flights ORDER BY started_at DESC")]
    for session in sessions:
        session_dir = DATA_ROOT / session
        row = latest_completed_from(session_dir)
        if row is None:
            continue
        image_id = row["id"]
        original = session_dir / "originals" / image_id / row["filename"]
        overlay = session_dir / "results" / image_id / "overlay.jpg"
        if not original.is_file() or not overlay.is_file():
            continue
        data = serialize(row)
        base = f"/api/overview/image/{session_dir.name}/{image_id}"
        has_mask = (session_dir / "results" / image_id / "mask.png").is_file()
        data.update(session=session_dir.name, historical=session_dir.name != SESSION_ID,
                    original_url=f"{base}/original", overlay_url=f"{base}/clean-overlay" if has_mask else f"{base}/overlay",
                    mask_url=f"{base}/mask" if has_mask else None)
        return data
    return None


@app.get("/api/overview/image/{session}/{image_id}/{kind}")
def overview_image_file(session: str, image_id: str, kind: str):
    if not valid_session(session) or kind not in {"original", "overlay", "mask", "clean-overlay"}:
        raise HTTPException(404, "image not found")
    try:
        uuid.UUID(image_id)
    except ValueError:
        raise HTTPException(404, "image not found")
    session_dir = DATA_ROOT / session
    database = session_dir / "inspection.sqlite3"
    if not database.is_file():
        raise HTTPException(404, "image not found")
    try:
        con = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            row = con.execute("SELECT filename FROM images WHERE id=? AND status='done'", (image_id,)).fetchone()
        finally:
            con.close()
    except sqlite3.DatabaseError:
        raise HTTPException(404, "image not found")
    if row is None:
        raise HTTPException(404, "image not found")
    original_path = session_dir / "originals" / image_id / row["filename"]
    if kind == "clean-overlay":
        mask_path = session_dir / "results" / image_id / "mask.png"
        if not original_path.is_file() or not mask_path.is_file():
            raise HTTPException(404, "image not found")
        with Image.open(original_path) as source, Image.open(mask_path) as raw_mask:
            base_image = source.convert("RGBA")
            mask_image = raw_mask.convert("L")
            if mask_image.size != base_image.size:
                raise HTTPException(422, "mask size does not match image")
            alpha = mask_image.point(lambda value: 170 if value >= 128 else 0)
            tint = Image.new("RGBA", base_image.size, (237, 78, 64, 0))
            tint.putalpha(alpha)
            rendered = Image.alpha_composite(base_image, tint).convert("RGB")
            output = io.BytesIO()
            rendered.save(output, format="JPEG", quality=88)
        return Response(content=output.getvalue(), media_type="image/jpeg")
    path = (original_path if kind == "original" else
            session_dir / "results" / image_id / ("overlay.jpg" if kind == "overlay" else "mask.png"))
    if not path.is_file():
        raise HTTPException(404, "image not found")
    return FileResponse(path)


@app.get("/api/images")
def list_images(response: Response, limit: int = 100, offset: int = 0,
                status: str | None = None, review: str | None = None, review_stage: str | None = None,
                human_review: bool | None = None):
    allowed_statuses = {"queued", "processing", "done", "failed"}
    allowed_reviews = {"pending", "confirmed", "corrected", "rejected", "uncertain"}
    if status is not None and status not in allowed_statuses:
        raise HTTPException(400, "Invalid image status filter")
    if review is not None and review not in allowed_reviews:
        raise HTTPException(400, "Invalid review status filter")
    if review_stage is not None and review_stage not in {"all", "unsubmitted", "pending", "reviewed"}:
        raise HTTPException(400, "Invalid review stage filter")
    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)
    with db() as con:
        clauses, params = [], []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if review:
            clauses.append("review_status = ?")
            params.append(review)
        if human_review is not None:
            clauses.append("review_requested = ?")
            params.append(1 if human_review else 0)
        if review_stage == "unsubmitted":
            clauses.append("review_requested = 0 AND review_status = 'pending'")
        elif review_stage == "pending":
            clauses.append("review_requested = 1 AND review_status = 'pending'")
        elif review_stage == "reviewed":
            clauses.append("review_status != 'pending'")
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params = tuple(params)
        total = con.execute("SELECT COUNT(*) FROM images" + where, params).fetchone()[0]
        rows = con.execute("SELECT * FROM images" + where +
                           " ORDER BY received_at DESC LIMIT ? OFFSET ?",
                           params + (limit, offset)).fetchall()
    response.headers["X-Total-Count"] = str(total)
    return [serialize(r) for r in rows]


@app.put("/api/images/review-queue")
async def set_review_queue(request: Request):
    payload = await request.json()
    ids = payload.get("image_ids")
    requested = bool(payload.get("requested", True))
    if not isinstance(ids, list) or not ids or len(ids) > 500:
        raise HTTPException(422, "image_ids must contain 1 to 500 image ids")
    ids = list(dict.fromkeys(str(image_id) for image_id in ids))
    placeholders = ",".join("?" for _ in ids)
    requested_value = 1 if requested else 0
    requested_at = datetime.now(timezone.utc).isoformat() if requested else None
    with db() as con:
        # Only change rows whose queue state actually differs.  This makes a
        # retry safe and lets the client distinguish an updated row from one
        # that was already queued, failed, or no longer exists.
        if requested:
            eligible = "status = 'done' AND review_requested = 0 AND review_status = 'pending'"
            params = ids
        else:
            # A completed human decision is an audit record, not a queue item:
            # undo is intentionally limited to rows still waiting for review.
            eligible = "status = 'done' AND review_requested = 1 AND review_status = 'pending'"
            params = ids
        found = con.execute("SELECT id FROM images WHERE " + eligible + " AND id IN (" + placeholders + ")",
                           params).fetchall()
        found_ids = [row["id"] for row in found]
        if found_ids:
            found_placeholders = ",".join("?" for _ in found_ids)
            if requested:
                con.execute("UPDATE images SET review_requested = 1, review_requested_at = ? WHERE id IN (" + found_placeholders + ")",
                            [requested_at] + found_ids)
            else:
                con.execute("UPDATE images SET review_requested = 0, review_requested_at = NULL WHERE id IN (" + found_placeholders + ")",
                            found_ids)
        reasons = {}
        for image_id in ids:
            if image_id in found_ids:
                continue
            item = con.execute("SELECT status, review_status, review_requested FROM images WHERE id=?", (image_id,)).fetchone()
            reasons[image_id] = ("ไม่พบภาพนี้" if item is None else
                                 "ภาพนี้ตรวจแล้ว" if item["review_status"] != "pending" else
                                 "AI ยังประมวลผลไม่เสร็จ" if item["status"] != "done" else
                                 "ภาพนี้ส่งเข้าตรวจแล้ว" if requested else "ภาพนี้ไม่ได้อยู่ในคิวตรวจ")
    skipped_ids = [image_id for image_id in ids if image_id not in found_ids]
    return {"updated": len(found_ids), "updated_ids": found_ids,
            "skipped_ids": skipped_ids, "requested": requested,
            "review_requested_at": requested_at, "reasons": reasons}


@app.put("/api/images/{image_id}/review")
async def save_review(image_id: str, request: Request):
    payload = await request.json()
    decision = payload.get("decision")
    if decision not in {"confirmed", "corrected", "rejected", "uncertain"}:
        raise HTTPException(422, "Choose a valid review decision")
    note = str(payload.get("note", "")).strip()[:4000]
    reviewer = str(payload.get("reviewer", "")).strip()[:120]
    mask_data = payload.get("mask_data")
    brush_mask_data = payload.get("brush_mask_data")
    vector = payload.get("vector", [])
    if not isinstance(vector, list):
        raise HTTPException(422, "review vector must be a list of paths")
    vector_json = json.dumps(vector, separators=(",", ":"))
    if len(vector_json) > 1024 * 1024:
        raise HTTPException(422, "review vector is too large")
    with db() as con:
        row = con.execute("SELECT * FROM images WHERE id = ?", (image_id,)).fetchone()
    if not row:
        raise HTTPException(404, "image not found")
    if row["status"] != "done":
        raise HTTPException(409, "An image can be reviewed after inference is done")

    def save_png(data: str | None, path: Path, label: str) -> Path | None:
        if data is None:
            return None
        if not isinstance(data, str) or not data.startswith("data:image/png;base64,"):
            raise HTTPException(422, f"{label} must be a PNG data URL")
        try:
            raw = base64.b64decode(data.split(",", 1)[1], validate=True)
        except (ValueError, UnicodeError, binascii.Error) as exc:
            raise HTTPException(422, f"{label} is not valid base64") from exc
        if len(raw) > 12 * 1024 * 1024 or not raw.startswith(b"\x89PNG\r\n\x1a\n"):
            raise HTTPException(422, f"{label} must be a PNG smaller than 12 MB")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return path

    mask_path = save_png(mask_data, RESULTS / image_id / "reviewer_mask.png", "review mask")
    brush_path = save_png(brush_mask_data, RESULTS / image_id / "reviewer_brush_mask.png", "brush mask")

    reviewed_at = datetime.now(timezone.utc).isoformat()
    with db() as con:
        con.execute("UPDATE images SET review_status=?, reviewer_note=?, reviewer_name=?, reviewed_at=?, "
                    "reviewer_mask_path=?, reviewer_brush_mask_path=?, review_vector=? WHERE id=?",
                    (decision, note or None, reviewer or None, reviewed_at,
                     str(mask_path.relative_to(DATA)) if mask_path and mask_path.is_file() else row["reviewer_mask_path"],
                     str(brush_path.relative_to(DATA)) if brush_path and brush_path.is_file() else row["reviewer_brush_mask_path"],
                     vector_json, image_id))
        saved = con.execute("SELECT * FROM images WHERE id = ?", (image_id,)).fetchone()
    return serialize(saved)


@app.get("/api/images/{image_id}")
def image_status(image_id: str):
    with db() as con:
        row = con.execute("SELECT * FROM images WHERE id = ?", (image_id,)).fetchone()
    if not row: raise HTTPException(404, "image not found")
    return serialize(row)


def served_file(image_id: str, kind: str):
    with db() as con:
        row = con.execute("SELECT * FROM images WHERE id = ?", (image_id,)).fetchone()
    if not row: raise HTTPException(404, "image not found")
    paths = {
        "original": ORIGINALS / image_id / row["filename"],
        "overlay": RESULTS / image_id / "overlay.jpg",
        "mask": RESULTS / image_id / "mask.png",
        "review-mask": DATA / row["reviewer_mask_path"] if row["reviewer_mask_path"] else None,
        "review-brush-mask": DATA / row["reviewer_brush_mask_path"] if row["reviewer_brush_mask_path"] else None,
    }
    if not paths[kind] or not paths[kind].is_file(): raise HTTPException(404, f"{kind} not ready")
    return FileResponse(paths[kind])


@app.get("/api/images/{image_id}/original")
def original(image_id: str): return served_file(image_id, "original")

@app.get("/api/images/{image_id}/overlay")
def overlay(image_id: str): return served_file(image_id, "overlay")

@app.get("/api/images/{image_id}/mask")
def mask(image_id: str): return served_file(image_id, "mask")

@app.get("/api/images/{image_id}/review-mask")
def review_mask(image_id: str): return served_file(image_id, "review-mask")

@app.get("/api/images/{image_id}/review-brush-mask")
def review_brush_mask(image_id: str): return served_file(image_id, "review-brush-mask")


from .workspace_api import register_workspace
register_workspace(sys.modules[__name__])

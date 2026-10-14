"""Project-scoped inspection, review and export APIs for the unified workspace."""
from __future__ import annotations

import base64
import csv
import io
import json
import math
import shutil
import sqlite3
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from PIL import Image, ImageDraw, ImageFont

FINAL_DECISIONS = {"confirmed", "corrected", "rejected"}


def register_workspace(m):
    router = APIRouter(prefix="/api/workspace")

    def project_scope(project_id, session=None):
        with m.catalog_db() as con:
            project = con.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            if not project:
                raise HTTPException(404, "Project not found")
            flights = [dict(r) for r in con.execute(
                "SELECT * FROM flights WHERE project_id=? ORDER BY started_at DESC", (project_id,))]
        if session and session not in {f["session"] for f in flights}:
            raise HTTPException(404, "Session does not belong to this project")
        return dict(project), [f for f in flights if not session or f["session"] == session]

    def scoped_image(project_id, session, image_id):
        project_scope(project_id, session)
        with m.session_db(session) as con:
            row = con.execute("SELECT * FROM images WHERE id=?", (image_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Image not found in this session")
        return dict(row)

    def decode(value, fallback):
        try:
            return json.loads(value) if isinstance(value, str) else value if value is not None else fallback
        except (ValueError, TypeError):
            return fallback

    def serialize(project_id, session, row):
        item = dict(row)
        for key, fallback in (("metadata", {}), ("measurement", None), ("final_measurement", None),
                              ("manual_measurements", []), ("review_vector", [])):
            item[key] = decode(item.get(key), fallback)
        item.update(project_id=project_id, session=session, revision=item.get("revision", 0),
                    review_status=item.get("review_status") or "pending")
        base = f"/api/workspace/projects/{project_id}/images/{session}/{item['id']}"
        item["original_url"] = base + "/asset/original"
        for kind, key, exists in (("mask", "mask_url", item["status"] == "done"),
                                  ("clean-overlay", "overlay_url", item["status"] == "done"),
                                  ("review-mask", "review_mask_url", item.get("reviewer_mask_path"))):
            item[key] = base + "/asset/" + kind + f"?v={item['revision']}" if exists else None
        return item

    def all_images(project_id, session=None):
        project, flights = project_scope(project_id, session)
        items = []
        for flight in flights:
            with m.session_db(flight["session"]) as con:
                items.extend(serialize(project_id, flight["session"], r) for r in con.execute("SELECT * FROM images"))
        items.sort(key=lambda r: r.get("captured_at") or r["received_at"], reverse=True)
        return project, flights, items

    def summary(items):
        result = {"total": len(items), "done": 0, "processing": 0, "queued": 0, "failed": 0,
                  "ready": 0, "reviewed": 0, "uncertain": 0, "confirmed": 0, "corrected": 0, "rejected": 0}
        for row in items:
            status, decision = row["status"], row["review_status"]
            if status in {"done", "processing", "queued", "failed"}:
                result[status] += 1
            if decision in FINAL_DECISIONS:
                result["reviewed"] += 1
                result[decision] += 1
            elif decision == "uncertain":
                result["uncertain"] += 1
            elif status == "done":
                result["ready"] += 1
        result["complete"] = bool(items) and result["reviewed"] == len(items)
        return result

    def filtered(items, status, q):
        if status not in {"all", "ready", "reviewed", "uncertain", "queued", "processing", "failed", "done"}:
            raise HTTPException(422, "Unknown image filter")
        return [r for r in items if (not q or q.casefold() in r["filename"].casefold()) and (
            status == "all" or (status == "ready" and r["status"] == "done" and r["review_status"] == "pending") or
            (status == "reviewed" and r["review_status"] in FINAL_DECISIONS) or
            (status == "uncertain" and r["review_status"] == "uncertain") or
            (status == "processing" and r["status"] in {"queued", "processing"}) or
            (status in {"queued", "failed", "done"} and r["status"] == status))]

    @router.get("/projects")
    def projects():
        result = []
        for project in m.list_projects():
            _, _, items = all_images(project["id"])
            project["name"] = "Imported inspections" if project["id"] == "legacy" else project["name"]
            project["summary"] = summary(items)
            project["latest_at"] = items[0].get("captured_at") or items[0]["received_at"] if items else project["created_at"]
            project["cover"] = items[0]["original_url"] if items else None
            result.append(project)
        return {"projects": result, "active": m.active_flight()}

    @router.get("/inbox")
    def inbox(page: int = 1):
        with m.session_db(m.INBOX) as con:
            rows = [dict(r) for r in con.execute("SELECT * FROM images WHERE status='held' ORDER BY received_at DESC")]
        page = max(1,min(page,max(1,math.ceil(len(rows)/24))))
        for row in rows:
            row["metadata"] = decode(row["metadata"], {})
            row["original_url"] = f"/api/flights/_inbox/image/{row['id']}/original"
        return {"items": rows[(page-1)*24:page*24], "total": len(rows), "page": page, "pages": max(1,math.ceil(len(rows)/24))}

    @router.post("/inbox/assign")
    async def assign(request: Request):
        payload = await request.json()
        project_id = str(payload.get("project_id", ""))
        project_scope(project_id)
        ids = payload.get("image_ids", [])
        if not isinstance(ids, list) or not 1 <= len(ids) <= 100 or any(not isinstance(i,str) for i in ids):
            raise HTTPException(422, "Select 1 to 100 images")
        ids = list(dict.fromkeys(ids))
        queued = []
        with m.CATALOG_LOCK, m.catalog_db() as con:
            placeholders = ",".join("?" for _ in ids)
            with m.session_db(m.INBOX) as inbox_con:
                rows = [dict(r) for r in inbox_con.execute(f"SELECT * FROM images WHERE status='held' AND id IN ({placeholders})",ids)]
            if len(rows) != len(ids):
                raise HTTPException(409, "Some selected images have already been assigned. Refresh the inbox.")
            flight_id = uuid.uuid4().hex
            session = "flight_"+flight_id[:12]
            m.init_session_db(session)
            now = datetime.now(timezone.utc).isoformat()
            con.execute("ATTACH DATABASE ? AS inbox_db", (str(m.DATA_ROOT/m.INBOX/"inspection.sqlite3"),))
            con.execute("ATTACH DATABASE ? AS target_db", (str(m.DATA_ROOT/session/"inspection.sqlite3"),))
            con.execute("INSERT INTO flights VALUES (?,?,?,?,?,1)",(flight_id,project_id,session,now,now))
            for row in rows:
                new_id = str(uuid.uuid4())
                target = m.DATA_ROOT/session/"originals"/new_id/Path(row["filename"]).name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(m.DATA_ROOT/m.INBOX/"originals"/row["id"]/row["filename"],target)
                metadata = decode(row["metadata"],{})
                metadata["assigned_from_inbox"] = {"image_id":row["id"],"assigned_at":now}
                con.execute("""INSERT INTO target_db.images (id,filename,received_at,captured_at,status,metadata)
                    VALUES (?,?,?,?,'queued',?)""",(new_id,row["filename"],row["received_at"],row["captured_at"],json.dumps(metadata)))
                con.execute("UPDATE inbox_db.images SET status='assigned' WHERE id=?",(row["id"],))
                con.execute("UPDATE uploads SET session=?,image_id=? WHERE session=? AND image_id=?",(session,new_id,m.INBOX,row["id"]))
                queued.append((session,new_id))
        for item in queued:
            m.WORK_QUEUE.put(item)
        return {"assigned":len(queued),"session":session,"project_id":project_id}

    @router.get("/projects/{project_id}")
    def project_detail(project_id: str, session: str | None = None):
        project, flights, items = all_images(project_id, session)
        if project_id == "legacy":
            project["name"] = "Imported inspections"
        for flight in flights:
            flight["summary"] = summary([r for r in items if r["session"] == flight["session"]])
        return {"project": project, "flights": flights, "summary": summary(items),
                "featured": next((r for r in items if r["status"] == "done"), items[0] if items else None),
                "recent": items[:8], "active": m.active_flight()}

    @router.get("/projects/{project_id}/images")
    def images(project_id: str, session: str | None = None, status: str = "all", q: str = "",
               page: int = 1, limit: int = 24):
        _, _, items = all_images(project_id, session)
        matches = filtered(items, status, q[:200])
        limit = max(1, min(limit, 100))
        page = max(1, min(page, max(1, math.ceil(len(matches) / limit))))
        return {"items": matches[(page-1)*limit:page*limit], "total": len(matches), "page": page,
                "pages": max(1, math.ceil(len(matches)/limit)), "summary": summary(items)}

    @router.get("/projects/{project_id}/images/{session}/{image_id}")
    def image_detail(project_id: str, session: str, image_id: str):
        return serialize(project_id, session, scoped_image(project_id, session, image_id))

    @router.get("/projects/{project_id}/images/{session}/{image_id}/asset/{kind}")
    def asset(project_id: str, session: str, image_id: str, kind: str):
        row = scoped_image(project_id, session, image_id)
        if kind == "review-mask":
            path = (m.DATA_ROOT / session / (row.get("reviewer_mask_path") or "missing")).resolve()
            if not path.is_relative_to((m.DATA_ROOT / session).resolve()) or not path.is_file():
                raise HTTPException(404, "Reviewed mask is not available")
            return FileResponse(path)
        return m.flight_image_file(session, image_id, kind)

    def ensure_review_schema(con):
        columns = {r["name"] for r in con.execute("PRAGMA table_info(images)")}
        fields = {"revision": "INTEGER NOT NULL DEFAULT 0", "manual_measurements": "TEXT",
                  "final_measurement": "TEXT", "review_status": "TEXT NOT NULL DEFAULT 'pending'",
                  "reviewer_name": "TEXT", "reviewer_note": "TEXT", "reviewed_at": "TEXT",
                  "review_requested": "INTEGER NOT NULL DEFAULT 0", "reviewer_mask_path": "TEXT"}
        for name, definition in fields.items():
            if name not in columns:
                con.execute(f"ALTER TABLE images ADD COLUMN {name} {definition}")
        con.execute("""CREATE TABLE IF NOT EXISTS review_events (
            id TEXT PRIMARY KEY, image_id TEXT NOT NULL, revision INTEGER NOT NULL,
            saved_at TEXT NOT NULL, snapshot TEXT NOT NULL)""")

    @router.put("/projects/{project_id}/images/{session}/{image_id}/review")
    async def review(project_id: str, session: str, image_id: str, request: Request):
        row = scoped_image(project_id, session, image_id)
        payload = await request.json()
        decision = payload.get("decision")
        if decision not in FINAL_DECISIONS | {"uncertain", "pending"}:
            raise HTTPException(422, "Choose a review decision")
        if decision != "pending" and row["status"] != "done":
            raise HTTPException(409, "AI processing must finish before reviewing its result")
        reviewer = str(payload.get("reviewer", "")).strip()[:120]
        if not reviewer:
            raise HTTPException(422, "Enter the reviewer name")
        if payload.get("revision") != row.get("revision", 0):
            raise HTTPException(409, "This image was updated elsewhere. Reopen it before saving.")
        original = m.DATA_ROOT / session / "originals" / image_id / row["filename"]
        with Image.open(original) as source:
            width, height = source.size
        metadata = decode(row.get("metadata"), {})
        gsd = metadata.get("gsd_mm_per_px")
        gsd = float(gsd) if isinstance(gsd, (int, float)) and math.isfinite(gsd) and gsd > 0 else None
        manual = payload.get("manual_measurements", [])
        if not isinstance(manual, list) or len(manual) > 100:
            raise HTTPException(422, "At most 100 measurement lines are supported")
        measurements = []
        for line in manual:
            try:
                points = [[float(v) for v in p] for p in line["points"]]
                assert len(points) == 2 and all(len(p) == 2 for p in points)
                assert all(math.isfinite(x) and math.isfinite(y) and 0 <= x <= width and 0 <= y <= height for x,y in points)
                length = math.dist(*points)
            except (KeyError, ValueError, TypeError, AssertionError):
                raise HTTPException(422, "Measurement coordinates must be within the original image")
            measurements.append({"points": points, "length_px": length, "length_mm": length*gsd if gsd else None})
        revision = row.get("revision", 0) + 1
        mask_path, final = row.get("reviewer_mask_path"), None
        if decision == "corrected":
            data = payload.get("mask_data", "")
            if not isinstance(data, str) or not data.startswith("data:image/png;base64,") or len(data) > 20*1024*1024:
                raise HTTPException(422, "A corrected PNG mask is required (maximum 15 MB)")
            try:
                raw = base64.b64decode(data.split(",", 1)[1], validate=True)
                with Image.open(io.BytesIO(raw)) as im:
                    if im.format != "PNG" or im.size != (width, height):
                        raise ValueError("Mask dimensions must match the original")
                    mask = im.convert("L").point(lambda p: 255 if p >= 128 else 0)
            except Exception as exc:
                raise HTTPException(422, "Invalid PNG mask or incorrect dimensions") from exc
            relative = Path("results") / image_id / f"review_{revision}_{uuid.uuid4().hex[:8]}.png"
            target = m.DATA_ROOT / session / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            mask.save(target)
            mask_path = str(relative)
            import numpy as np
            from .measurements import measure_mask
            final = measure_mask(np.asarray(mask) > 0, gsd)
        elif decision == "confirmed":
            final = decode(row.get("measurement"), None)
            mask_path = None
        elif decision == "rejected":
            mask_path = None
            final = {"crack_pixels": 0, "component_count": 0, "length_px": 0,
                     "width_px_max": 0, "width_px_mean": 0, "length_mm": 0 if gsd else None,
                     "width_mm_max": 0 if gsd else None, "gsd_mm_per_px": gsd}
        elif decision == "pending":
            # Manual measurements can be saved while AI is queued or failed.
            decision = row.get("review_status", "pending")
            final = decode(row.get("final_measurement"), None)
        saved_at = datetime.now(timezone.utc).isoformat()
        snapshot = {"decision": decision, "reviewer": reviewer, "note": str(payload.get("note", ""))[:4000],
                    "mask_path": mask_path, "manual_measurements": measurements, "final_measurement": final,
                    "previous_decision": row.get("review_status", "pending")}
        with m.session_db(session) as con:
            ensure_review_schema(con)
            changed = con.execute("""UPDATE images SET review_status=?,reviewer_name=?,reviewer_note=?,reviewed_at=?,
                review_requested=1,reviewer_mask_path=?,manual_measurements=?,final_measurement=?,revision=?
                WHERE id=? AND revision=?""", (decision, reviewer, snapshot["note"], saved_at, mask_path,
                json.dumps(measurements), json.dumps(final), revision, image_id, revision-1)).rowcount
            if not changed:
                raise HTTPException(409, "This image was updated elsewhere. Reopen it before saving.")
            con.execute("INSERT INTO review_events VALUES (?,?,?,?,?)",
                        (uuid.uuid4().hex, image_id, revision, saved_at, json.dumps(snapshot)))
        return image_detail(project_id, session, image_id)

    @router.get("/projects/{project_id}/images/{session}/{image_id}/history")
    def history(project_id: str, session: str, image_id: str):
        row = scoped_image(project_id, session, image_id)
        with m.session_db(session) as con:
            exists = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='review_events'").fetchone()
            events = [dict(r) for r in con.execute("SELECT * FROM review_events WHERE image_id=? ORDER BY revision DESC", (image_id,))] if exists else []
        for event in events:
            event["snapshot"] = json.loads(event["snapshot"])
        return {"events": events, "legacy_review": row.get("reviewed_at") if not events else None}

    def csv_bytes(items):
        buffer = io.StringIO(newline="")
        fields = ["project_id", "session", "id", "filename", "captured_at", "status", "review_status", "reviewer_name",
                  "reviewed_at", "reviewer_note", "revision", "latitude", "longitude", "model", "ai_length_px",
                  "ai_width_px_max", "final_length_px", "final_width_px_max", "final_length_mm", "final_width_mm_max", "manual_measurements"]
        writer = csv.DictWriter(buffer, fieldnames=fields)
        writer.writeheader()
        for item in items:
            row = {key: item.get(key, "") for key in fields}
            ai, final, meta = item["measurement"] or {}, item["final_measurement"] or {}, item["metadata"]
            # Historical confirmations use the preserved AI result; corrected legacy masks are not substituted with AI measurements.
            if item["review_status"] == "confirmed" and not final:
                final = ai
            row.update(latitude=meta.get("latitude"), longitude=meta.get("longitude"), model=meta.get("inference_model", "Unknown (historical)"),
                       ai_length_px=ai.get("length_px"), ai_width_px_max=ai.get("width_px_max"),
                       final_length_px=final.get("length_px"), final_width_px_max=final.get("width_px_max"),
                       final_length_mm=final.get("length_mm"), final_width_mm_max=final.get("width_mm_max"),
                       manual_measurements=json.dumps(item["manual_measurements"]))
            # Avoid spreadsheet formulas from user-entered names and notes.
            writer.writerow({key: "'"+value if isinstance(value,str) and value.startswith(("=", "+", "-", "@")) else value for key,value in row.items()})
        return buffer.getvalue().encode("utf-8-sig")

    @router.get("/projects/{project_id}/export/{format}")
    def export(project_id: str, format: str, session: str | None = None, reviewed_only: bool = False):
        project, flights, all_items = all_images(project_id, session)
        counts = summary(all_items)
        items = [r for r in all_items if not reviewed_only or r["review_status"] in FINAL_DECISIONS]
        if format not in {"csv", "zip", "pdf"}:
            raise HTTPException(422, "Choose CSV, ZIP or PDF")
        name = f"inspection-{project_id[:12]}-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.{format}"
        headers = {"Content-Disposition": f'attachment; filename="{name}"'}
        report = {"project": project, "sessions": flights, "generated_at": datetime.now(timezone.utc).isoformat(),
                  "summary": counts, "reviewed_only": reviewed_only, "included_images": len(items),
                  "measurement_note": "Pixel measurements are retained. Physical units require recorded GSD. Field accuracy has not been established.",
                  "images": items}
        if format == "csv":
            return Response(csv_bytes(items), media_type="text/csv", headers=headers)
        if format == "pdf":
            # Pillow's PDF writer avoids a separate report renderer dependency.
            try:
                font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 20)
                title_font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 34)
            except OSError:
                font = ImageFont.load_default(size=20)
                title_font = ImageFont.load_default(size=34)
            lines = ["CRACKSTATION / INSPECTION REPORT", project["name"] if project_id != "legacy" else "Imported inspections",
                     "Generated: " + report["generated_at"], f"Review status: {'COMPLETE' if counts['complete'] else 'INCOMPLETE'}",
                     f"Total images: {counts['total']} | Reviewed: {counts['reviewed']} | Needs recheck: {counts['uncertain']}",
                     f"Included in this export: {len(items)} | Scope: {'reviewed images' if reviewed_only else 'all images'}",
                     "Measurements: px unless calibrated GSD is recorded. Field error: not evaluated.", "",
                     "IMAGE / SESSION / DECISION / REVIEWER"]
            for item in items:
                lines.extend([item["filename"][:88], f"  {item['session']} | {item['review_status']} | {item.get('reviewer_name') or 'Unreviewed'}"])
            pages = []
            for start in range(0, len(lines), 35):
                page = Image.new("RGB", (1240,1754), "white")
                draw = ImageDraw.Draw(page)
                for index,line in enumerate(lines[start:start+35]):
                    draw.text((65,65+index*43), line[:100], font=title_font if start+index == 0 else font, fill="#18312c")
                draw.text((65,1675),f"CrackStation | Page {len(pages)+1}",font=font,fill="#52645d")
                pages.append(page)
            buffer = io.BytesIO()
            pages[0].save(buffer, "PDF", save_all=True, append_images=pages[1:], resolution=150)
            return Response(buffer.getvalue(), media_type="application/pdf", headers=headers)
        archive = tempfile.SpooledTemporaryFile(max_size=16*1024*1024)
        try:
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("report.json", json.dumps(report, indent=2, ensure_ascii=False))
                z.writestr("images.csv", csv_bytes(items))
                for item in items:
                    base = m.DATA_ROOT / item["session"]
                    prefix = f"{item['session']}/{item['id']}"
                    files = {"original/"+Path(item["filename"]).name: base/"originals"/item["id"]/item["filename"],
                             "ai-mask.png": base/"results"/item["id"]/"mask.png",
                             "ai-overlay.jpg": base/"results"/item["id"]/"overlay.jpg"}
                    if item.get("reviewer_mask_path"):
                        files["reviewed-mask.png"] = base/item["reviewer_mask_path"]
                    for target,path in files.items():
                        if path.resolve().is_relative_to(base.resolve()) and path.is_file():
                            z.write(path, f"{prefix}/{target}")
                    z.writestr(f"{prefix}/review-history.json",json.dumps(history(project_id,item["session"],item["id"]),indent=2))
            archive.seek(0)
        except Exception:
            archive.close()
            raise
        def chunks():
            try:
                while block := archive.read(1024*1024):
                    yield block
            finally:
                archive.close()
        return StreamingResponse(chunks(), media_type="application/zip", headers=headers)

    m.app.include_router(router)

"""AIHawk dashboard: the jobs, the applications, and applying, from one local page.

Runs on 127.0.0.1 only. It reads the same store the terminal app uses, and applies through the same Session, so a send from
here is a send from there. While the terminal app is open, applying is refused: both drive one browser and one database.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from aihawk.config import default_home
from aihawk.store import SUBMITTED, Store

PORT = int(os.environ.get("AIHAWK_PORT", "8765"))
DB = default_home() / "data" / "jobs.db"

# One batch at a time: the browser is one, and so is the person watching it.
STATE: dict[str, Any] = {
    "running": False,
    "stopping": False,
    "total": 0,
    "done": 0,
    "current": "",
    "log": [],
    "results": [],
    "started": "",
}
TASK: asyncio.Task[None] | None = None


def query(sql: str, parameters: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    """Rows from this account's store; none at all before the account has one, which a new account has not."""
    if not DB.is_file():
        return []
    try:
        connection = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute(sql, parameters)]
    except sqlite3.Error:
        return []
    finally:
        connection.close()


def terminal_app_open() -> bool:
    """Whether the terminal AIHawk is running: it holds the browser and the folder, so a send from here would collide."""
    if sys.platform != "win32":
        return False
    try:
        done = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq aihawk.exe", "/NH"], capture_output=True, text=True, timeout=10, check=False,
        )
    except OSError:
        return False
    return "aihawk.exe" in done.stdout.lower()


def browser_busy() -> bool:
    """Whether the one browser is already in use: by the terminal app, or by a batch that is still sending."""
    if terminal_app_open():
        return True
    try:
        import psutil
    except ImportError:
        return False
    for process in psutil.process_iter(["cmdline"]):
        command = process.info["cmdline"] or []
        if command[-2:] == ["-m", "invisible_playwright_mcp"]:
            return True
        if any("_apply_batch.py" in str(part) for part in command[:3]):
            return True
    return False


def log(line: str) -> None:
    stamp = datetime.now().strftime("%H:%M:%S")
    STATE["log"].append(f"{stamp}  {line}")
    del STATE["log"][:-400]


REFRESH: dict[str, Any] = {"running": False, "message": "", "new": 0}


async def refresh_jobs() -> None:
    """The job database brought up to date, searched again, and the new postings scored. No browser, only the AI service."""
    from aihawk.session import Session

    REFRESH.update({"running": True, "message": "bringing the job database up to date…", "new": 0})
    session = Session(default_home())
    try:
        await session.sync(
            lambda done, size: REFRESH.update(message=f"downloading chunks {done}/{size}"),
            lambda: REFRESH.update(message="a chunk arrived"),
        )
        REFRESH.update(message="searching")
        new = await session.find()
        REFRESH.update(new=new, message=f"{new} new postings, scoring…")
        total = 0
        while True:
            scored = await session.read(lambda line: REFRESH.update(message=line))
            if not scored:
                break
            total += scored
            REFRESH.update(message=f"scored {total} postings")
        REFRESH.update(message=f"done: {new} new postings found, {total} scored")
    except Exception as error:  # noqa: BLE001  said on the page, nothing else
        REFRESH.update(message=f"failed: {type(error).__name__}: {str(error)[:160]}")
    finally:
        session.close()
        REFRESH["running"] = False


async def apply_batch(ids: list[str], skip_duplicates: bool, trial: bool = False) -> None:
    """Every job named, one after another, through the terminal app's own Session. A trial fills every form and sends none."""
    from aihawk.llm import ACCOUNT_CAUSES
    from aihawk.session import Session

    if trial:
        os.environ["AIHAWK_DRY_RUN"] = "1"
    session = Session(default_home())
    try:
        chosen: list[Any] = []
        seen: set[tuple[str, str]] = set()
        for job_id in ids:
            item = session.store.get(job_id)
            if item is None:
                continue
            if item.status in SUBMITTED:
                log(f"skip {item.job.company}: already sent")
                continue
            if skip_duplicates:
                key = (item.job.company.strip().lower(), item.job.title.strip().lower())
                if key in seen:
                    log(f"skip duplicate {item.job.company} | {item.job.title}")
                    continue
                seen.add(key)
            chosen.append(item)

        STATE["total"] = len(chosen)
        log(f"{len(chosen)} applications to send")
        for number, item in enumerate(chosen, 1):
            if STATE["stopping"]:
                log("stopped by the person")
                break
            STATE["current"] = f"{item.job.company} — {item.job.title}"
            STATE["done"] = number - 1
            log(f"[{number}/{len(chosen)}] {item.job.company} | {item.job.title} ({item.job.url})")
            try:
                outcome = await session.apply(item, log)
            except Exception as error:  # noqa: BLE001  one job's failure is reported and recorded; the batch goes on
                log(f"    failed: {type(error).__name__}: {str(error)[:160]}")
                STATE["results"].append({"job": item.job.id, "company": item.job.company, "status": "failed", "reason": str(error)[:120]})
                continue
            STATE["done"] = number
            STATE["results"].append({
                "job": item.job.id, "company": item.job.company, "title": item.job.title,
                "status": outcome.status, "reason": outcome.reason, "summary": outcome.summary[:300],
            })
            log(f"    -> {outcome.status} / {outcome.reason}")
            if outcome.reason in ACCOUNT_CAUSES:
                log("stopped: the account or the key")
                break
    finally:
        session.close()
        if trial:
            os.environ.pop("AIHAWK_DRY_RUN", None)
        STATE["running"] = False
        STATE["stopping"] = False
        STATE["current"] = ""
        log("batch finished")


async def index(request):
    return HTMLResponse(PAGE)


async def api_stats(request):
    rows = query("select status, count(*) as n from jobs group by status")
    counts = {row["status"]: row["n"] for row in rows}
    found = query(
        "select sum(case when score >= 4 then 1 else 0 end) as s4, sum(case when score >= 3 then 1 else 0 end) as s3,"
        " count(*) as all_scored from jobs where score is not null"
    )
    scores = found[0] if found else {"s4": 0, "s3": 0, "all_scored": 0}
    return JSONResponse({
        "counts": counts,
        "scored": scores["all_scored"] or 0,
        "band4": scores["s4"] or 0,
        "band3": scores["s3"] or 0,
        "terminal_open": terminal_app_open(),
        "busy": browser_busy(),
        "running": STATE["running"],
        "account": default_home().name or str(default_home()),
    })


async def api_jobs(request):
    min_score = float(request.query_params.get("min_score", "0"))
    status = request.query_params.get("status", "ready")
    text = (request.query_params.get("q") or "").strip()
    limit = min(int(request.query_params.get("limit", "300")), 1000)

    where = ["score >= ?"]
    parameters: list[Any] = [min_score]
    if status == "ready":
        where.append("status in ('scored','new')")
    elif status and status != "all":
        where.append("status = ?")
        parameters.append(status)
    if text:
        where.append("(lower(company) like ? or lower(title) like ? or lower(location) like ?)")
        like = f"%{text.lower()}%"
        parameters += [like, like, like]

    rows = query(
        "select id, company, title, location, url, status, score, reason, sent_at from jobs"
        f" where {' and '.join(where)} order by score desc, company limit {limit}",
        tuple(parameters),
    )
    for row in rows:
        row["site"] = (row["url"] or "").split("//")[-1].split("/")[0]
    return JSONResponse(rows)


async def api_applications(request):
    rows = query(
        "select company, title, location, status, reason, sent_at, final_url, substr(note,1,400) as note from jobs"
        " where status not in ('new','scored','passed_over') order by coalesce(sent_at, updated) desc limit 300"
    )
    return JSONResponse(rows)


def key_state() -> dict[str, Any]:
    """Whether a key is kept and whether it is sealed. Neither its value nor any part of it leaves this function."""
    try:
        raw = (default_home() / "key").read_text(encoding="utf-8-sig").strip()
    except OSError:
        return {"configured": False, "sealed": False}
    return {"configured": bool(raw), "sealed": raw.startswith("dpapi:")}


async def api_key(request):
    from aihawk.config import load_config

    config = load_config(default_home())
    if request.method == "POST":
        from openai import AsyncOpenAI

        body = await request.json()
        value = str(body.get("key") or "").strip()
        if not value:
            return JSONResponse({"error": "the key is empty"}, status_code=400)
        client = AsyncOpenAI(base_url=config.base_url, api_key=value, max_retries=0, timeout=20)
        try:
            await client.get("/models", cast_to=dict)
        except Exception as error:  # noqa: BLE001  an answer that is not a refusal leaves the key to be tried in real use
            if type(error).__name__ in ("AuthenticationError", "PermissionDeniedError"):
                return JSONResponse({"error": "the service refused that key"}, status_code=400)
        try:
            config.save_key(value)
        except OSError as error:
            return JSONResponse({"error": f"could not save: {error}"}, status_code=500)
        return JSONResponse({"saved": True, **key_state(), "base_url": config.base_url})
    return JSONResponse({**key_state(), "base_url": config.base_url, "key_env": config.key_env()})


async def api_resume(request):
    """The CV: read back, or replaced with an uploaded one, from which the search plan is written again."""
    from aihawk.config import load_config

    if request.method == "POST":
        form = await request.form()
        upload = form.get("file")
        if upload is None:
            return JSONResponse({"error": "no file came with the upload"}, status_code=400)
        data = await upload.read()
        if not data:
            return JSONResponse({"error": "that file is empty"}, status_code=400)
        suffix = Path(str(getattr(upload, "filename", "") or "")).suffix.lower() or ".pdf"
        if suffix not in (".pdf", ".docx", ".doc", ".txt", ".md"):
            return JSONResponse({"error": "a PDF, Word or text file"}, status_code=400)
        import tempfile

        from aihawk.session import Session

        folder = Path(tempfile.mkdtemp(prefix="rainy-cv-"))
        # The file keeps the name it was uploaded with, which is the name AIHawk shows for the resume.
        target = folder / (Path(str(getattr(upload, "filename", "") or "")).name or f"resume{suffix}")
        target.write_bytes(data)
        session = Session(default_home())
        try:
            await session.load_resume(target)
        except Exception as error:  # noqa: BLE001  said on the page
            return JSONResponse({"error": f"could not read that resume: {type(error).__name__}: {str(error)[:160]}"}, status_code=422)
        finally:
            session.close()
        config = load_config(default_home())
        return JSONResponse({"loaded": True, "resume": attached_name(), "titles": config.filters.titles()})
    config = load_config(default_home())
    return JSONResponse({"resume": attached_name(), "titles": config.filters.titles(), "cv": config.cv.is_file()})


def attached_name() -> str:
    try:
        return (default_home() / "data" / "cvs" / "cv-name.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


async def api_answers(request):
    """The answers the forms are given: read, and one set (an empty value removes it) with POST."""
    if request.method == "POST":
        body = await request.json()
        name = "_".join(str(body.get("name") or "").strip().lower().split())
        value = str(body.get("value") or "").strip()
        if not name:
            return JSONResponse({"error": "an answer needs a name"}, status_code=400)
        if name == "account_password":
            return JSONResponse({"error": "the account password is not changed here"}, status_code=400)
        store = Store(DB)
        try:
            store.set_answer(name, value)
        finally:
            store.close()
    return JSONResponse(query("select name, value from answers where name != 'account_password' order by name"))


CHAT: list[dict[str, str]] = []


def chat_context() -> str:
    rows = query("select name, value from answers where name != 'account_password' order by name")
    counts = {row["status"]: row["n"] for row in query("select status, count(*) as n from jobs group by status")}
    lines = [f"- {row['name']}: {row['value']}" for row in rows]
    return (
        "You are Rainy, the assistant inside a job-application tool. You help the person understand their job search and "
        "how the tool works. Be brief and concrete; use plain sentences.\n\n"
        "What the forms are given about them:\n" + "\n".join(lines) + "\n\n"
        f"Jobs: {counts.get('scored', 0)} scored and waiting, {counts.get('submitted', 0)} applications submitted, "
        f"{counts.get('blocked', 0)} blocked. The applications are sent by the tool on company forms; you cannot send one."
    )


async def api_chat(request):
    from openai import AsyncOpenAI

    from aihawk.config import load_config

    if request.method == "POST":
        body = await request.json()
        message = str(body.get("message") or "").strip()
        if not message:
            return JSONResponse({"error": "nothing to send"}, status_code=400)
        config = load_config(default_home())
        key = config.api_key()
        if not key:
            return JSONResponse({"error": "no key yet: add one in the API key panel"}, status_code=400)
        CHAT.append({"role": "user", "content": message})
        client = AsyncOpenAI(base_url=config.base_url, api_key=key, max_retries=1, timeout=120)
        try:
            answer = await client.chat.completions.create(
                model=config.quick_model,
                messages=[{"role": "system", "content": chat_context()}, *CHAT[-12:]],
            )
            reply = answer.choices[0].message.content or ""
        except Exception as error:  # noqa: BLE001  said in the chat
            CHAT.pop()
            return JSONResponse({"error": f"{type(error).__name__}: {str(error)[:160]}"}, status_code=502)
        CHAT.append({"role": "assistant", "content": reply})
        del CHAT[:-24]
    return JSONResponse(CHAT)


async def api_apply(request):
    global TASK
    if STATE["running"]:
        return JSONResponse({"error": "a batch is already sending"}, status_code=409)
    if browser_busy():
        return JSONResponse({"error": "the browser is in use: close the terminal AIHawk, or wait for the batch that is sending"}, status_code=409)
    body = await request.json()
    ids = [str(job_id) for job_id in body.get("ids") or []]
    if not ids:
        return JSONResponse({"error": "no jobs selected"}, status_code=400)
    skip_duplicates = bool(body.get("skip_duplicates", True))
    trial = bool(body.get("trial", False))
    STATE.update({"running": True, "stopping": False, "total": 0, "done": 0, "current": "", "log": [], "results": [],
                  "started": datetime.now().strftime("%H:%M:%S"), "trial": trial})
    TASK = asyncio.create_task(apply_batch(ids, skip_duplicates, trial))
    return JSONResponse({"started": len(ids)})


async def api_stop(request):
    STATE["stopping"] = True
    return JSONResponse({"stopping": True})


async def api_refresh(request):
    if REFRESH["running"]:
        return JSONResponse({"error": "already fetching"}, status_code=409)
    if STATE["running"]:
        return JSONResponse({"error": "a batch is sending; fetch after it"}, status_code=409)
    asyncio.create_task(refresh_jobs())
    return JSONResponse({"started": True})


async def api_refresh_state(request):
    return JSONResponse(REFRESH)


def _meta(page: str, prop: str) -> str:
    """The content of a meta tag, by property or name, whichever order the page wrote it in."""
    import re

    for pattern in (
        rf'<meta[^>]+property=["\']{re.escape(prop)}["\'][^>]*content=["\']([^"\']*)',
        rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]*property=["\']{re.escape(prop)}["\']',
        rf'<meta[^>]+name=["\']{re.escape(prop)}["\'][^>]*content=["\']([^"\']*)',
    ):
        found = re.search(pattern, page, re.IGNORECASE)
        if found:
            return _strip_html(found.group(1))
    return ""


def _strip_html(text: str) -> str:
    import html as htmlmodule
    import re

    plain = re.sub(r"<[^>]+>", " ", text or "")
    return " ".join(htmlmodule.unescape(plain).split())


def jsonld_posting(page: str) -> dict[str, Any] | None:
    """The schema.org JobPosting a page carries, which most job sites publish for search engines."""
    import json as jsonmodule
    import re

    for match in re.finditer(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page, re.DOTALL | re.IGNORECASE):
        try:
            data = jsonmodule.loads(match.group(1).strip())
        except ValueError:
            continue
        for item in data if isinstance(data, list) else [data]:
            if not isinstance(item, dict):
                continue
            kinds = item.get("@type")
            kinds = kinds if isinstance(kinds, list) else [kinds]
            if "JobPosting" in kinds:
                return item
    return None


async def api_add_job(request):
    """One job by its link: read from the page, added to the store and scored, so it can be applied to like any other."""
    import httpx

    from aihawk.database import Job
    from aihawk.session import Session

    body = await request.json()
    url = str(body.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        return JSONResponse({"error": "paste a link that starts with http"}, status_code=400)
    if "linkedin.com" in url:
        return JSONResponse({
            "error": "LinkedIn postings are left out on purpose: applying there needs a LinkedIn session and breaks their "
                     "terms. Open the posting, choose 'Apply on company site', and paste that link instead.",
        }, status_code=400)
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30, headers={
            "User-Agent": "Mozilla/5.0 (compatible; AIHawk dashboard)",
        }) as client:
            page = await client.get(url)
    except Exception as error:  # noqa: BLE001  said on the page
        return JSONResponse({"error": f"could not open that link: {type(error).__name__}"}, status_code=502)
    if page.status_code >= 400:
        return JSONResponse({"error": f"the page answered HTTP {page.status_code}"}, status_code=502)

    posting = jsonld_posting(page.text) or {}
    title = str(posting.get("title") or "").strip()
    if not title:
        title = _meta(page.text, "og:title")
    for prefix in ("Job Application for ", "Apply - ", "Careers - "):
        if title.lower().startswith(prefix.lower()):
            title = title[len(prefix):]
    organisation = posting.get("hiringOrganization") or {}
    name = organisation.get("name") if isinstance(organisation, dict) else organisation
    company = str(name or "").strip()
    place = posting.get("jobLocation") or {}
    if isinstance(place, list):
        place = place[0] if place else {}
    address = place.get("address") if isinstance(place, dict) else {}
    if isinstance(address, dict):
        location = ", ".join(str(address.get(key)) for key in ("addressLocality", "addressRegion", "addressCountry") if address.get(key))
    else:
        location = str(address or "")
    description = _strip_html(str(posting.get("description") or "")) or _meta(page.text, "og:description")

    if not title:
        import re

        found = re.search(r"<title[^>]*>(.*?)</title>", page.text, re.DOTALL | re.IGNORECASE)
        title = _strip_html(found.group(1)) if found else ""
        if not title:
            return JSONResponse({"error": "that page names no job"}, status_code=422)
    if not company:
        company = _meta(page.text, "og:site_name")
    if not company:
        parts = url.split("//")[-1].split("/")
        company = parts[1] if len(parts) > 1 and parts[1] else parts[0]

    job = Job(id=url, company=company, title=title, location=location, url=url, apply_url=url, description=description)
    session = Session(default_home())
    try:
        added = session.store.add_all([job])
        if not added:
            stored = session.store.get(url)
            return JSONResponse({"duplicate": True, "title": stored.job.title if stored else title,
                                 "company": stored.job.company if stored else company,
                                 "score": stored.score if stored else None})
        await session.read(lambda line: None)
        stored = session.store.get(url)
        return JSONResponse({
            "added": True, "title": title, "company": company, "location": location,
            "score": stored.score if stored else None, "status": stored.status if stored else "new",
        })
    finally:
        session.close()


async def api_progress(request):
    return JSONResponse(STATE)


app = Starlette(routes=[
    Route("/", index),
    Route("/api/stats", api_stats),
    Route("/api/jobs", api_jobs),
    Route("/api/applications", api_applications),
    Route("/api/answers", api_answers, methods=["GET", "POST"]),
    Route("/api/chat", api_chat, methods=["GET", "POST"]),
    Route("/api/resume", api_resume, methods=["GET", "POST"]),
    Route("/api/apply", api_apply, methods=["POST"]),
    Route("/api/stop", api_stop, methods=["POST"]),
    Route("/api/refresh", api_refresh, methods=["POST"]),
    Route("/api/refresh-state", api_refresh_state),
    Route("/api/add-job", api_add_job, methods=["POST"]),
    Route("/api/key", api_key, methods=["GET", "POST"]),
    Route("/api/progress", api_progress),
])


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Rainy dashboard</title>
<style>
  :root { color-scheme: dark; --bg:#101317; --panel:#171b21; --line:#252b33; --text:#e6e9ee; --dim:#8b94a3;
          --good:#3fb950; --warn:#d29922; --bad:#f85149; --accent:#2f81f7; }
  * { box-sizing: border-box; }
  body { margin:0; font:14px/1.45 "Segoe UI",system-ui,sans-serif; background:var(--bg); color:var(--text); }
  header { display:flex; gap:24px; align-items:center; padding:14px 20px; border-bottom:1px solid var(--line); flex-wrap:wrap; }
  h1 { font-size:16px; margin:0; letter-spacing:.3px; }
  .stat { color:var(--dim); } .stat b { color:var(--text); font-size:15px; }
  main { display:grid; grid-template-columns: 1fr 380px; gap:0; height:calc(100vh - 58px); }
  .left, .right { overflow:auto; }
  .right { border-left:1px solid var(--line); background:var(--panel); }
  .bar { display:flex; gap:8px; align-items:center; padding:10px 14px; position:sticky; top:0; background:var(--bg);
         border-bottom:1px solid var(--line); z-index:2; flex-wrap:wrap; }
  input, select, button { background:#1d222a; color:var(--text); border:1px solid var(--line); border-radius:6px;
                          padding:6px 9px; font:inherit; }
  button { cursor:pointer; } button:hover { border-color:#3a4350; }
  button.primary { background:var(--accent); border-color:var(--accent); color:#fff; font-weight:600; }
  button.danger { background:#3a1d1d; border-color:#5a2a2a; color:#ffb3ad; }
  table { width:100%; border-collapse:collapse; }
  th, td { text-align:left; padding:6px 10px; border-bottom:1px solid var(--line); white-space:nowrap; overflow:hidden;
           text-overflow:ellipsis; max-width:340px; }
  th { color:var(--dim); font-weight:600; position:sticky; top:47px; background:var(--bg); z-index:1; }
  tr:hover td { background:#141920; }
  .score { font-variant-numeric:tabular-nums; font-weight:600; }
  .s45 { color:var(--good); } .s40 { color:#7ee787; } .s30 { color:var(--warn); } .s00 { color:var(--dim); }
  .tag { font-size:11px; padding:2px 7px; border-radius:10px; background:#232a33; color:var(--dim); }
  .tag.submitted { background:#12331b; color:#7ee787; }
  .tag.blocked, .tag.refused, .tag.failed { background:#3a1d1d; color:#ffb3ad; }
  .panel { padding:12px 14px; border-bottom:1px solid var(--line); }
  .panel h2 { font-size:12px; text-transform:uppercase; letter-spacing:.8px; color:var(--dim); margin:0 0 8px; }
  #log { font:12px/1.5 Consolas,monospace; white-space:pre-wrap; word-break:break-word; max-height:52vh; overflow:auto; }
  #chat { display:flex; flex-direction:column; gap:6px; max-height:34vh; overflow:auto; }
  .msg { padding:6px 9px; border-radius:9px; max-width:96%; white-space:pre-wrap; word-break:break-word; }
  .msg.user { align-self:flex-end; background:#1d2b3a; }
  .msg.bot { background:#1b2027; }
  .msg.err { background:#3a1d1d; color:#ffb3ad; }
  .muted { color:var(--dim); }
  .pbar { height:6px; background:#222831; border-radius:3px; overflow:hidden; margin:8px 0; }
  .pbar > div { height:100%; width:0; background:var(--accent); transition:width .3s; }
  a { color:var(--accent); text-decoration:none; }
  @media (max-width: 980px) {
    main { grid-template-columns: 1fr; height:auto; }
    .right { border-left:none; border-top:1px solid var(--line); }
    .left, .right { overflow:visible; }
    th { position:static; }
    #log { max-height:30vh; }
  }
</style></head>
<body>
<header>
  <h1>Rainy</h1>
  <span class="muted" id="account"></span>
  <div class="stat">Jobs <b id="s-jobs">–</b></div>
  <div class="stat">Scored <b id="s-scored">–</b></div>
  <div class="stat">≥4.0 <b id="s-band4">–</b></div>
  <div class="stat">≥3.0 <b id="s-band3">–</b></div>
  <div class="stat">Submitted <b id="s-sub">–</b></div>
  <div class="stat">Blocked <b id="s-blocked">–</b></div>
  <div class="stat" id="s-terminal"></div>
</header>
<main>
  <div class="left">
    <div class="bar">
      <select id="status">
        <option value="ready">Ready to apply</option>
        <option value="all">All statuses</option>
        <option value="submitted">Submitted</option>
        <option value="blocked">Blocked</option>
      </select>
      <label class="muted">min score <input id="min" type="number" value="3" min="0" max="5" step="0.1" style="width:74px"></label>
      <input id="q" placeholder="company, title, city…" style="width:220px">
      <button id="refresh">Refresh</button>
      <button id="fetch">Fetch new jobs</button>
      <span class="muted" id="fetchstate"></span>
      <input id="newurl" placeholder="paste a job link to add it…" style="width:300px">
      <button id="addjob">Add job</button>
      <button id="select-all">Select shown</button>
      <button id="clear">Clear</button>
      <button id="apply" class="primary">Apply selected</button>
      <button id="stop" class="danger">Stop</button>
      <label class="muted" title="fill every form, send none"><input type="checkbox" id="trial"> trial</label>
      <span class="muted" id="selcount">0 selected</span>
    </div>
    <table>
      <thead><tr><th style="width:30px"></th><th style="width:60px">Score</th><th>Company</th><th>Role</th>
      <th>Location</th><th>Site</th><th>Status</th></tr></thead>
      <tbody id="rows"></tbody>
    </table>
  </div>
  <div class="right">
    <div class="panel">
      <h2>Resume</h2>
      <div id="resumestate" class="muted">loading…</div>
      <div style="display:flex; gap:6px; margin-top:8px">
        <input id="resumefile" type="file" accept=".pdf,.docx,.doc,.txt,.md" style="flex:1; padding:4px">
        <button id="resumeupload">Upload</button>
      </div>
    </div>
    <div class="panel">
      <h2>Progress</h2>
      <div id="progress" class="muted">idle</div>
      <div class="pbar"><div id="pfill"></div></div>
      <div id="log"></div>
    </div>
    <div class="panel">
      <h2>API key</h2>
      <div id="keystate" class="muted">loading…</div>
      <div style="display:flex; gap:6px; margin-top:8px">
        <input id="keyinput" type="password" placeholder="paste a key to replace it" style="flex:1">
        <button id="keysave">Save</button>
      </div>
    </div>
    <div class="panel">
      <h2>Answers the forms get — edit any</h2>
      <div id="answers" class="muted">loading…</div>
      <div style="display:flex; gap:6px; margin-top:8px">
        <input id="newanswer" placeholder="name" style="width:36%">
        <input id="newvalue" placeholder="value" style="flex:1">
        <button id="addanswer">Add</button>
      </div>
    </div>
    <div class="panel">
      <h2>Chat with Rainy</h2>
      <div id="chat"></div>
      <div style="display:flex; gap:6px; margin-top:8px">
        <input id="chatinput" placeholder="ask anything…" style="flex:1">
        <button id="chatsend">Send</button>
      </div>
    </div>
  </div>
</main>
<script>
const selected = new Set();
let shown = [];
const $ = (id) => document.getElementById(id);

function money(n){ return n.toLocaleString(); }

async function loadStats(){
  const s = await (await fetch('/api/stats')).json();
  $('s-jobs').textContent = money(s.scored);
  $('s-scored').textContent = money(s.scored);
  $('s-band4').textContent = money(s.band4);
  $('s-band3').textContent = money(s.band3);
  $('s-sub').textContent = money(s.counts.submitted || 0);
  $('s-blocked').textContent = money(s.counts.blocked || 0);
  $('s-terminal').textContent = s.busy ? '⚠ browser in use — applying is locked until it is free' : '';
  if (s.account && $('account')) $('account').textContent = '· account: ' + s.account;
}

async function loadJobs(){
  const q = new URLSearchParams({
    min_score: $('min').value || '0', status: $('status').value, q: $('q').value || '', limit: '400'});
  shown = await (await fetch('/api/jobs?' + q)).json();
  const body = document.getElementById('rows');
  body.innerHTML = shown.map(j => {
    const cls = j.score >= 4.5 ? 's45' : j.score >= 4.0 ? 's40' : j.score >= 3.0 ? 's30' : 's00';
    const checked = selected.has(j.id) ? 'checked' : '';
    return `<tr>
      <td><input type="checkbox" data-id="${j.id}" ${checked}></td>
      <td class="score ${cls}">${(j.score ?? 0).toFixed(1)}</td>
      <td title="${j.company}">${j.company}</td>
      <td title="${j.title}"><a href="${j.url}" target="_blank" rel="noreferrer">${j.title}</a></td>
      <td class="muted" title="${j.location || ''}">${(j.location || '').slice(0, 34)}</td>
      <td class="muted">${j.site}</td>
      <td><span class="tag ${j.status}">${j.status}</span></td></tr>`;
  }).join('');
  body.querySelectorAll('input[type=checkbox]').forEach(box => {
    box.onchange = () => { box.checked ? selected.add(box.dataset.id) : selected.delete(box.dataset.id); showCount(); };
  });
  showCount();
}

function showCount(){ $('selcount').textContent = selected.size + ' selected'; }

async function loadProgress(){
  const p = await (await fetch('/api/progress')).json();
  $('progress').textContent = p.running
    ? `sending ${p.done}/${p.total} — ${p.current || 'starting'}`
    : (p.results.length ? `last batch: ${p.results.length} jobs` : 'idle');
  $('pfill').style.width = p.total ? (100 * p.done / p.total) + '%' : '0';
  $('log').textContent = (p.log || []).slice(-120).join('\\n');
  $('log').scrollTop = $('log').scrollHeight;
  return p.running;
}

async function loadKey(){
  const k = await (await fetch('/api/key')).json();
  $('keystate').textContent = k.configured
    ? `saved — ${k.sealed ? 'encrypted for this Windows user' : 'kept as plain text'} · ${k.base_url}`
    : `no key yet — ${k.base_url}; paste one below or set ${k.key_env}`;
}

$('keysave').onclick = async () => {
  const key = $('keyinput').value.trim();
  if (!key) { alert('Paste a key first.'); return; }
  const button = $('keysave');
  button.disabled = true; button.textContent = 'Checking…';
  try {
    const r = await fetch('/api/key', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({key})});
    const d = await r.json();
    if (!r.ok) { alert(d.error || 'could not save that key'); return; }
    $('keyinput').value = '';
    loadKey();
  } catch (error) {
    // A dead server or a dropped connection left this button saying "Checking…" forever before; it always recovers now.
    alert('Could not reach the dashboard. Is Rainy still running?');
  } finally {
    button.disabled = false; button.textContent = 'Save';
  }
};

async function loadResume(){
  const r = await (await fetch('/api/resume')).json();
  $('resumestate').textContent = r.cv
    ? `attached: ${r.resume || 'resume'} — searching: ${r.titles || '—'}`
    : 'no resume yet — upload one below';
}

$('resumeupload').onclick = async () => {
  const file = $('resumefile').files[0];
  if (!file) { alert('Choose a file first.'); return; }
  const button = $('resumeupload');
  button.disabled = true; button.textContent = 'Reading…';
  const form = new FormData();
  form.append('file', file);
  const r = await fetch('/api/resume', {method:'POST', body: form});
  const d = await r.json();
  button.disabled = false; button.textContent = 'Upload';
  if (!r.ok) { alert(d.error || 'could not read that resume'); return; }
  alert(`Resume read. Searching for: ${d.titles}`);
  loadResume();
};

function escapeHtml(text){
  return String(text ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
}

function renderAnswers(rows){
  $('answers').innerHTML = rows.map(r => `
    <div style="display:flex; gap:6px; margin-bottom:4px; align-items:center">
      <span class="muted" style="width:33%; overflow:hidden; text-overflow:ellipsis" title="${escapeHtml(r.name)}">${escapeHtml(r.name)}</span>
      <input value="${escapeHtml(r.value)}" data-name="${escapeHtml(r.name)}" style="flex:1">
      <button data-del="${escapeHtml(r.name)}" title="remove this answer">×</button>
    </div>`).join('');
  $('answers').querySelectorAll('input[data-name]').forEach(box => {
    box.onchange = () => saveAnswer(box.dataset.name, box.value);
  });
  $('answers').querySelectorAll('button[data-del]').forEach(btn => {
    btn.onclick = () => { if (confirm(`Remove the answer "${btn.dataset.del}"?`)) saveAnswer(btn.dataset.del, ''); };
  });
}

async function loadAnswers(){
  renderAnswers(await (await fetch('/api/answers')).json());
}

async function saveAnswer(name, value){
  const r = await fetch('/api/answers', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({name, value})});
  const d = await r.json();
  if (!r.ok) { alert(d.error || 'could not save that answer'); loadAnswers(); return; }
  renderAnswers(d);
}

$('addanswer').onclick = () => {
  const name = $('newanswer').value.trim(), value = $('newvalue').value.trim();
  if (!name || !value) { alert('Give the answer a name and a value.'); return; }
  saveAnswer(name, value).then(() => { $('newanswer').value = ''; $('newvalue').value = ''; });
};

async function loadChat(){
  const rows = await (await fetch('/api/chat')).json();
  $('chat').innerHTML = (rows || []).map(m =>
    `<div class="msg ${m.role === 'user' ? 'user' : 'bot'}">${escapeHtml(m.content)}</div>`).join('');
  $('chat').scrollTop = $('chat').scrollHeight;
}

async function sendChat(){
  const message = $('chatinput').value.trim();
  if (!message) return;
  $('chatinput').value = '';
  const mine = document.createElement('div');
  mine.className = 'msg user'; mine.textContent = message;
  $('chat').appendChild(mine);
  const waiting = document.createElement('div');
  waiting.className = 'msg bot muted'; waiting.textContent = '…';
  $('chat').appendChild(waiting);
  $('chat').scrollTop = $('chat').scrollHeight;
  const r = await fetch('/api/chat', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({message})});
  const d = await r.json();
  if (!r.ok) {
    waiting.className = 'msg err';
    waiting.textContent = d.error || 'the service did not answer';
    return;
  }
  loadChat();
}

$('chatsend').onclick = sendChat;
$('chatinput').addEventListener('keydown', e => { if (e.key === 'Enter') sendChat(); });

$('refresh').onclick = () => { loadStats(); loadJobs(); };
$('fetch').onclick = async () => {
  const r = await fetch('/api/refresh', {method:'POST'});
  const d = await r.json();
  if (!r.ok) alert(d.error || 'could not start');
};

$('addjob').onclick = async () => {
  const url = $('newurl').value.trim();
  if (!url) return;
  $('addjob').disabled = true; $('addjob').textContent = 'Adding…';
  const r = await fetch('/api/add-job', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({url})});
  const d = await r.json();
  $('addjob').disabled = false; $('addjob').textContent = 'Add job';
  if (!r.ok) { alert(d.error || 'could not add that link'); return; }
  if (d.duplicate) alert(`Already in the list: ${d.company} — ${d.title}` + (d.score ? ` (score ${d.score})` : ''));
  else alert(`Added: ${d.company} — ${d.title}` + (d.score ? ` (score ${d.score})` : ' (unscored)'));
  $('newurl').value = '';
  loadStats(); loadJobs();
};

async function loadRefresh(){
  const s = await (await fetch('/api/refresh-state')).json();
  $('fetchstate').textContent = s.message || '';
  $('fetch').disabled = s.running;
  if (!s.running && window._wasFetching) { loadStats(); loadJobs(); }
  window._wasFetching = s.running;
}
$('status').onchange = loadJobs;
$('min').onchange = loadJobs;
$('q').oninput = () => { clearTimeout(window._t); window._t = setTimeout(loadJobs, 250); };
$('select-all').onclick = () => { shown.forEach(j => selected.add(j.id)); loadJobs(); };
$('clear').onclick = () => { selected.clear(); loadJobs(); };
$('apply').onclick = async () => {
  if (!selected.size) { alert('Select jobs first.'); return; }
  if (!confirm(`Send ${selected.size} application(s) for real? They go to the employers in your name.`)) return;
  const r = await fetch('/api/apply', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ids: [...selected], skip_duplicates: true, trial: $('trial').checked})});
  const data = await r.json();
  if (!r.ok) { alert(data.error || 'could not start'); return; }
  selected.clear(); showCount();
  poll();
};
$('stop').onclick = async () => { await fetch('/api/stop', {method:'POST'}); };

async function poll(){
  const running = await loadProgress();
  if (running) setTimeout(poll, 2000);
  else { loadStats(); loadJobs(); }
}

loadStats(); loadJobs(); loadAnswers(); loadKey(); loadResume(); loadChat(); loadProgress(); loadRefresh();
setInterval(() => {
  loadProgress().then(r => { if (r) { loadStats(); } });
  loadRefresh();
}, 5000);
</script></body></html>
"""


if __name__ == "__main__":
    print(f"Rainy dashboard on http://127.0.0.1:{PORT}")
    print(f"database: {DB}")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")

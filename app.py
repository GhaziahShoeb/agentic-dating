import re, uuid, threading
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from pipeline import db, scrape_instagram, scrape_linkedin, analyze
from dating import words, one

app = FastAPI(title="Agentic Dating")
JOBS = {}
LI = re.compile(r"^https?://([a-z]{2,3}\.)?linkedin\.com/in/[^/?#\s]+", re.I)
IG = re.compile(r"^https?://(www\.)?instagram\.com/[^/?#\s]+", re.I)


def clean(u):
    return u.strip().split("?")[0].split("#")[0].rstrip("/")


def friendly(e):
    s = str(e)
    if isinstance(e, IndexError) or "not found" in s.lower():
        return "No data came back. Check that both links are correct and the Instagram account is public."
    if "private" in s.lower():
        return "That Instagram account is private. Use a public account."
    if "429" in s or "rate limit" in s.lower():
        return "The AI is rate limited right now. Try again in a few minutes."
    if "402" in s or "limit" in s.lower() and "apify" in s.lower():
        return "The profile scraper has run out of credits."
    return "Something went wrong: " + s[:150]


def run_dates(pid, job, k=3):
    people = db.table("people").select("id,name,profile,gender").execute().data
    by_id = {p["id"]: p for p in people}
    me = by_id[pid]
    if not me.get("gender"):
        raise ValueError("Gender is missing for this person")
    wa = words(me["profile"] or {})
    sims = sorted(
        ((len(wa & words(b["profile"] or {})) / (len(wa | words(b["profile"] or {})) or 1), b["id"])
         for b in people
         if b["id"] != pid and b.get("gender") and b["gender"] != me["gender"]),
        reverse=True)[:k]
    done = {tuple(sorted((r["a_id"], r["b_id"])))
            for r in db.table("dates").select("a_id,b_id").execute().data}
    todo = [t for t in (tuple(sorted((pid, b))) for _, b in sims) if t not in done]
    for i, t in enumerate(todo, 1):
        other = by_id[t[0] if t[1] == pid else t[1]]
        job["msg"] = f"Date {i} of {len(todo)}: {me['name']} meets {other['name']}"
        one(t, by_id)


def job_add(job, ig, li, gender):
    try:
        job.update(step=0, msg="Reading LinkedIn and Instagram")
        with ThreadPoolExecutor(2) as ex:
            fl, fg = ex.submit(scrape_linkedin, li), ex.submit(scrape_instagram, ig)
            l, g = fl.result(), fg.result()
        job.update(step=2, msg="Writing the profile")
        prof = analyze(g, l)
        res = db.table("people").insert({
            "name": l["name"], "linkedin_url": li, "instagram_url": ig,
            "profile": prof, "sources": {"instagram": g, "linkedin": l},
            "gender": gender}).execute()
        pid = res.data[0]["id"]
        job.update(person_id=pid, step=3, msg="Agents are going on dates")
        run_dates(pid, job)
        job.update(status="done", step=4, msg="Done")
    except Exception as e:
        job.update(status="error", msg=friendly(e))


def job_dates(job, pid):
    try:
        job.update(step=3, person_id=pid, msg="Agents are going on dates")
        run_dates(pid, job)
        job.update(status="done", step=4, msg="Done")
    except Exception as e:
        job.update(status="error", msg=friendly(e))


def start(fn, *args):
    if sum(j["status"] == "running" for j in JOBS.values()) >= 3:
        raise HTTPException(429, "Three people are already being processed. Try again in a minute.")
    jid = uuid.uuid4().hex[:10]
    JOBS[jid] = job = {"status": "running", "step": 0, "msg": "Starting", "person_id": None}
    threading.Thread(target=fn, args=(job, *args), daemon=True).start()
    return {"job": jid}


class NewPerson(BaseModel):
    linkedin_url: str
    instagram_url: str
    gender: str


@app.post("/api/people")
def add_person(p: NewPerson):
    li, ig, g = clean(p.linkedin_url), clean(p.instagram_url), p.gender.strip().lower()
    if not LI.match(li):
        raise HTTPException(400, "That does not look like a LinkedIn profile link (linkedin.com/in/...).")
    if not IG.match(ig):
        raise HTTPException(400, "That does not look like an Instagram profile link (instagram.com/username).")
    if g not in ("man", "woman"):
        raise HTTPException(400, "Choose man or woman.")
    for r in db.table("people").select("id,linkedin_url").execute().data:
        if clean(r["linkedin_url"]).lower() == li.lower():
            return {"existing": r["id"]}
    return start(job_add, ig, li, g)


@app.post("/api/people/{pid}/dates")
def more_dates(pid: int):
    return start(job_dates, pid)


@app.get("/api/jobs/{jid}")
def job(jid: str):
    if jid not in JOBS:
        raise HTTPException(404, "Unknown job")
    return JOBS[jid]


@app.get("/api/stats")
def stats():
    people = db.table("people").select("id", count="exact").execute().count
    ds = db.table("dates").select("id,a_id,b_id,mutual").order("mutual", desc=True).execute().data
    best = None
    if ds:
        d = ds[0]
        names = {r["id"]: r["name"] for r in
                 db.table("people").select("id,name").in_("id", [d["a_id"], d["b_id"]]).execute().data}
        best = {"id": d["id"], "a": names.get(d["a_id"]), "b": names.get(d["b_id"]),
                "mutual": round(d["mutual"])}
    return {"people": people, "dates": len(ds), "best": best}


@app.get("/api/people")
def people():
    rows = db.table("people").select(
        "id,name,gender,summary:profile->>summary,headline:sources->linkedin->>headline"
    ).order("name").execute().data
    count = {}
    for r in db.table("dates").select("a_id,b_id").execute().data:
        for k in (r["a_id"], r["b_id"]):
            count[k] = count.get(k, 0) + 1
    for r in rows:
        r["dates"] = count.get(r["id"], 0)
    return rows


@app.get("/api/people/{pid}")
def person(pid: int):
    r = db.table("people").select("*").eq("id", pid).execute().data
    if not r:
        raise HTTPException(404, "Person not found")
    r = r[0]
    li = (r.get("sources") or {}).get("linkedin") or {}
    ig = (r.get("sources") or {}).get("instagram") or {}
    return {"id": r["id"], "name": r["name"], "gender": r.get("gender"),
            "linkedin_url": r["linkedin_url"], "instagram_url": r["instagram_url"],
            "profile": r.get("profile") or {}, "headline": li.get("headline"),
            "location": li.get("location"), "followers": ig.get("followers"),
            "ig_bio": ig.get("bio")}


@app.get("/api/people/{pid}/matches")
def matches(pid: int):
    rows = db.table("dates").select(
        "id,a_id,b_id,a_score,b_score,a_note,b_note,mutual"
    ).or_(f"a_id.eq.{pid},b_id.eq.{pid}").execute().data
    ids = {(r["b_id"] if r["a_id"] == pid else r["a_id"]) for r in rows}
    names = {}
    if ids:
        names = {p["id"]: p for p in db.table("people").select(
            "id,name,headline:sources->linkedin->>headline").in_("id", list(ids)).execute().data}
    out = []
    for r in rows:
        mine = r["a_id"] == pid
        oid = r["b_id"] if mine else r["a_id"]
        out.append({"date_id": r["id"], "other_id": oid, "other": names.get(oid, {}).get("name"),
                    "headline": names.get(oid, {}).get("headline"),
                    "mutual": round(r["mutual"] or 0),
                    "my_score": r["a_score"] if mine else r["b_score"],
                    "their_score": r["b_score"] if mine else r["a_score"],
                    "my_note": r["a_note"] if mine else r["b_note"]})
    out.sort(key=lambda x: x["mutual"], reverse=True)
    return out


@app.get("/api/dates/{did}")
def get_date(did: int):
    r = db.table("dates").select("*").eq("id", did).execute().data
    if not r:
        raise HTTPException(404, "Date not found")
    r = r[0]
    names = {p["id"]: p["name"] for p in db.table("people").select("id,name")
             .in_("id", [r["a_id"], r["b_id"]]).execute().data}
    r["a_name"], r["b_name"] = names.get(r["a_id"]), names.get(r["b_id"])
    return r


@app.get("/")
def index():
    return FileResponse("static/index.html")


app.mount("/static", StaticFiles(directory="static"), name="static")
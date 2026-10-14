import os, re, json, time
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv
from groq import Groq
from supabase import create_client

load_dotenv()
groq = Groq(api_key=os.getenv("GROQ_API_KEY"))
db = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SECRET_KEY"))
MODELS = ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"]
TURNS = 3   # messages per agent
K = 5       # dates each agent goes on (most similar people of the other gender)
CATS = ["values", "lifestyle", "communication", "dealbreakers"]
PLANS = {}


def llm(messages, json_mode=False):
    for n in range(9):
        m = MODELS[n % len(MODELS)]
        try:
            kw = {"response_format": {"type": "json_object"}} if json_mode else {}
            r = groq.chat.completions.create(
                model=m, max_completion_tokens=700 if json_mode else 300,
                messages=messages, **kw)
            return r.choices[0].message.content.strip()
        except Exception as e:
            print("retry", m, str(e)[:80])
            if n % len(MODELS) == len(MODELS) - 1:
                time.sleep(20)
    raise RuntimeError("Groq failed")


def tr(i):
    return i.get("trait", "") if isinstance(i, dict) else str(i)


def words(p):
    w = set()
    for k in ["hobbies", "interests", "values", "needs"]:
        for i in p.get(k) or []:
            w |= set(re.findall(r"[a-z]{4,}", tr(i).lower()))
    return w


def pick_pairs(people, k=K):
    pairs = set()
    for a in people:
        if not a.get("gender"):
            continue
        wa = words(a["profile"] or {})
        sims = []
        for b in people:
            if b["id"] == a["id"] or not b.get("gender") or a["gender"] == b["gender"]:
                continue
            wb = words(b["profile"] or {})
            sims.append((len(wa & wb) / (len(wa | wb) or 1), b["id"]))
        for _, bid in sorted(sims, reverse=True)[:k]:
            pairs.add(tuple(sorted((a["id"], bid))))
    return pairs


def brief(p):
    pr = p["profile"] or {}
    b = {k: [tr(i) for i in (pr.get(k) or [])] if isinstance(pr.get(k), list) else pr.get(k)
         for k in ["needs", "hobbies", "interests", "values",
                   "communication_style", "looking_for_in_a_partner", "dealbreakers"]}
    b["summary"] = pr.get("summary")
    return b


def system(p, plan=None):
    s = (f"You are the dating agent for {p['name']}, on a first date on their behalf. "
         f"Speak in first person as {p['name']}, 1-2 casual sentences. "
         "Use ONLY facts from this profile, never invent anything. "
         f"Profile: {json.dumps(brief(p))}")
    if plan:
        s += (f" Your plan for this date: {plan} Work these in naturally, one at a time, "
              "and react to what the other person actually says.")
    return s


def make_plan(p):
    """The agent decides what it wants to learn on a date, from ITS OWN person only."""
    if p["id"] not in PLANS:
        PLANS[p["id"]] = llm([
            {"role": "system", "content": system(p)},
            {"role": "user", "content":
             "Before the date, write your plan in 2 short sentences: the 2 things you most "
             "want to find out about the other person (based on your needs and values) "
             "and 1 dealbreaker to watch for. No preamble."}])
    return PLANS[p["id"]]


def date(a, b, plans):
    log = []
    for _ in range(TURNS):
        for me, other in ((a, b), (b, a)):
            msgs = [{"role": "system", "content": system(me, plans[me["id"]])}]
            if not log:
                msgs.append({"role": "user", "content": "Start the date with a friendly opener."})
            for m in log:
                msgs.append({"role": "assistant" if m["who"] == me["name"] else "user",
                             "content": m["text"]})
            log.append({"who": me["name"], "text": llm(msgs)})
    return log


def clamp(x):
    try:
        return max(0, min(100, int(float(x))))
    except Exception:
        return None


def score(me, other, log):
    txt = "\n".join(f"{m['who']}: {m['text']}" for m in log)
    out = llm([{"role": "system", "content": system(me)},
               {"role": "user", "content":
                f"Here is the date with {other['name']}:\n{txt}\n"
                "Judge how well they fit YOU. Return JSON with integers 0-100 for: "
                "values, lifestyle, communication, dealbreakers (100 = none triggered), "
                'score (overall), and reason (max 25 words). Example: {"values":70,"lifestyle":60,'
                '"communication":80,"dealbreakers":100,"score":72,"reason":"..."}'}],
              json_mode=True)
    d = json.loads(out)
    detail = {c: clamp(d.get(c)) for c in CATS}
    vals = [v for v in detail.values() if v is not None]
    s = clamp(d.get("score"))
    if s is None:
        s = round(sum(vals) / len(vals)) if vals else 50
    return s, str(d.get("reason", ""))[:200], detail


def one(pair, by_id):
    a, b = by_id[pair[0]], by_id[pair[1]]
    plans = {a["id"]: make_plan(a), b["id"]: make_plan(b)}
    log = date(a, b, plans)
    sa, na, da = score(a, b, log)
    sb, nb, dbb = score(b, a, log)
    mutual = 2 * sa * sb / (sa + sb) if sa + sb else 0
    row = {"a_id": a["id"], "b_id": b["id"], "transcript": log, "a_score": sa, "b_score": sb,
           "a_note": na, "b_note": nb, "mutual": mutual}
    extra = {"a_plan": plans[a["id"]], "b_plan": plans[b["id"]], "a_detail": da, "b_detail": dbb}
    try:
        db.table("dates").insert({**row, **extra}).execute()
    except Exception:  # extra columns not added yet
        db.table("dates").insert(row).execute()
    print("dated", a["name"], "x", b["name"], round(mutual))


def date_new(pid):
    people = db.table("people").select("id,name,profile,gender").execute().data
    by_id = {p["id"]: p for p in people}
    me = by_id[pid]
    wa = words(me["profile"] or {})
    sims = sorted(((len(wa & words(b["profile"] or {})) / (len(wa | words(b["profile"] or {})) or 1), b["id"])
                   for b in people if b["id"] != pid and me.get("gender") and b.get("gender")
                   and me["gender"] != b["gender"]), reverse=True)[:3]
    for _, bid in sims:
        one(tuple(sorted((pid, bid))), by_id)


if __name__ == "__main__":
    people = db.table("people").select("id,name,profile,gender").execute().data
    by_id = {p["id"]: p for p in people}
    done = {tuple(sorted((r["a_id"], r["b_id"])))
            for r in db.table("dates").select("a_id,b_id").execute().data}
    todo = [p for p in pick_pairs(people) if p not in done]
    print(len(people), "people,", len(todo), "dates to run")
    with ThreadPoolExecutor(2) as ex:
        list(ex.map(lambda p: one(p, by_id), todo))
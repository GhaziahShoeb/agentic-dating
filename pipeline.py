import os, re, json, time
from dotenv import load_dotenv
from apify_client import ApifyClient
from groq import Groq
from supabase import create_client


def save_person(ig_url, li_url, gender=None):
    li = scrape_linkedin(li_url)
    ig = scrape_instagram(ig_url)
    row = {"name": li["name"], "linkedin_url": li_url,
           "instagram_url": ig_url, "profile": analyze(ig, li),
           "sources": {"instagram": ig, "linkedin": li},
           "gender": gender}
    res = db.table("people").insert(row).execute()
    row["id"] = res.data[0]["id"]
    return row
load_dotenv()
apify = ApifyClient(os.getenv("APIFY_TOKEN"))
groq = Groq(api_key=os.getenv("GROQ_API_KEY"))
db = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SECRET_KEY"))

def ig_username(url):
    return url.rstrip("/").split("instagram.com/")[-1].split("?")[0]

def scrape_instagram(url):
    run = apify.actor("apify/instagram-profile-scraper").call(
        run_input={"usernames": [ig_username(url)]})
    p = list(apify.dataset(run.default_dataset_id).iterate_items())[0]
    if p.get("private"): raise ValueError("Instagram account is private")
    bio = re.sub(r"\S+@\S+", "", p.get("biography") or "")  # drop emails
    posts = [{"caption": (x.get("caption") or "")[:300],
              "hashtags": x.get("hashtags") or []}
             for x in (p.get("latestPosts") or [])[:12]]
    return {"name": p.get("fullName"), "bio": bio,
            "category": p.get("businessCategoryName"),
            "followers": p.get("followersCount"), "posts": posts}
def scrape_linkedin(url):
    run = apify.actor("harvestapi/linkedin-profile-scraper").call(
        run_input={"urls": [url]})
    p = list(apify.dataset(run.default_dataset_id).iterate_items())[0]
    if not p.get("firstName"):
        raise ValueError("LinkedIn profile not found")
    keep = ["headline", "about", "industry", "currentPosition", "experience",
            "education", "skills", "interests", "certifications",
            "projects", "honorsAndAwards", "languages", "volunteering"]
    out = {k: p.get(k) for k in keep}
    out["name"] = f"{p.get('firstName')} {p.get('lastName')}"
    out["location"] = (p.get("location") or {}).get("linkedinText")
    return out  # keep it small for the AI
PROMPT = """You analyze a real person from ONLY the LinkedIn and Instagram data given.
Never invent facts. Do not infer religion, health, sexuality or relationship history.
Posts marked #ad are sponsored: never use them as evidence of needs or interests.
Return JSON with keys:
summary (a 2-3 sentence string),
needs, hobbies, interests, values, communication_style,
looking_for_in_a_partner (qualities that would suit them, based on their values and lifestyle, each marked as an inference),
dealbreakers (only if the data supports it).
"needs" means personal needs (ambition, creative freedom, social energy), not product needs.
Every list item is {"trait": str, "evidence": "short quote or field from the data"}.
Use empty lists if there is no evidence."""

def analyze(instagram, linkedin):
    payload = json.dumps({"instagram": instagram, "linkedin": linkedin})[:9000]
    for _ in range(3):
        try:
            r = groq.chat.completions.create(
                model="openai/gpt-oss-120b",
                response_format={"type": "json_object"},
                max_completion_tokens=3000,
                messages=[{"role": "system", "content": PROMPT},
                          {"role": "user", "content": payload}])
            return json.loads(r.choices[0].message.content)
        except Exception as e:
            print("retrying analysis:", str(e)[:100])
            time.sleep(25)
    raise RuntimeError("analysis failed")

if __name__ == "__main__":
    r = save_person("https://www.instagram.com/piyanshimehra/",
                    "https://www.linkedin.com/in/piyanshi-mehra-628312341/")
    print("saved:", r["name"])
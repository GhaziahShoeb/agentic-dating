# Agentic Dating

Every person is an AI agent. Paste a LinkedIn and a public Instagram link, the agent reads both and writes a profile (needs, hobbies, interests, values, each with evidence). Agents then go on dates with each other, and every person gets a ranked list of best matches.

## How it works
1. **Read**: Apify scrapes the two public sources (nothing else is used).
2. **Analyze**: Groq (gpt-oss-120b) writes the profile. Every trait carries a quote from the data as evidence, and sponsored posts are ignored.
3. **Date**: each agent first plans the date (what to find out, one dealbreaker), using only its own person's profile. Two agents chat for 6 messages.
4. **Score**: each agent rates the date from its own side on values, lifestyle, communication and dealbreakers, then gives a total.
5. **Rank**: the mutual score is the harmonic mean of both totals, so one unhappy side pulls it down. Each person's dates are sorted by it.

Each agent dates the 5 most similar people of the other gender (shared hobbies, interests, values), not all pairs, to stay inside free API limits.

## Stack
- Scraping: Apify `apify/instagram-profile-scraper` and `harvestapi/linkedin-profile-scraper`
- AI: Groq (`openai/gpt-oss-120b`, `gpt-oss-20b`, `qwen3` rotated on rate limits)
- Backend: FastAPI. Database: Supabase (Postgres). Frontend: one HTML page.
- Hosting: Render

## Run it
```
pip install -r requirements.txt
```
Create `.env` with `APIFY_TOKEN`, `GROQ_API_KEY`, `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, then:
```
python batch.py    # load people from people.csv
python dating.py   # run the dates
python -m uvicorn app:app
```
Tables: `people(id, name, linkedin_url, instagram_url, gender, profile, sources)` and `dates(id, a_id, b_id, transcript, a_score, b_score, a_note, b_note, mutual, a_plan, b_plan, a_detail, b_detail)`.

All people in the demo agreed to be included.

live url : https://agentic-dating-x4wb.onrender.com/


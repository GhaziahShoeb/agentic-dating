import csv, time
from pipeline import save_person, db

done = {r["linkedin_url"] for r in
        db.table("people").select("linkedin_url").execute().data}

for row in csv.DictReader(open("people.csv")):
    if row["linkedin_url"] in done:
        continue
    try:
        r = save_person(row["instagram_url"], row["linkedin_url"], row.get("gender"))
        print("ok |", r["name"], "|", r["sources"]["instagram"]["name"], "|",
              (r["sources"]["linkedin"]["headline"] or "")[:60])
    except Exception as e:
        print("FAIL", row["linkedin_url"], e)
    time.sleep(8)
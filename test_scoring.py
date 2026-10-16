"""Offline scoring tests. No Discord connection required."""
import datetime
import importlib
import json
import os

os.environ["DB_PATH"] = "data/test_run.db"
if os.path.exists("data/test_run.db"):
    os.remove("data/test_run.db")

import config
importlib.reload(config)
config.START_DATE = None
config.END_DATE = None
import db
importlib.reload(db)
import scoring
importlib.reload(scoring)

FAKE_DAY = ["2026-10-18"]
db.today_et = lambda: FAKE_DAY[0]
scoring.db.today_et = db.today_et


def day(value):
    FAKE_DAY[0] = value


def reset():
    path = os.environ["DB_PATH"]
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(path + suffix)
        except FileNotFoundError:
            pass
    return db.init()


def ok(condition, message):
    if not condition:
        raise AssertionError(message)
    print("  ✅ " + message)


con = reset()
print("=== 1. check-in + First Haunt ===")
day("2026-10-18")
r = scoring.checkin(con, "u1", "Alice")
ok(r["points"] == 1, "first check-in base score is +1")
ok(r["first_haunt"] == 2 and r["explorer"] == 0, "first valid activity awards First Haunt +2 only")
ok(scoring.personal(con, "u1")["points"] == 3, "personal total is 1 + 2 = 3")
try:
    scoring.checkin(con, "u1", "Alice")
    raise AssertionError("duplicate check-in accepted")
except scoring.AlreadyDone:
    ok(True, "duplicate check-in blocked")

print("\n=== 2. UGC registration: Forum thread + caps ===")
r = scoring.register_ugc_post(con, "t1", "forum", "u2", "Bob")
ok(r["status"] == "registered" and r["points"] == 5, "first tagged Forum post registered +5")
ok(r["first_haunt"] == 2, "UGC can be a user's First Haunt")
r2 = scoring.register_ugc_post(con, "t2", "forum", "u2", "Bob")
ok(r2["status"] == "ineligible", "second same-day tagged post is non-scoring")
for i, d in enumerate(["19", "20", "21", "22"], start=2):
    day(f"2026-10-{d}")
    rr = scoring.register_ugc_post(con, f"t{i+1}", "forum", "u2", "Bob")
    ok(rr["status"] == "registered", f"UGC entry #{i} registered")
day("2026-10-23")
r6 = scoring.register_ugc_post(con, "t99", "forum", "u2", "Bob")
ok(r6["status"] == "ineligible", "sixth scoring entry blocked by campaign cap")

print("\n=== 3. voting: unique, no self-vote, first 3/day score ===")
day("2026-10-18")
try:
    scoring.cast_vote(con, "t1", "u2", "Bob")
    raise AssertionError("self vote accepted")
except scoring.InvalidVote:
    ok(True, "self vote blocked")

v = scoring.cast_vote(con, "t1", "v1", "Voter")
ok(v["scored"] and v["points"] == 1, "first valid vote scores +1")
ok(v["first_haunt"] == 2, "vote can be a user's First Haunt")
try:
    scoring.cast_vote(con, "t1", "v1", "Voter")
    raise AssertionError("duplicate vote accepted")
except scoring.AlreadyDone:
    ok(True, "duplicate vote on same entry blocked")

for n in range(3):
    uid = f"author{n}"
    tid = f"vx{n}"
    scoring.register_ugc_post(con, tid, "forum", uid, uid)
results = [scoring.cast_vote(con, f"vx{n}", "v1", "Voter") for n in range(3)]
ok(results[0]["scored"] and results[1]["scored"] and not results[2]["scored"],
   "only first 3 total votes of the day earn voter points")

print("\n=== 4. Halloween Explorer after 3 distinct mission types ===")
con.close(); con = reset(); day("2026-10-18")
# Type 1: check-in
x1 = scoring.checkin(con, "explorer", "Explorer")
ok(x1["first_haunt"] == 2 and x1["task_types"] == 1, "type 1 recorded + First Haunt")
# Type 2: UGC
x2 = scoring.register_ugc_post(con, "explorer-post", "forum", "explorer", "Explorer")
ok(x2["explorer"] == 0 and x2["task_types"] == 2, "type 2 recorded, Explorer not yet unlocked")
# Set up another user's post so Explorer can vote = type 3.
scoring.register_ugc_post(con, "other-post", "forum", "other", "Other")
x3 = scoring.cast_vote(con, "other-post", "explorer", "Explorer")
ok(x3["explorer"] == 3 and x3["task_types"] == 3, "third distinct type unlocks Halloween Explorer +3")
# A fourth type must not award Explorer again.
now = db.now_dt()
con.execute(
    "INSERT INTO quiz_posted(quiz_id,message_id,channel_id,posted_at,closes_at,revealed_at,correct_choice,question_text,options_json) "
    "VALUES(?,?,?,?,?,NULL,?,?,?)",
    ("explorer-q", "1", "1", now.isoformat(timespec="seconds"),
     (now + datetime.timedelta(hours=24)).isoformat(timespec="seconds"),
     "B", "Question", json.dumps({"A":"a","B":"b","C":"c","D":"d"}))
)
con.commit()
x4 = scoring.answer_quiz(con, "explorer", "Explorer", "explorer-q", "A")
ok(not x4["correct"] and x4["points"] == 0, "wrong quiz answer gives no quiz points")
ok(x4["explorer"] == 0 and x4["task_types"] == 4, "fourth type is recorded but Explorer bonus does not repeat")
p = scoring.personal(con, "explorer")
ok(p["explorer_unlocked"] and p["task_types"] == 4, "profile exposes exploration progress")

print("\n=== 5. votes received milestones award author ===")
con.close(); con = reset(); day("2026-10-18")
scoring.register_ugc_post(con, "milestone-thread", "forum", "author", "Author")
bonuses = []
for i in range(1, 31):
    rv = scoring.cast_vote(con, "milestone-thread", f"v{i}", f"Voter{i}")
    bonuses.extend(rv["author_bonuses"])
ok(bonuses == [(5, 2), (10, 3), (20, 5), (30, 5)], f"author bonuses fired once at 5/10/20/30: {bonuses}")
p = scoring.personal(con, "author")
ok(p["votes_received"] == 30, "profile counts 30 votes received")
ok(p["breakdown"].get("ugc_vote_bonus") == 15, "vote milestone bonus total is +15")

print("\n=== 6. community progress excludes First Haunt / Explorer / vote-received bonuses ===")
# Author UGC = 8 community; 30 voters each have one scoring vote = 30 community.
sp = scoring.server_progress(con)
ok(sp["total"] == 38, f"community total is 38 (actual {sp['total']})")

print("\n=== 7. legacy per-task first bonuses do not count if old DB rows exist ===")
# Simulate an old-v2 event and make sure ranking ignores it.
scoring.ensure_user(con, "legacy", "Legacy")
con.execute(
    "INSERT INTO events(user_id,kind,points,community_points,counts_personal,day,ref,created_at) VALUES(?,?,?,?,?,?,?,?)",
    ("legacy", "first", 10, 0, 0, db.today_et(), "checkin", db.now_iso()),
)
con.commit()
ok(scoring.personal(con, "legacy")["points"] == 0, "legacy first bonus is excluded from personal score")

print("\n=== 8. leaderboard/profile merged data is queryable ===")
rank, total = scoring.rank_of(con, "author")
ok(rank is not None and total >= 1, f"author ranked #{rank} among {total}")
p = scoring.personal(con, "author")
ok(p["ugc_entries"] == 1 and p["votes_received"] == 30, "profile includes UGC entries and votes received")

print("\n=== 9. quiz snapshot, one answer each, closed quiz blocked ===")
now = db.now_dt()
con.execute(
    "INSERT INTO quiz_posted(quiz_id,message_id,channel_id,posted_at,closes_at,revealed_at,correct_choice,question_text,options_json) "
    "VALUES(?,?,?,?,?,NULL,?,?,?)",
    ("q1", "1", "1", now.isoformat(timespec="seconds"),
     (now + datetime.timedelta(hours=24)).isoformat(timespec="seconds"),
     "B", "Question", json.dumps({"A":"a","B":"b","C":"c","D":"d"}))
)
con.commit()
q = scoring.answer_quiz(con, "quiz-user", "Quiz User", "q1", "B")
ok(q["points"] == 3 and q["correct"], "correct quiz answer scores +3")
ok(q["first_haunt"] == 2, "quiz can be a user's First Haunt")
try:
    scoring.answer_quiz(con, "quiz-user", "Quiz User", "q1", "B")
    raise AssertionError("duplicate quiz accepted")
except scoring.AlreadyDone:
    ok(True, "duplicate quiz answer blocked")
con.execute("UPDATE quiz_posted SET revealed_at=? WHERE quiz_id='q1'", (db.now_iso(),))
con.commit()
try:
    scoring.answer_quiz(con, "late-user", "Late", "q1", "B")
    raise AssertionError("closed quiz accepted")
except scoring.QuizClosed:
    ok(True, "revealed/closed quiz blocks late answers")

con.close()
for suffix in ("", "-wal", "-shm"):
    try:
        os.remove("data/test_run.db" + suffix)
    except FileNotFoundError:
        pass
print("\nAll offline scoring tests passed.")

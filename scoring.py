"""Scoring engine. All point mutations go through this module."""
from __future__ import annotations

import sqlite3
from typing import Any

import config
import db


class AlreadyDone(Exception):
    pass


class ActivityClosed(Exception):
    pass


class InvalidVote(Exception):
    pass


class QuizClosed(Exception):
    pass


def _assert_activity_open() -> None:
    if not config.activity_is_open(db.today_et()):
        raise ActivityClosed("The Halloween activity is not open right now.")


def _insert(con, user_id, kind, points, day, ref=None, community_points=0, counts_personal=1):
    con.execute(
        "INSERT INTO events(user_id,kind,points,community_points,counts_personal,day,ref,created_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (str(user_id), kind, points, community_points, counts_personal, day, ref, db.now_iso()),
    )


def ensure_user(con, user_id, display):
    con.execute(
        "INSERT INTO users(user_id,display,joined_at) VALUES(?,?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET display=excluded.display",
        (str(user_id), display, db.now_iso()),
    )


def _record_participation(con, user_id, task_kind, day):
    """Record one distinct activity type and award lightweight discovery bonuses.

    - First Haunt: +2 once, on the user's first valid activity of any type.
    - Halloween Explorer: +3 once, after 3 distinct activity types.
    Metadata participation rows carry zero personal/community points.
    """
    added = False
    try:
        _insert(
            con, user_id, "participation", 0, day, ref=task_kind,
            community_points=0, counts_personal=0,
        )
        added = True
    except sqlite3.IntegrityError:
        pass

    task_types = con.execute(
        "SELECT COUNT(*) FROM events WHERE user_id=? AND kind='participation'",
        (str(user_id),),
    ).fetchone()[0]

    first_haunt = 0
    explorer = 0

    if added and task_types == 1:
        try:
            _insert(con, user_id, "first_haunt", config.PTS_FIRST_HAUNT, day)
            first_haunt = config.PTS_FIRST_HAUNT
        except sqlite3.IntegrityError:
            pass

    if task_types >= config.EXPLORER_TASK_TYPES:
        try:
            _insert(con, user_id, "explorer", config.PTS_EXPLORER, day)
            explorer = config.PTS_EXPLORER
        except sqlite3.IntegrityError:
            pass

    return {
        "first_haunt": first_haunt,
        "explorer": explorer,
        "task_types": task_types,
    }

def _award_milestones(con, user_id, day):
    n = con.execute(
        "SELECT COUNT(*) FROM events WHERE user_id=? AND kind='checkin'", (str(user_id),)
    ).fetchone()[0]
    got = []
    for threshold, bonus in sorted(config.MILESTONES.items()):
        if n >= threshold:
            try:
                _insert(con, user_id, "milestone", bonus, day, ref=str(threshold))
                got.append((threshold, bonus))
            except sqlite3.IntegrityError:
                pass
    return got


def _claim_new_nodes(con):
    sp = server_progress(con)
    claimed = []
    for idx, (threshold, name) in enumerate(config.NODES):
        if sp["pct"] < threshold:
            continue
        try:
            con.execute(
                "INSERT INTO community_nodes(node_idx,unlocked_at) VALUES(?,?)",
                (idx, db.now_iso()),
            )
            claimed.append((idx, threshold, name))
        except sqlite3.IntegrityError:
            pass
    return claimed


# ---------- scoring actions ----------

def checkin(con, user_id, display):
    _assert_activity_open()
    user_id, day = str(user_id), db.today_et()
    ensure_user(con, user_id, display)
    try:
        _insert(con, user_id, "checkin", config.PTS_CHECKIN, day,
                community_points=config.COMM_CHECKIN)
    except sqlite3.IntegrityError:
        raise AlreadyDone("Already checked in today")
    participation = _record_participation(con, user_id, "checkin", day)
    milestones = _award_milestones(con, user_id, day)
    total_days = con.execute(
        "SELECT COUNT(*) FROM events WHERE user_id=? AND kind='checkin'", (user_id,)
    ).fetchone()[0]
    new_nodes = _claim_new_nodes(con)
    con.commit()
    return {
        "points": config.PTS_CHECKIN,
        "first_haunt": participation["first_haunt"],
        "explorer": participation["explorer"],
        "task_types": participation["task_types"],
        "milestones": milestones,
        "total_days": total_days,
        "new_nodes": new_nodes,
    }


def register_ugc_post(con, thread_id, forum_id, author_id, display):
    """Register one tagged Forum thread as a scoring Halloween UGC entry."""
    _assert_activity_open()
    thread_id, author_id, day = str(thread_id), str(author_id), db.today_et()
    ensure_user(con, author_id, display)

    existing = con.execute(
        "SELECT * FROM ugc_posts WHERE thread_id=?", (thread_id,)
    ).fetchone()
    if existing:
        if existing["scoring_eligible"]:
            con.execute("UPDATE ugc_posts SET active=1 WHERE thread_id=?", (thread_id,))
            con.commit()
            return {"status": "reactivated", "points": 0, "first_haunt": 0, "explorer": 0, "new_nodes": []}
        return {"status": "ineligible", "reason": "This post was previously marked non-scoring."}

    total_entries = con.execute(
        "SELECT COUNT(*) FROM ugc_posts WHERE author_id=? AND scoring_eligible=1", (author_id,)
    ).fetchone()[0]
    today_entries = con.execute(
        "SELECT COUNT(*) FROM ugc_posts WHERE author_id=? AND day=? AND scoring_eligible=1",
        (author_id, day),
    ).fetchone()[0]

    if total_entries >= config.UGC_TOTAL_CAP:
        con.execute(
            "INSERT INTO ugc_posts(thread_id,forum_id,author_id,day,registered_at,active,scoring_eligible) "
            "VALUES(?,?,?,?,?,0,0)",
            (thread_id, str(forum_id), author_id, day, db.now_iso()),
        )
        con.commit()
        return {"status": "ineligible", "reason": f"You have reached the {config.UGC_TOTAL_CAP}-entry scoring cap."}

    if today_entries >= config.UGC_DAILY_CAP:
        con.execute(
            "INSERT INTO ugc_posts(thread_id,forum_id,author_id,day,registered_at,active,scoring_eligible) "
            "VALUES(?,?,?,?,?,0,0)",
            (thread_id, str(forum_id), author_id, day, db.now_iso()),
        )
        con.commit()
        return {"status": "ineligible", "reason": "Only one Halloween UGC entry can score per day."}

    con.execute(
        "INSERT INTO ugc_posts(thread_id,forum_id,author_id,day,registered_at,active,scoring_eligible) "
        "VALUES(?,?,?,?,?,1,1)",
        (thread_id, str(forum_id), author_id, day, db.now_iso()),
    )
    _insert(con, author_id, "ugc", config.PTS_UGC, day, ref=thread_id,
            community_points=config.COMM_UGC)
    participation = _record_participation(con, author_id, "ugc", day)
    new_nodes = _claim_new_nodes(con)
    con.commit()
    return {
        "status": "registered",
        "points": config.PTS_UGC,
        "first_haunt": participation["first_haunt"],
        "explorer": participation["explorer"],
        "task_types": participation["task_types"],
        "entry_no": total_entries + 1,
        "new_nodes": new_nodes,
    }


def set_ugc_vote_message(con, thread_id, message_id):
    con.execute(
        "UPDATE ugc_posts SET vote_message_id=? WHERE thread_id=?",
        (str(message_id), str(thread_id)),
    )
    con.commit()


def set_ugc_active(con, thread_id, active: bool):
    con.execute(
        "UPDATE ugc_posts SET active=? WHERE thread_id=? AND scoring_eligible=1",
        (1 if active else 0, str(thread_id)),
    )
    con.commit()


def get_ugc_post(con, thread_id):
    row = con.execute("SELECT * FROM ugc_posts WHERE thread_id=?", (str(thread_id),)).fetchone()
    return dict(row) if row else None


def ugc_vote_count(con, thread_id):
    return con.execute(
        "SELECT COUNT(*) FROM ugc_votes WHERE thread_id=?", (str(thread_id),)
    ).fetchone()[0]


def cast_vote(con, thread_id, voter_id, display):
    """Cast a unique vote on one registered Forum entry.

    Everyone may vote on unlimited different posts, but only the first
    VOTE_DAILY_SCORE_CAP votes per day earn personal/community points.
    """
    _assert_activity_open()
    thread_id, voter_id, day = str(thread_id), str(voter_id), db.today_et()
    post = con.execute("SELECT * FROM ugc_posts WHERE thread_id=?", (thread_id,)).fetchone()
    if not post or not post["scoring_eligible"]:
        raise InvalidVote("This post is not a registered Halloween entry.")
    if not post["active"]:
        raise InvalidVote("This Halloween entry is no longer active.")
    if post["author_id"] == voter_id:
        raise InvalidVote("You can’t vote for your own entry 👻")

    ensure_user(con, voter_id, display)
    scored_today = con.execute(
        "SELECT COUNT(*) FROM ugc_votes WHERE voter_id=? AND day=? AND scored=1",
        (voter_id, day),
    ).fetchone()[0]
    scored = int(scored_today < config.VOTE_DAILY_SCORE_CAP)

    try:
        con.execute(
            "INSERT INTO ugc_votes(thread_id,voter_id,day,scored,voted_at) VALUES(?,?,?,?,?)",
            (thread_id, voter_id, day, scored, db.now_iso()),
        )
    except sqlite3.IntegrityError:
        raise AlreadyDone("You already voted for this entry.")

    points = 0
    if scored:
        points = config.PTS_VOTE
        _insert(con, voter_id, "vote", points, day, ref=thread_id,
                community_points=config.COMM_VOTE)
    participation = _record_participation(con, voter_id, "vote", day)

    vote_count = ugc_vote_count(con, thread_id)
    author_bonuses = []
    for threshold, bonus in sorted(config.UGC_VOTE_BONUSES.items()):
        if vote_count >= threshold:
            ref = f"{thread_id}:{threshold}"
            try:
                _insert(con, post["author_id"], "ugc_vote_bonus", bonus, day, ref=ref)
                author_bonuses.append((threshold, bonus))
            except sqlite3.IntegrityError:
                pass

    new_nodes = _claim_new_nodes(con)
    con.commit()
    return {
        "points": points,
        "first_haunt": participation["first_haunt"],
        "explorer": participation["explorer"],
        "task_types": participation["task_types"],
        "scored": bool(scored),
        "vote_count": vote_count,
        "author_id": post["author_id"],
        "author_bonuses": author_bonuses,
        "new_nodes": new_nodes,
    }


def answer_quiz(con, user_id, display, quiz_id, choice):
    # Quiz availability is controlled by its own 24h window, not campaign dates.
    user_id, day = str(user_id), db.today_et()
    posted = con.execute("SELECT * FROM quiz_posted WHERE quiz_id=?", (quiz_id,)).fetchone()
    if not posted or posted["revealed_at"]:
        raise QuizClosed("This quiz is closed.")
    if posted["closes_at"] and db.now_dt() >= __import__("datetime").datetime.fromisoformat(posted["closes_at"]):
        raise QuizClosed("This quiz is closed.")

    ensure_user(con, user_id, display)
    correct_choice = posted["correct_choice"]
    is_correct = int(choice == correct_choice)
    try:
        con.execute(
            "INSERT INTO quiz_answers(quiz_id,user_id,choice,correct,answered_at) VALUES(?,?,?,?,?)",
            (quiz_id, user_id, choice, is_correct, db.now_iso()),
        )
    except sqlite3.IntegrityError:
        raise AlreadyDone("You already answered this question.")

    points = config.PTS_QUIZ if is_correct else 0
    if points:
        _insert(con, user_id, "quiz", points, day, ref=quiz_id,
                community_points=config.COMM_QUIZ)
    participation = _record_participation(con, user_id, "quiz", day)
    new_nodes = _claim_new_nodes(con)
    con.commit()
    return {
        "correct": bool(is_correct),
        "points": points,
        "first_haunt": participation["first_haunt"],
        "explorer": participation["explorer"],
        "task_types": participation["task_types"],
        "new_nodes": new_nodes,
    }


# ---------- queries ----------

def personal(con, user_id):
    user_id = str(user_id)
    points = con.execute(
        "SELECT COALESCE(SUM(points),0) FROM events WHERE user_id=? AND counts_personal=1", (user_id,)
    ).fetchone()[0]
    checkin_days = con.execute(
        "SELECT COUNT(*) FROM events WHERE user_id=? AND kind='checkin'", (user_id,)
    ).fetchone()[0]
    breakdown = {r["kind"]: r["s"] for r in con.execute(
        "SELECT kind, SUM(points) AS s FROM events WHERE user_id=? AND counts_personal=1 GROUP BY kind", (user_id,)
    )}
    next_milestone = next(
        ((th, bonus) for th, bonus in sorted(config.MILESTONES.items()) if th > checkin_days), None
    )
    ugc_entries = con.execute(
        "SELECT COUNT(*) FROM ugc_posts WHERE author_id=? AND scoring_eligible=1", (user_id,)
    ).fetchone()[0]
    votes_cast = con.execute(
        "SELECT COUNT(*) FROM ugc_votes WHERE voter_id=?", (user_id,)
    ).fetchone()[0]
    votes_received = con.execute(
        "SELECT COUNT(*) FROM ugc_votes v JOIN ugc_posts p ON p.thread_id=v.thread_id "
        "WHERE p.author_id=? AND p.scoring_eligible=1", (user_id,)
    ).fetchone()[0]
    quiz_answers = con.execute(
        "SELECT COUNT(*) FROM quiz_answers WHERE user_id=?", (user_id,)
    ).fetchone()[0]
    task_types = con.execute(
        "SELECT COUNT(*) FROM events WHERE user_id=? AND kind='participation'", (user_id,)
    ).fetchone()[0]
    explorer_unlocked = bool(con.execute(
        "SELECT 1 FROM events WHERE user_id=? AND kind='explorer' LIMIT 1", (user_id,)
    ).fetchone())
    return {
        "points": points,
        "checkin_days": checkin_days,
        "breakdown": breakdown,
        "next_milestone": next_milestone,
        "ugc_entries": ugc_entries,
        "votes_cast": votes_cast,
        "votes_received": votes_received,
        "quiz_answers": quiz_answers,
        "task_types": task_types,
        "explorer_unlocked": explorer_unlocked,
    }


def rank_of(con, user_id):
    user_id = str(user_id)
    rows = leaderboard(con, limit=None)
    for i, row in enumerate(rows, 1):
        if row["user_id"] == user_id:
            return i, len(rows)
    return None, len(rows)


def leaderboard(con, limit=config.LEADERBOARD_SIZE):
    sql = """
      SELECT e.user_id, u.display, SUM(e.points) AS pts, MAX(e.created_at) AS last_at
      FROM events e LEFT JOIN users u ON u.user_id=e.user_id
      WHERE e.counts_personal=1
      GROUP BY e.user_id
      HAVING pts > 0
      ORDER BY pts DESC, last_at ASC
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [dict(r) for r in con.execute(sql)]


def server_progress(con):
    total = con.execute("SELECT COALESCE(SUM(community_points),0) FROM events").fetchone()[0]
    pct = (total / config.PROGRESS_TOTAL) if config.PROGRESS_TOTAL else 0
    cleared = None
    for idx, (threshold, name) in enumerate(config.NODES):
        if pct >= threshold:
            cleared = (idx, threshold, name)
    next_node = next(((t, n) for t, n in config.NODES if pct < t), None)
    participants = con.execute(
        "SELECT COUNT(DISTINCT user_id) FROM events WHERE community_points > 0"
    ).fetchone()[0]
    return {
        "total": total,
        "pct": pct,
        "cleared_node": cleared[0] if cleared else None,
        "cleared_name": cleared[2] if cleared else None,
        "next_node": next_node,
        "participants": participants,
    }

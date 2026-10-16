"""ISLANDS Halloween Discord Bot.

User-facing commands:
  /checkin       Daily check-in
  /leaderboard   Private personal progress
  /progress      Public Top 10 + community progress

Admin command:
  /postquiz      Create a custom quiz with preview + publish flow

Automatic flows:
  - Halloween Forum post registration via tag
  - Vote button + unique voting + voter daily scoring cap
  - UGC vote-received bonus milestones
  - Community milestone announcements
  - Quiz closes/reveals automatically after 24h
"""
from __future__ import annotations

import asyncio
import datetime
import json
import os
import uuid

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

# Load .env before importing local modules that read environment variables.
load_dotenv()

import config
import db
import notifications
import scoring
from views import QuizView, UGCVoteView

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID") or 0)
CH_MAIN = int(os.getenv("CHANNEL_MAIN") or 0)
CH_UGC = int(os.getenv("CHANNEL_UGC") or 0)  # Existing collectible-sharing Forum channel
CH_PROGRESS = int(os.getenv("CHANNEL_PROGRESS") or 0)
ADMIN_ROLE_ID = int(os.getenv("ADMIN_ROLE_ID") or 0)
UGC_TAG_ID = int(os.getenv("UGC_TAG_ID") or 0)
UGC_TAG_NAME = (os.getenv("UGC_TAG_NAME") or "Hollywood").strip()

# No Message Content or Server Members privileged intent required.
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!hh ", intents=intents)
_views_registered = False


def load_questions() -> list[dict]:
    with open("questions.json", encoding="utf-8") as f:
        return json.load(f).get("questions", [])


def question_by_id(question_id: str) -> dict | None:
    question_id = question_id.lower().strip()
    return next((q for q in load_questions() if str(q.get("id", "")).lower() == question_id), None)


def bar(pct: float, width: int = 14) -> str:
    return notifications.progress_bar(pct, width)


def progress_line(con):
    sp = scoring.server_progress(con)
    shown = min(sp["pct"], 1.0)
    return f"📊 Community Progress: **{shown:.0%}** {bar(shown)}", sp


def is_admin():
    async def pred(inter: discord.Interaction):
        perms = getattr(inter.user, "guild_permissions", None)
        if perms and perms.manage_guild:
            return True
        if ADMIN_ROLE_ID and isinstance(inter.user, discord.Member):
            return any(role.id == ADMIN_ROLE_ID for role in inter.user.roles)
        return False
    return app_commands.check(pred)


def _thread_has_halloween_tag(thread: discord.Thread) -> bool:
    if not CH_UGC or thread.parent_id != CH_UGC:
        return False
    tags = getattr(thread, "applied_tags", []) or []
    if UGC_TAG_ID:
        return any(tag.id == UGC_TAG_ID for tag in tags)
    return bool(
        UGC_TAG_NAME
        and any(tag.name.strip().casefold() == UGC_TAG_NAME.casefold() for tag in tags)
    )


async def _display_name_for_thread(thread: discord.Thread) -> str:
    if thread.owner:
        return thread.owner.display_name
    guild = thread.guild
    if guild:
        member = guild.get_member(thread.owner_id)
        if member:
            return member.display_name
        try:
            member = await guild.fetch_member(thread.owner_id)
            return member.display_name
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass
    return f"User {thread.owner_id}"


async def _edit_vote_card(thread: discord.Thread, post: dict, active: bool):
    message_id = post.get("vote_message_id")
    if not message_id:
        return False
    con = db.connect()
    count = scoring.ugc_vote_count(con, thread.id)
    con.close()
    try:
        msg = await thread.fetch_message(int(message_id))
        await msg.edit(view=UGCVoteView(count, disabled=not active))
        return True
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return False


async def _send_to_forum_thread(thread: discord.Thread, **kwargs):
    """Send after Discord has finished creating a Forum starter message.

    Discord can dispatch ``on_thread_create`` just before the author's starter
    message is ready. During that short window it rejects replies with error
    40058. Retry only that transient response; real permission errors still
    surface immediately.
    """
    retry_delays = (1, 2, 4, 8)
    for delay in retry_delays:
        try:
            return await thread.send(**kwargs)
        except discord.Forbidden as exc:
            if exc.code != 40058:
                raise
            await asyncio.sleep(delay)
    return await thread.send(**kwargs)


async def _create_vote_card(thread: discord.Thread, result: dict):
    description = (
        "This post is registered for the **ISLANDS Halloween UGC Challenge**.\n\n"
        "Vote for entries you like using the button below. "
        "One member can vote for each entry once."
    )
    embed = discord.Embed(
        title="🎃 Halloween Entry Registered!",
        description=description,
        color=0xFF6B00,
    )
    footer = f"+{result['points']} personal points"
    if result.get("first_haunt"):
        footer += f" · First Haunt +{result['first_haunt']}"
    if result.get("explorer"):
        footer += f" · Explorer +{result['explorer']}"
    footer += f" · Entry {result.get('entry_no', '?')}/{config.UGC_TOTAL_CAP}"
    embed.set_footer(text=footer)
    msg = await _send_to_forum_thread(thread, embed=embed, view=UGCVoteView(0))
    con = db.connect()
    scoring.set_ugc_vote_message(con, thread.id, msg.id)
    con.close()


async def process_forum_thread(thread: discord.Thread):
    """Keep one Forum thread in sync with Halloween-tag registration state."""
    if not CH_UGC or thread.parent_id != CH_UGC or not thread.owner_id:
        return

    tagged = _thread_has_halloween_tag(thread)
    con = db.connect()
    post = scoring.get_ugc_post(con, thread.id)
    con.close()

    if tagged:
        if post:
            if not post["scoring_eligible"]:
                return
            if not post["active"]:
                con = db.connect()
                scoring.set_ugc_active(con, thread.id, True)
                con.close()
                post["active"] = 1
            if not await _edit_vote_card(thread, post, active=True):
                # Recover from a crash/deleted bot card without scoring twice.
                con = db.connect()
                count = scoring.ugc_vote_count(con, thread.id)
                con.close()
                embed = discord.Embed(
                    title="🎃 Halloween Entry",
                    description="Vote for this Halloween entry below.",
                    color=0xFF6B00,
                )
                msg = await _send_to_forum_thread(
                    thread, embed=embed, view=UGCVoteView(count)
                )
                con = db.connect()
                scoring.set_ugc_vote_message(con, thread.id, msg.id)
                con.close()
            return

        display = await _display_name_for_thread(thread)
        con = db.connect()
        try:
            result = scoring.register_ugc_post(
                con, thread.id, thread.parent_id, thread.owner_id, display
            )
        except scoring.ActivityClosed:
            con.close()
            return
        con.close()

        if result["status"] == "registered":
            await _create_vote_card(thread, result)
            await notifications.announce_nodes(bot, result["new_nodes"], fallback_channel=thread)
        elif result["status"] == "ineligible":
            # One explanatory message only: the thread is recorded as non-scoring.
            try:
                await _send_to_forum_thread(
                    thread,
                    content=(
                        f"🎃 Halloween tag detected, but this post is **not a scoring entry**.\n"
                        f"Reason: {result['reason']}"
                    ),
                )
            except discord.HTTPException:
                pass
        return

    # Tag removed: stop future voting, but do not retroactively erase earned points/votes.
    if post and post["scoring_eligible"] and post["active"]:
        con = db.connect()
        scoring.set_ugc_active(con, thread.id, False)
        con.close()
        post["active"] = 0
        await _edit_vote_card(thread, post, active=False)


# ---------------- User commands ----------------

@bot.tree.command(description="Daily Halloween check-in")
@app_commands.describe(message="Optional — leave a short message for the community 🎃")
async def checkin(inter: discord.Interaction, message: str | None = None):
    con = db.connect()
    try:
        result = scoring.checkin(con, inter.user.id, inter.user.display_name)
    except scoring.AlreadyDone:
        con.close()
        await inter.response.send_message(
            "🎃 You've already checked in today. Come back tomorrow!", ephemeral=True
        )
        return
    except scoring.ActivityClosed:
        con.close()
        await inter.response.send_message(
            "⏰ Halloween check-in is not open right now.", ephemeral=True
        )
        return

    pline, _ = progress_line(con)
    con.close()
    lines = [
        f"🎃 **{inter.user.display_name}** checked in! · Day {result['total_days']} · +{result['points']}"
    ]
    if message:
        lines.append(f"> 💬 {discord.utils.escape_markdown(message[:200])}")
    if result["first_haunt"]:
        lines.append(f"👻 **Welcome to the Haunted House!** First Haunt Bonus: **+{result['first_haunt']}**")
    if result["explorer"]:
        lines.append(f"🎃 **Halloween Explorer unlocked!** **+{result['explorer']}**")
    for threshold, bonus in result["milestones"]:
        lines.append(f"🏆 **{threshold}-day milestone** reached: **+{bonus}**!")
    lines.append(pline)
    await inter.response.send_message("\n".join(lines))
    await notifications.announce_nodes(bot, result["new_nodes"], fallback_channel=inter.channel)


@bot.tree.command(description="View your personal Halloween progress")
async def leaderboard(inter: discord.Interaction):
    con = db.connect()
    personal = scoring.personal(con, inter.user.id)
    rank, total_players = scoring.rank_of(con, inter.user.id)
    con.close()

    rank_text = f"#{rank} / {total_players}" if rank else "Not ranked yet"
    mine = (
        f"**Rank:** {rank_text}\n"
        f"**Points:** {personal['points']}\n"
        f"**Check-ins:** {personal['checkin_days']}\n"
        f"**Quiz answered:** {personal['quiz_answers']}\n"
        f"**UGC entries:** {personal['ugc_entries']}\n"
        f"**Votes received:** {personal['votes_received']} ❤️\n"
        f"**Votes cast:** {personal['votes_cast']}\n"
        f"**Mission types explored:** {personal['task_types']}/4"
    )
    if personal["explorer_unlocked"]:
        mine += "\n**Halloween Explorer:** ✅ Unlocked"
    elif personal["task_types"] < config.EXPLORER_TASK_TYPES:
        mine += (
            f"\n**Halloween Explorer:** Complete "
            f"{config.EXPLORER_TASK_TYPES - personal['task_types']} more mission type(s) "
            f"→ +{config.PTS_EXPLORER} pts"
        )
    if personal["next_milestone"]:
        threshold, bonus = personal["next_milestone"]
        mine += f"\n**Next check-in milestone:** {threshold} days → +{bonus} pts"

    embed = discord.Embed(
        title="👻 Your Halloween Progress",
        description=mine,
        color=0xFF6B00,
    )
    embed.set_footer(text="Only you can see this panel.")
    await inter.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(description="Public Top 10 + community progress")
async def progress(inter: discord.Interaction):
    con = db.connect()
    rows = scoring.leaderboard(con, config.LEADERBOARD_SIZE)
    community = scoring.server_progress(con)
    con.close()

    if rows:
        medals = ["🥇", "🥈", "🥉"] + [f"`{i}.`" for i in range(4, 11)]
        top = "\n".join(
            f"{medals[i]} **{row['display'] or row['user_id']}** — {row['pts']} pts"
            for i, row in enumerate(rows)
        )
    else:
        top = "No scores yet — be the first to `/checkin`!"

    shown = min(community["pct"], 1.0)
    community_text = (
        f"**{shown:.0%}** {bar(shown)}\n"
        f"Community points: **{community['total']} / {config.PROGRESS_TOTAL}**\n"
        f"Hunters joined: **{community['participants']}**"
    )
    if community["next_node"]:
        threshold, name = community["next_node"]
        community_text += f"\nNext unlock: **{name}** at {threshold:.0%}"
    else:
        community_text += "\n🏆 All community rewards unlocked!"

    embed = discord.Embed(
        title="🎃 ISLANDS Halloween Progress",
        description=top,
        color=0xFF6B00,
    )
    embed.add_field(name="🏚️ Community Progress", value=community_text, inline=False)
    embed.set_footer(text="Top 10 is public · Ties are broken by who reached the score first.")
    await inter.response.send_message(embed=embed)


# ---------------- Admin quiz command ----------------

async def _post_quiz(channel: discord.abc.Messageable, q: dict):
    options = q.get("options", {})
    opts = "\n".join(f"**{k}** · {v}" for k, v in options.items())
    embed = discord.Embed(
        title="🎃 Question of the Day",
        description=f"{q['text']}\n\n{opts}",
        color=0xFF6B00,
    )
    embed.set_footer(text=f"Your choice is private · Open for {config.QUIZ_OPEN_HOURS} hours")
    msg = await channel.send(embed=embed, view=QuizView(q["id"]))

    posted_at = db.now_dt()
    closes_at = posted_at + datetime.timedelta(hours=config.QUIZ_OPEN_HOURS)
    con = db.connect()
    con.execute(
        "INSERT INTO quiz_posted(quiz_id,message_id,channel_id,posted_at,closes_at,revealed_at,"
        "correct_choice,question_text,options_json) VALUES(?,?,?,?,?,NULL,?,?,?)",
        (
            q["id"], str(msg.id), str(msg.channel.id), posted_at.isoformat(timespec="seconds"),
            closes_at.isoformat(timespec="seconds"), q["correct"], q["text"],
            json.dumps(options, ensure_ascii=False),
        ),
    )
    con.commit()
    con.close()
    return msg


def _quiz_preview_embed(draft: dict) -> discord.Embed:
    options = draft["options"]
    opts = "\n".join(f"**{letter}** · {options[letter]}" for letter in ("A", "B", "C", "D"))
    embed = discord.Embed(
        title="🎃 Quiz Preview",
        description=f"{draft['text']}\n\n{opts}",
        color=0xFF6B00,
    )
    correct = draft.get("correct")
    if correct:
        embed.add_field(
            name="✅ Correct Answer",
            value=f"**{correct} · {options[correct]}**",
            inline=False,
        )
    else:
        embed.add_field(
            name="Correct Answer",
            value="Choose **A / B / C / D** below before publishing.",
            inline=False,
        )
    embed.set_footer(text="Private preview · Edit, choose the correct answer, then publish")
    return embed


class QuizDraftModal(discord.ui.Modal):
    def __init__(
        self,
        *,
        channel_id: int,
        owner_id: int,
        draft: dict | None = None,
        preview_message: discord.WebhookMessage | None = None,
    ):
        super().__init__(title="Create Halloween Quiz", timeout=600)
        self.channel_id = channel_id
        self.owner_id = owner_id
        self.draft = draft or {}
        self.preview_message = preview_message

        old_options = self.draft.get("options", {})
        self.question = discord.ui.TextInput(
            label="Question",
            style=discord.TextStyle.paragraph,
            max_length=300,
            default=self.draft.get("text", ""),
            placeholder="e.g. Which IP is #2 on today's US Price Drops ranking?",
        )
        self.option_a = discord.ui.TextInput(label="Option A", max_length=100, default=old_options.get("A", ""))
        self.option_b = discord.ui.TextInput(label="Option B", max_length=100, default=old_options.get("B", ""))
        self.option_c = discord.ui.TextInput(label="Option C", max_length=100, default=old_options.get("C", ""))
        self.option_d = discord.ui.TextInput(label="Option D", max_length=100, default=old_options.get("D", ""))
        for item in (self.question, self.option_a, self.option_b, self.option_c, self.option_d):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("⚠️ This quiz draft belongs to another admin.", ephemeral=True)
            return

        draft = {
            "text": str(self.question.value).strip(),
            "options": {
                "A": str(self.option_a.value).strip(),
                "B": str(self.option_b.value).strip(),
                "C": str(self.option_c.value).strip(),
                "D": str(self.option_d.value).strip(),
            },
            # Keep the selected letter when editing the wording/options.
            "correct": self.draft.get("correct"),
        }
        view = QuizPreviewView(
            draft=draft,
            channel_id=self.channel_id,
            owner_id=self.owner_id,
        )
        embed = _quiz_preview_embed(draft)

        if self.preview_message:
            await interaction.response.defer(ephemeral=True)
            await self.preview_message.edit(embed=embed, view=view)
            view.preview_message = self.preview_message
        else:
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            view.preview_message = await interaction.original_response()


class CorrectAnswerButton(discord.ui.Button):
    def __init__(self, letter: str, selected: bool = False):
        super().__init__(
            label=letter,
            style=discord.ButtonStyle.success if selected else discord.ButtonStyle.secondary,
            custom_id=f"quiz_preview_correct_{letter}",
            row=0,
        )
        self.letter = letter

    async def callback(self, interaction: discord.Interaction):
        view: QuizPreviewView = self.view
        if interaction.user.id != view.owner_id:
            await interaction.response.send_message("⚠️ This quiz draft belongs to another admin.", ephemeral=True)
            return
        view.draft["correct"] = self.letter
        view.refresh_answer_buttons()
        await interaction.response.edit_message(embed=_quiz_preview_embed(view.draft), view=view)


class QuizPreviewView(discord.ui.View):
    def __init__(self, *, draft: dict, channel_id: int, owner_id: int):
        super().__init__(timeout=600)
        self.draft = draft
        self.channel_id = channel_id
        self.owner_id = owner_id
        self.preview_message: discord.WebhookMessage | None = None
        self.refresh_answer_buttons()

    def refresh_answer_buttons(self):
        # Preserve action buttons while rebuilding the A/B/C/D row.
        for item in list(self.children):
            if isinstance(item, CorrectAnswerButton):
                self.remove_item(item)
        selected = self.draft.get("correct")
        for letter in ("A", "B", "C", "D"):
            self.add_item(CorrectAnswerButton(letter, selected=(letter == selected)))

    @discord.ui.button(label="Edit", style=discord.ButtonStyle.secondary, emoji="✏️", row=1)
    async def edit_quiz(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("⚠️ This quiz draft belongs to another admin.", ephemeral=True)
            return
        await interaction.response.send_modal(
            QuizDraftModal(
                channel_id=self.channel_id,
                owner_id=self.owner_id,
                draft=self.draft,
                preview_message=self.preview_message,
            )
        )

    @discord.ui.button(label="Publish Quiz", style=discord.ButtonStyle.primary, emoji="🚀", row=1)
    async def publish_quiz(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("⚠️ This quiz draft belongs to another admin.", ephemeral=True)
            return
        if not self.draft.get("correct"):
            await interaction.response.send_message(
                "⚠️ Choose the correct answer (A/B/C/D) before publishing.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        channel = interaction.client.get_channel(self.channel_id)
        if channel is None:
            try:
                channel = await interaction.client.fetch_channel(self.channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                channel = None
        if channel is None or not hasattr(channel, "send"):
            await interaction.followup.send("⚠️ I can't access the target channel.", ephemeral=True)
            return

        quiz_id = f"custom-{db.now_dt().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        q = {
            "id": quiz_id,
            "text": self.draft["text"],
            "options": self.draft["options"],
            "correct": self.draft["correct"],
        }
        try:
            msg = await _post_quiz(channel, q)
        except Exception as exc:
            await interaction.followup.send(f"⚠️ Failed to publish quiz: `{type(exc).__name__}`", ephemeral=True)
            raise

        published = discord.Embed(
            title="✅ Quiz Published",
            description=(
                f"{self.draft['text']}\n\n"
                f"Correct answer: **{self.draft['correct']} · "
                f"{self.draft['options'][self.draft['correct']]}**\n\n"
                f"It will close and reveal automatically in **{config.QUIZ_OPEN_HOURS} hours**."
            ),
            color=0x00C853,
        )
        published.add_field(name="Published message", value=f"[Jump to quiz]({msg.jump_url})", inline=False)
        if self.preview_message:
            await self.preview_message.edit(embed=published, view=None)
        else:
            await interaction.followup.send(embed=published, ephemeral=True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, row=1)
    async def cancel_quiz(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("⚠️ This quiz draft belongs to another admin.", ephemeral=True)
            return
        cancelled = discord.Embed(
            title="Quiz Draft Cancelled",
            description="Nothing was published.",
            color=0x777777,
        )
        await interaction.response.edit_message(embed=cancelled, view=None)


@bot.tree.command(description="[Admin] Create a custom quiz")
@is_admin()
async def postquiz(inter: discord.Interaction):
    if not inter.channel_id:
        await inter.response.send_message("⚠️ Run this command inside the channel where you want the quiz posted.", ephemeral=True)
        return
    await inter.response.send_modal(
        QuizDraftModal(channel_id=inter.channel_id, owner_id=inter.user.id)
    )


# ---------------- Forum events ----------------

@bot.event
async def on_thread_create(thread: discord.Thread):
    await process_forum_thread(thread)


@bot.event
async def on_thread_update(before: discord.Thread, after: discord.Thread):
    if after.parent_id == CH_UGC:
        await process_forum_thread(after)


@bot.event
async def on_thread_delete(thread: discord.Thread):
    if thread.parent_id != CH_UGC:
        return
    con = db.connect()
    scoring.set_ugc_active(con, thread.id, False)
    con.close()


# ---------------- Automatic quiz reveal ----------------

@tasks.loop(minutes=5)
async def reveal_closed_quizzes():
    now = db.now_dt()
    con = db.connect()
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM quiz_posted WHERE revealed_at IS NULL AND closes_at IS NOT NULL"
    )]
    con.close()

    for row in rows:
        try:
            closes_at = datetime.datetime.fromisoformat(row["closes_at"])
        except (TypeError, ValueError):
            continue
        if now < closes_at:
            continue

        # Lock it first so no answer can slip in while we reveal.
        con = db.connect()
        current = con.execute(
            "SELECT revealed_at FROM quiz_posted WHERE quiz_id=?", (row["quiz_id"],)
        ).fetchone()
        if not current or current["revealed_at"]:
            con.close()
            continue
        con.execute(
            "UPDATE quiz_posted SET revealed_at=? WHERE quiz_id=?",
            (db.now_iso(), row["quiz_id"]),
        )
        answers = con.execute(
            "SELECT correct FROM quiz_answers WHERE quiz_id=?", (row["quiz_id"],)
        ).fetchall()
        con.commit()
        con.close()

        total = len(answers)
        right = sum(r[0] for r in answers)
        accuracy = f"{right}/{total} ({right/total:.0%})" if total else "No answers"
        options = json.loads(row["options_json"] or "{}")
        correct = row["correct_choice"]
        correct_text = options.get(correct, "")

        channel = bot.get_channel(int(row["channel_id"])) if row["channel_id"] else None
        if not channel and row["channel_id"]:
            try:
                channel = await bot.fetch_channel(int(row["channel_id"]))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                channel = None
        if not channel:
            continue

        if row["message_id"]:
            try:
                original = await channel.fetch_message(int(row["message_id"]))
                await original.edit(view=QuizView(row["quiz_id"], disabled=True))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

        reveal = discord.Embed(
            title="✅ Answer Revealed",
            description=(
                f"{row['question_text']}\n\n"
                f"**Correct answer: {correct} · {correct_text}**\n\n"
                f"Community accuracy: {accuracy}"
            ),
            color=0x00C853,
        )
        try:
            await channel.send(embed=reveal)
        except discord.HTTPException:
            pass


@bot.event
async def on_ready():
    global _views_registered
    con = db.init()

    if not _views_registered:
        # One global persistent UGC vote handler works for every Forum thread.
        bot.add_view(UGCVoteView())
        for row in con.execute(
            "SELECT quiz_id,message_id FROM quiz_posted WHERE revealed_at IS NULL"
        ):
            bot.add_view(
                QuizView(row["quiz_id"]),
                message_id=int(row["message_id"]) if row["message_id"] else None,
            )
        _views_registered = True
    con.close()

    if GUILD_ID:
        guild_obj = discord.Object(id=GUILD_ID)
        bot.tree.copy_global_to(guild=guild_obj)
        await bot.tree.sync(guild=guild_obj)

    if not reveal_closed_quizzes.is_running():
        reveal_closed_quizzes.start()

    # Recover/register active Halloween-tagged Forum posts after a restart.
    if CH_UGC:
        forum = bot.get_channel(CH_UGC)
        if isinstance(forum, discord.ForumChannel):
            if UGC_TAG_ID:
                matching_tag = next(
                    (tag for tag in forum.available_tags if tag.id == UGC_TAG_ID),
                    None,
                )
            else:
                matching_tag = next(
                    (
                        tag for tag in forum.available_tags
                        if UGC_TAG_NAME
                        and tag.name.strip().casefold() == UGC_TAG_NAME.casefold()
                    ),
                    None,
                )
            if matching_tag:
                print(f"🏷️ UGC Forum tag matched | Name={matching_tag.name} | ID={matching_tag.id}")
            else:
                print(
                    f"⚠️ UGC Forum tag not found | Expected ID={UGC_TAG_ID or 'not set'} "
                    f"| Name={UGC_TAG_NAME or 'not set'}"
                )
            for thread in forum.threads:
                await process_forum_thread(thread)

    date_mode = (
        f"{config.START_DATE or 'open'} → {config.END_DATE or 'open'}"
        if config.START_DATE or config.END_DATE else "test/open mode"
    )
    print(f"✅ {bot.user} online | {date_mode} | UGC Forum={CH_UGC or 'not set'} | Tag={UGC_TAG_NAME}")


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("❌ DISCORD_TOKEN is not set. Copy .env.example to .env and fill it locally.")
    bot.run(TOKEN)

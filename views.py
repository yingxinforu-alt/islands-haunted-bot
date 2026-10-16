"""Persistent Discord UI components for quiz answers and UGC voting."""
from __future__ import annotations

import discord

import config
import db
import notifications
import scoring


class QuizView(discord.ui.View):
    def __init__(self, quiz_id: str, disabled: bool = False):
        super().__init__(timeout=None)
        self.quiz_id = quiz_id
        for letter in ("A", "B", "C", "D"):
            self.add_item(QuizButton(letter, quiz_id, disabled=disabled))


class QuizButton(discord.ui.Button):
    def __init__(self, letter: str, quiz_id: str, disabled: bool = False):
        super().__init__(
            label=letter,
            style=discord.ButtonStyle.secondary,
            custom_id=f"quiz:{quiz_id}:{letter}",
            disabled=disabled,
        )
        self.letter = letter

    async def callback(self, interaction: discord.Interaction):
        view: QuizView = self.view
        con = db.connect()
        try:
            result = scoring.answer_quiz(
                con, interaction.user.id, interaction.user.display_name,
                view.quiz_id, self.letter,
            )
        except scoring.AlreadyDone:
            await interaction.response.send_message(
                "⚠️ You've already answered this question.", ephemeral=True
            )
            con.close()
            return
        except (scoring.QuizClosed, scoring.ActivityClosed):
            await interaction.response.send_message(
                "⏰ This quiz is closed.", ephemeral=True
            )
            con.close()
            return
        con.close()

        msg = f"✅ Locked in: **{self.letter}**\nYour choice stays private until the answer is revealed."
        if result["first_haunt"]:
            msg += f"\n👻 **Welcome to the Haunted House!** First Haunt Bonus: **+{result['first_haunt']}**"
        if result["explorer"]:
            msg += f"\n🎃 **Halloween Explorer unlocked!** **+{result['explorer']}**"
        await interaction.response.send_message(msg, ephemeral=True)
        await notifications.announce_nodes(
            interaction.client, result["new_nodes"], fallback_channel=interaction.channel
        )


class UGCVoteView(discord.ui.View):
    def __init__(self, vote_count: int | None = None, disabled: bool = False):
        super().__init__(timeout=None)
        label = "Vote 🎃" if vote_count is None else f"Vote 🎃 · {vote_count}"
        self.add_item(UGCVoteButton(label=label, disabled=disabled))


class UGCVoteButton(discord.ui.Button):
    def __init__(self, label="Vote 🎃", disabled=False):
        super().__init__(
            label=label,
            style=discord.ButtonStyle.primary,
            custom_id="halloween_ugc_vote",
            disabled=disabled,
        )

    async def callback(self, interaction: discord.Interaction):
        # The button lives inside the Forum thread; channel_id is the UGC thread_id.
        thread_id = interaction.channel_id
        con = db.connect()
        try:
            result = scoring.cast_vote(
                con, thread_id, interaction.user.id, interaction.user.display_name
            )
        except scoring.AlreadyDone:
            await interaction.response.send_message(
                "🗳️ You've already voted for this entry.", ephemeral=True
            )
            con.close()
            return
        except scoring.InvalidVote as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
            con.close()
            return
        except scoring.ActivityClosed:
            await interaction.response.send_message(
                "⏰ Halloween voting is not open right now.", ephemeral=True
            )
            con.close()
            return
        con.close()

        if result["scored"]:
            msg = f"🗳️ Vote counted! **+{result['points']} point**"
            if result["first_haunt"]:
                msg += f"\n👻 **Welcome to the Haunted House!** First Haunt Bonus: **+{result['first_haunt']}**"
            if result["explorer"]:
                msg += f"\n🎃 **Halloween Explorer unlocked!** **+{result['explorer']}**"
        else:
            msg = (
                "🗳️ Vote counted! You've already earned points from your first "
                f"{config.VOTE_DAILY_SCORE_CAP} votes today, so this vote doesn't add more vote points."
            )
            if result["first_haunt"]:
                msg += f"\n👻 **Welcome to the Haunted House!** First Haunt Bonus: **+{result['first_haunt']}**"
            if result["explorer"]:
                msg += f"\n🎃 **Halloween Explorer unlocked!** **+{result['explorer']}**"
        await interaction.response.send_message(msg, ephemeral=True)

        if interaction.message:
            try:
                await interaction.message.edit(view=UGCVoteView(result["vote_count"]))
            except discord.HTTPException:
                pass

        for threshold, bonus in result["author_bonuses"]:
            try:
                await interaction.channel.send(
                    f"🎉 **{threshold} votes reached!** <@{result['author_id']}> earned "
                    f"**+{bonus} bonus points** for this Halloween entry."
                )
            except discord.HTTPException:
                pass

        await notifications.announce_nodes(
            interaction.client, result["new_nodes"], fallback_channel=interaction.channel
        )

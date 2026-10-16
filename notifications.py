"""Shared Discord notifications used by slash commands and button callbacks."""
from __future__ import annotations

import os

import discord

import config

CHANNEL_PROGRESS = int(os.getenv("CHANNEL_PROGRESS") or 0)
CHANNEL_MAIN = int(os.getenv("CHANNEL_MAIN") or 0)


def progress_bar(pct: float, width: int = 14) -> str:
    pct = max(0.0, min(pct, 1.0))
    filled = int(round(pct * width))
    return "▓" * filled + "░" * (width - filled)


async def announce_nodes(client: discord.Client, new_nodes, fallback_channel=None):
    if not new_nodes:
        return
    channel = None
    for channel_id in (CHANNEL_PROGRESS, CHANNEL_MAIN):
        if channel_id:
            channel = client.get_channel(channel_id)
            if channel:
                break
    channel = channel or fallback_channel
    if not channel:
        return
    for _, threshold, name in new_nodes:
        embed = discord.Embed(
            title=f"🎃 Community Milestone Unlocked — {threshold:.0%}",
            description=f"**{name}**\n\nThe whole community just unlocked a new Halloween reward!",
            color=0xFF6B00,
        )
        await channel.send(embed=embed)

# ISLANDS Halloween Discord Bot

## Current user experience

- `/checkin` — daily check-in.
- `/leaderboard` — private personal progress panel (only the caller can see it).
- `/progress` — public Top 10 + community progress panel.
- Admin `/postquiz` — opens a custom quiz form. After filling Question + A/B/C/D, the admin gets a private preview, selects the correct answer, then can Edit / Publish / Cancel. Published quizzes close and reveal automatically after 24 hours.
- UGC is **not** submitted with a slash command. Users post normally in the existing collectible-sharing **Forum** and select the configured Halloween tag.
- The bot detects that tag, registers the Forum thread as an event entry, posts a persistent **Vote 🎃** button inside the thread, and counts votes automatically.

## First participation / exploration

- **First Haunt Bonus:** the first valid activity of any type (check-in, quiz, UGC, or vote) gives **+2 personal points**, once per user.
- **Halloween Explorer:** completing **3 distinct activity types** gives **+3 personal points**, once per user.
- These bonuses do **not** add community progress.
- `/leaderboard` shows how many mission types the caller has explored.

## Custom quiz publishing

1. Run `/postquiz` in the channel where the quiz should appear.
2. Fill the modal: Question + Option A + Option B + Option C + Option D.
3. The bot shows a **private preview** visible only to the admin.
4. Choose the correct answer using A/B/C/D buttons.
5. Use **Edit**, **Publish Quiz**, or **Cancel**.
6. After publishing, users answer using A/B/C/D buttons. Each user can answer once; their choice stays private until the automatic reveal.

`questions.json` is kept only as a legacy/reference file and is no longer required for normal quiz publishing.

## UGC / vote rules implemented

- Only posts in `CHANNEL_UGC` with the Halloween tag are registered.
- 1 scoring UGC entry per user per day; 5 scoring entries per campaign by default.
- Each scoring UGC entry: +5 personal points, +8 community points.
- One member can vote for each entry once.
- Authors cannot vote for their own entry.
- Voters can vote on more than 3 entries, but only their first 3 votes per day score (+1 personal / +1 community each).
- Votes received give the author milestone bonuses per post:
  - 5 votes: +2
  - 10 votes: +3
  - 20 votes: +5
  - 30 votes: +5
- Removing the Halloween tag disables future voting. Existing earned points/votes are not retroactively removed.
- Adding the tag back reactivates the entry without awarding UGC points a second time.

## Community progress

Community progress uses separate weights instead of simply summing personal points:

- Check-in: +1 community
- Correct quiz answer: +2 community
- UGC: +8 community
- Scoring vote: +1 community

First Haunt, Halloween Explorer, check-in milestones, and vote-received bonuses affect personal ranking only.

The community target is **2,000 points**, with unlock nodes at 30% (600),
50% (1,000), and 100% (2,000).

## Dates

The campaign runs from **2026-10-16 through 2026-10-31, inclusive**.
Daily resets and activity availability use **Beijing time (`Asia/Shanghai`)**.

## Discord permissions

The bot does **not** require Message Content Intent or Server Members Intent.

Recommended server permissions:

- View Channels
- Send Messages
- Send Messages in Threads
- Embed Links
- Read Message History
- Use Application Commands

## Local setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Fill `.env` locally. Do not share the real bot token.

Then run:

```bash
python3 bot.py
```

## Railway deployment

This package includes a production `Dockerfile` and a step-by-step
`RAILWAY-DEPLOY.md`. Store the Discord token in Railway Variables and attach a
persistent Volume at `/app/data` so the SQLite scores survive redeployments.
The bot does not need a public domain.

## Forum setup

1. Keep using the existing collectible-sharing Forum channel.
2. Use the configured Forum tag `Hollywood`.
3. Put the Forum channel ID in `CHANNEL_UGC`.
4. The exact tag name is configured in `UGC_TAG_NAME`. On startup, the bot prints
   the matching tag ID to the console so it can later be copied into `UGC_TAG_ID`.
5. A user creates a normal Forum post and selects the Halloween tag.
6. The bot automatically posts the voting card inside that Forum thread.

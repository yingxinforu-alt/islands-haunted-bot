# Railway deployment

This Discord bot runs as one continuously running service. It does not need a
public domain or an inbound web port.

## 1. Upload the project

Put the contents of this folder in a private GitHub repository, then create a
Railway project using **Deploy from GitHub repo**. Railway will detect the
`Dockerfile` automatically.

Keep exactly one replica running. Multiple replicas would connect the same bot
twice and could process an interaction more than once.

## 2. Add Railway variables

Open the service's **Variables** tab and add the following. Replace only the
Discord token placeholder with the real existing token.

```env
DISCORD_TOKEN=put_the_existing_token_here
GUILD_ID=1468499818858151976
CHANNEL_MAIN=1560549120282726430
CHANNEL_UGC=1501426705439395881
CHANNEL_PROGRESS=1560549120282726430
UGC_TAG_NAME=Hollywood
ADMIN_ROLE_ID=1468547089884643400
DB_PATH=/app/data/haunted.db
```

Do not put the token in GitHub, the ZIP, `Dockerfile`, or `.env.example`.

`UGC_TAG_ID` can remain unset for the first deployment. The bot matches the
exact tag name `Hollywood` and prints the matching tag ID in the Railway logs.
Afterward, add the printed number as `UGC_TAG_ID` and redeploy.

## 3. Attach persistent storage

Create a Railway Volume for the bot service and set its mount path to:

```text
/app/data
```

The volume stores `haunted.db`. Without it, scores can disappear after a
redeploy or restart.

## 4. Service settings

- Start command: supplied by the Dockerfile (`python bot.py`).
- Replicas: `1`.
- Restart policy: `Always` on a paid plan; otherwise keep `On Failure`.
- Serverless/App Sleeping: disabled.
- Public networking/domain: not required.

## 5. Confirm a successful start

The Railway deployment logs should contain lines similar to:

```text
UGC Forum tag matched | Name=Hollywood | ID=...
<bot name> online | 2026-10-16 → 2026-10-31 | UGC Forum=1501426705439395881 | Tag=Hollywood
```

Then test `/checkin`, `/leaderboard`, `/progress`, and an admin `/postquiz` in
the production Discord server.

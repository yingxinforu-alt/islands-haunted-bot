# v4 changes

- `/leaderboard` is now private personal progress only.
- Added public `/progress` with Top 10 + community progress.
- Rebuilt `/postquiz` as a custom admin flow:
  - `/postquiz` opens a 5-field modal (Question + A/B/C/D).
  - Private preview before publishing.
  - A/B/C/D buttons select the correct answer.
  - Edit / Publish Quiz / Cancel actions.
  - Published quizzes still close and reveal automatically after 24 hours.
- `questions.json` is no longer required for normal quiz publishing (kept as legacy/reference).

## 2026 production configuration

- Campaign dates: 2026-10-16 through 2026-10-31, inclusive.
- Daily reset timezone: Beijing (`Asia/Shanghai`).
- Community target: 2,000 points (600 / 1,000 / 2,000 unlocks).
- Configured the production guild, Forum, announcement channel, progress channel,
  `Hollywood` Forum tag name, and `/postquiz` admin role.
- The bot prints the matching Forum tag ID at startup when the tag name is found.
- Added Railway deployment support with a Dockerfile, persistent SQLite volume
  instructions, and dashboard environment-variable setup.

# Substack Digests

## Purpose
Python script that fetches Substack newsletter content and generates daily AI-powered summaries, saved to file and pushed to GitHub.

## Architecture
- `substack_daily_summary.py` — main script
- `substack_summaries/` — output directory for generated digest files
- `tests/` — test suite

## Remote
- `https://github.com/pinkymon/substack-digests.git` (pinkymon's repo — push OK)

## Scheduled Automation
- Primary run: daily @ 9:30 AM via `~/.claude/scheduled-tasks/substack-daily-digest`
- Retry run: daily @ 11:30 AM via `~/.claude/scheduled-tasks/substack-daily-digest-retry`

## Key Commands
```bash
python substack_daily_summary.py   # run digest
python -m pytest tests/            # run tests
```

## Notes
- Uses `.env` for any API keys — never commit secrets
- Output files committed and pushed to GitHub as part of the automation

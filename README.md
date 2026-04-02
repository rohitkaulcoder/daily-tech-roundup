# Daily Tech Roundup

Automated daily email digest of US tech industry news from 3 podcasts:

- **TBPN** — Technology's Daily Show
- **TITV** — The Information's TITV
- **Techmeme Ride Home**

## How it works

1. **Fetch transcripts** from podcast RSS feeds (Groq Whisper for audio transcription)
2. **Generate highlights** via Anthropic API (Claude Sonnet)
3. **Render email** using Jinja2 template
4. **Send via Resend** API

Runs on GitHub Actions, triggered at 7:00 AM IST (1:30 AM UTC) weekdays by an external cron-job.org scheduler via `workflow_dispatch`. The GitHub Actions built-in cron schedule has been removed to prevent duplicate runs.

## Setup

### GitHub Secrets

| Secret | Description |
|--------|-------------|
| `ANTHROPIC_API_KEY` | Anthropic API key (console.anthropic.com) |
| `RESEND_API_KEY` | Resend API key (resend.com) |
| `GROQ_API_KEY` | Groq API key for Whisper transcription |
| `YOUTUBE_API_KEY` | YouTube Data API key (fallback transcripts) |
| `RECIPIENT_EMAIL` | Email address to receive the roundup |

### Local testing

```bash
pip install -r requirements.txt

# Fetch transcripts
python scripts/fetch_podcasts.py --days 2 -o /tmp/episodes.json

# Generate highlights (needs ANTHROPIC_API_KEY)
python scripts/generate_highlights.py --input /tmp/episodes.json --output /tmp/highlights.json

# Render email
python scripts/render_email.py --input /tmp/highlights.json --output /tmp/email.html

# Send (needs RESEND_API_KEY + RECIPIENT_EMAIL)
python scripts/send_email.py --input /tmp/email.html
```

## Cost

~$6/month (Anthropic API + Groq Whisper). Resend and GitHub Actions are free tier.

Forked from [podcast-digest](https://github.com/rohitkaulcoder/podcast-digest).

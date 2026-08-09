# Daily MTS TBPN TITV Roundup

Automated daily email digest of US tech industry news from 3 sources:

- **TBPN** — Technology's Daily Show
- **TITV** — The Information's TITV
- **MTS Live** — short daily YouTube videos ([@mtsituation](https://www.youtube.com/@mtsituation))

Each morning you receive an email with a byte-style digest per episode:
1. The episode title
2. A quick summary teaser (written by the model)
3. A lightly cleaned verbatim byte (quote) from the transcript — fillers stripped, the speaker's words preserved

## How it works

1. **Discover episodes**
   - **TBPN / TITV**: episodes are discovered from their podcast RSS feeds (which list only full episodes), then each is title-matched to its full-length YouTube upload. This avoids sourcing clip teasers from the YouTube channel feeds.
   - **MTS Live**: videos are discovered directly from the YouTube channel feed.
2. **Fetch transcripts via Apify** — every episode's YouTube video is transcribed with the Apify actor [`starvibe/youtube-video-transcript`](https://apify.com/starvibe/youtube-video-transcript). Running on Apify's infrastructure bypasses the YouTube cloud-provider IP block that breaks `yt-dlp` and the caption API from GitHub Actions.
3. **Filter noise** — livestreams (≥ 4 hrs) and Shorts/reels (< 2 min, or transcripts under `MIN_TRANSCRIPT_CHARS`) are skipped via the actor's duration metadata.
4. **Generate bytes** via Fireworks AI (OpenAI-compatible API, default `accounts/fireworks/models/deepseek-v4-flash-0731`) — model configurable via the `FIREWORKS_MODEL` env var.
5. **Render email** using a Jinja2 template.
6. **Send via Resend** API, subject `Daily MTS TBPN TITV Roundup — <date>`.

Runs on GitHub Actions, triggered at 7:00 AM IST (1:30 AM UTC) weekdays by an external [cron-job.org](https://cron-job.org) scheduler that POSTs to the workflow's `workflow_dispatch` endpoint. (cron-job.org free tier supports custom headers + request body, which is what authenticates the dispatch call.)

## Setup

### GitHub Secrets

| Secret | Description |
|--------|-------------|
| `FIREWORKS_API_KEY` | Fireworks AI API key (fireworks.ai) for byte generation |
| `APIFY_API_KEY` | Apify API token (console.apify.com → Settings → Integrations) for YouTube transcripts |
| `RESEND_API_KEY` | Resend API key (resend.com) |
| `RECIPIENT_EMAIL` | Email address to receive the roundup |
| `GROQ_API_KEY` | *(fallback only)* Groq API key for Whisper transcription of RSS audio |
| `YOUTUBE_API_KEY` | *(fallback only)* YouTube Data API key |

Optional env vars: `FIREWORKS_MODEL` (default `accounts/fireworks/models/deepseek-v4-flash-0731`), `MIN_TRANSCRIPT_CHARS` (default `2400`), `APIFY_ACTOR_ID` (default `starvibe~youtube-video-transcript`).

### Daily trigger (cron-job.org)

The workflow accepts `workflow_dispatch` (manual/API) with an optional `days` input (default `2`). To automate it:

1. Create a **fine-grained GitHub PAT** scoped to this repo with **Actions: Read and write**.
2. In cron-job.org, create a cron job:
   - **Method**: `POST`
   - **URL**: `https://api.github.com/repos/rohitkaulcoder/daily-tech-roundup/actions/workflows/daily-roundup.yml/dispatches`
   - **Request body**: `{"ref":"main"}`
   - **Headers**: `Authorization: Bearer <PAT>`, `Accept: application/vnd.github+json`, `Content-Type: application/json`
   - **Schedule**: weekday morning (e.g. Mon–Fri at 01:30 UTC = 07:00 IST)
3. Verify with the "Run job now" preview button — an HTTP `204 No Content` response means the dispatch succeeded.

### Local testing

```bash
pip install -r requirements.txt

# Fetch transcripts (needs APIFY_API_KEY)
python scripts/fetch_podcasts.py --days 2 -o /tmp/episodes.json

# Generate bytes (needs FIREWORKS_API_KEY)
python scripts/generate_highlights.py --input /tmp/episodes.json --output /tmp/highlights.json

# Render email
python scripts/render_email.py --input /tmp/highlights.json --output /tmp/email.html

# Send (needs RESEND_API_KEY + RECIPIENT_EMAIL)
python scripts/send_email.py --input /tmp/email.html
```

## Cost

Effectively free on the free tiers: Apify transcripts (the `starvibe/youtube-video-transcript` actor is $5 / 1,000 videos ≈ $0.005/video), Fireworks `deepseek-v4-flash`, Resend, GitHub Actions (this repo is public → free minutes), and cron-job.org all have free tiers adequate for one daily email.

Forked from [podcast-digest](https://github.com/rohitkaulcoder/podcast-digest).

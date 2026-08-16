#!/usr/bin/env python3
"""
Podcast Transcript Fetcher — Daily Tech Roundup
================================================
Fetches transcripts from 3 daily tech news podcasts using a tiered approach:
  Tier 1: RSS <podcast:transcript> tags (free)
  Tier 2: Groq Whisper on podcast audio (~$0.02/hr)
  Tier 3: YouTube transcript API (fallback, unreliable)

Forked from podcast-digest/scripts/fetch_podcasts.py

Usage:
    python fetch_podcasts.py                    # Fetch last 2 days
    python fetch_podcasts.py --days 1           # Fetch last 1 day
    python fetch_podcasts.py -o episodes.json
"""

import argparse
import json
import os
import re
import ssl
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timedelta
from typing import Optional
from xml.etree import ElementTree

import feedparser

# =============================================================================
# CONFIGURATION — 3 daily US tech news podcasts
# =============================================================================

CHANNELS = [
    {
        "name": "TBPN",
        "rss_url": "https://feeds.transistor.fm/technology-brother",
        "youtube_channel_id": "UC-DRzaGnL_vtBUpCFH5M0tg",
        "handle": "tbpnlive",
        "skip_longer_than_seconds": 4 * 3600,  # skip long livestreams on YT
        "skip_shorter_than_seconds": 5 * 60,   # skip YT Shorts/clip teasers (<5 min)
        "max_episodes": 2,
    },
    {
        "name": "TITV",
        "rss_url": "https://anchor.fm/s/9add758/podcast/rss",
        "youtube_channel_id": "UCoKqUtcUtf8QPb0GWxe5e7Q",
        "handle": "theinformation",
        "skip_longer_than_seconds": 4 * 3600,
        "skip_shorter_than_seconds": 5 * 60,
        "max_episodes": 2,
    },
    {
        "name": "MTS Live",
        "youtube_channel_id": "UClWkDGXEzsh77GAhs90wpXw",
        "handle": "mtsituation",
        "skip_longer_than_seconds": 4 * 3600,   # skip 7-8hr livestreams
        "skip_shorter_than_seconds": 2 * 60,    # skip Shorts/reels (< 2 min)
        "max_episodes": 5,
    },
]


# =============================================================================
# TIER 1: RSS TRANSCRIPT FETCHING
# =============================================================================

PODCAST_NS = "https://podcastindex.org/namespace/1.0"


def get_rss_episodes(rss_url: str, days_back: int, max_results: int) -> list:
    """Parse RSS feed and return recent episodes with metadata."""
    feed = feedparser.parse(rss_url)
    cutoff = datetime.now() - timedelta(days=days_back)
    episodes = []

    for entry in feed.entries:
        # Parse publication date
        pub_date = None
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            pub_date = datetime(*entry.published_parsed[:6])
        elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
            pub_date = datetime(*entry.updated_parsed[:6])

        if not pub_date or pub_date < cutoff:
            continue

        # Get audio enclosure URL
        audio_url = None
        for link in getattr(entry, "links", []):
            if link.get("type", "").startswith("audio/") or link.get("rel") == "enclosure":
                audio_url = link.get("href")
                break
        if not audio_url and hasattr(entry, "enclosures"):
            for enc in entry.enclosures:
                audio_url = enc.get("href")
                break

        episodes.append({
            "title": entry.get("title", ""),
            "published_at": pub_date.isoformat(),
            "description": entry.get("summary", "")[:500],
            "url": entry.get("link", ""),
            "audio_url": audio_url,
            "rss_entry": entry,  # keep for transcript extraction
        })

        if len(episodes) >= max_results:
            break

    return episodes


def normalize_title(title: str) -> str:
    """Normalize a title for fuzzy matching (lowercase, alphanumeric only)."""
    return re.sub(r"[^a-z0-9]", "", title.lower())


def find_matching_youtube_episode(rss_title: str, youtube_episodes: list) -> Optional[dict]:
    """Find the YouTube episode whose title best matches an RSS episode title.

    RSS lists full episodes; YouTube feeds also contain clip/teaser titles.
    Match on the longest shared normalized-prefix tiebreaker so we pick the
    full episode upload rather than a short clip.
    """
    if not youtube_episodes:
        return None
    target = normalize_title(rss_title)
    if not target:
        return None

    best = None
    best_score = -1
    for yt in youtube_episodes:
        cand = normalize_title(yt["title"])
        if not cand:
            continue
        # Score by count of shared leading characters.
        shared = 0
        for a, b in zip(target, cand):
            if a != b:
                break
            shared += 1
        if shared > best_score:
            best_score = shared
            best = yt

    # Require a meaningful overlap (>= 8 leading chars) to avoid junk matches.
    if best_score >= 8 and best:
        return best
    return None
    """Extract transcript from RSS entry's <podcast:transcript> tag."""
    # Approach 1: Check for podcast:transcript in links
    for link in getattr(entry, "links", []):
        link_type = link.get("type", "").lower()
        rel = link.get("rel", "").lower()
        if "transcript" in rel or link_type in (
            "application/srt",
            "application/x-subrip",
            "text/vtt",
            "text/plain",
            "text/html",
            "application/json",
        ):
            transcript_url = link.get("href")
            if transcript_url:
                return fetch_transcript_url(transcript_url, link_type)

    # Approach 2: Check for podcast_transcript attribute
    if hasattr(entry, "podcast_transcript"):
        t = entry.podcast_transcript
        url = t.get("url") if isinstance(t, dict) else getattr(t, "url", None)
        if url:
            type_ = t.get("type", "") if isinstance(t, dict) else getattr(t, "type", "")
            return fetch_transcript_url(url, type_)

    return None


def fetch_transcript_url(url: str, content_type: str) -> Optional[str]:
    """Fetch and parse a transcript URL (SRT, VTT, or plain text)."""
    try:
        ctx = ssl.create_default_context()
        req = urllib.request.Request(url, headers={"User-Agent": "DailyTechRoundup/1.0"})
        with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
            raw = resp.read().decode("utf-8", errors="replace")

        # Reject HTML pages (false positive transcript links)
        if raw.strip().startswith("<!DOCTYPE") or raw.strip().startswith("<html"):
            return None

        if "srt" in content_type or url.endswith(".srt"):
            return parse_srt(raw)
        elif "vtt" in content_type or url.endswith(".vtt"):
            return parse_vtt(raw)
        elif "json" in content_type or url.endswith(".json"):
            return parse_json_transcript(raw)
        else:
            text = re.sub(r"\s+", " ", raw).strip()
            return text if len(text) > 100 else None

    except Exception as e:
        print(f"    Warning: Error fetching transcript URL: {e}")
        return None


def parse_srt(raw: str) -> str:
    """Parse SRT subtitle format to plain text."""
    lines = []
    for line in raw.split("\n"):
        line = line.strip()
        if not line or re.match(r"^\d+$", line) or re.match(r"\d{2}:\d{2}:", line):
            continue
        lines.append(line)
    return " ".join(lines)


def parse_vtt(raw: str) -> str:
    """Parse WebVTT format to plain text."""
    lines = []
    for line in raw.split("\n"):
        line = line.strip()
        if not line or line.startswith("WEBVTT") or line.startswith("NOTE"):
            continue
        if re.match(r"\d{2}:\d{2}:", line) or "-->" in line:
            continue
        line = re.sub(r"<[^>]+>", "", line)
        lines.append(line)
    deduped = []
    for line in lines:
        if not deduped or line != deduped[-1]:
            deduped.append(line)
    return " ".join(deduped)


def parse_json_transcript(raw: str) -> str:
    """Parse JSON transcript format to plain text."""
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            texts = [seg.get("text") or seg.get("body", "") for seg in data]
            return " ".join(t for t in texts if t)
        elif isinstance(data, dict) and "segments" in data:
            texts = [seg.get("text", "") for seg in data["segments"]]
            return " ".join(t for t in texts if t)
    except:
        pass
    return None


# =============================================================================
# TIER 1.5: RSS RAW XML TRANSCRIPT CHECK
# =============================================================================

def check_rss_transcript_xml(rss_url: str, episode_title: str) -> Optional[str]:
    """Re-fetch RSS XML and look for <podcast:transcript> tags directly."""
    try:
        ctx = ssl.create_default_context()
        req = urllib.request.Request(rss_url, headers={"User-Agent": "DailyTechRoundup/1.0"})
        with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
            raw_xml = resp.read()

        root = ElementTree.fromstring(raw_xml)

        for item in root.iter("item"):
            title_el = item.find("title")
            if title_el is None:
                continue
            item_title = title_el.text or ""

            if episode_title.lower()[:40] not in item_title.lower() and item_title.lower()[:40] not in episode_title.lower():
                continue

            for child in item:
                tag = child.tag.lower()
                if "transcript" in tag:
                    url = child.get("url")
                    type_ = child.get("type", "")
                    if url:
                        return fetch_transcript_url(url, type_)

    except Exception as e:
        print(f"    Warning: XML transcript check error: {e}")

    return None


# =============================================================================
# YOUTUBE CHANNEL DISCOVERY (yt-dlp + Groq path)
# =============================================================================

YT_CHANNEL_FEED = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"

# Bypass YouTube's "Sign in to confirm you're not a bot" gate, which is
# triggered on default (web) clients from cloud/datacenter IPs. The android/tv
# clients use a different extraction path that is usually allowed.
YT_EXTRACTOR_ARGS = "youtube:player_client=android,tv"


def get_youtube_channel_episodes(channel_id: str, days_back: int, max_results: int, title_filter: Optional[str] = None) -> list:
    """Parse a YouTube channel's RSS feed and return recent videos."""
    feed = feedparser.parse(YT_CHANNEL_FEED.format(channel_id=channel_id))
    cutoff = datetime.now() - timedelta(days=days_back)
    episodes = []

    for entry in feed.entries:
        pub_date = None
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            pub_date = datetime(*entry.published_parsed[:6])
        if not pub_date or pub_date < cutoff:
            continue

        title = entry.get("title", "")
        if title_filter and title_filter.lower() not in title.lower():
            continue

        video_url = entry.get("link", "")
        video_id = entry.get("yt_videoid") or (video_url.split("v=")[-1] if "v=" in video_url else None)

        episodes.append({
            "title": title,
            "published_at": pub_date.isoformat(),
            "description": entry.get("summary", "")[:500],
            "url": video_url,
            "video_id": video_id,
            "audio_url": None,
        })

        if len(episodes) >= max_results:
            break

    return episodes


def get_youtube_video_duration(video_url: str) -> Optional[int]:
    """Get a YouTube video's duration in seconds via yt-dlp (metadata only, no download)."""
    import subprocess
    try:
        cmd = [
            "yt-dlp",
            "--skip-download",
            "--print", "duration",
            "--no-warnings",
            "--extractor-args", YT_EXTRACTOR_ARGS,
            video_url,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            print(f"    Warning: yt-dlp duration lookup failed: {result.stderr.strip()[:200]}")
            return None
        raw = result.stdout.strip()
        if not raw.isdigit():
            return None
        return int(raw)
    except Exception as e:
        print(f"    Warning: yt-dlp duration lookup error: {e}")
        return None


def download_youtube_audio(video_url: str) -> Optional[str]:
    """Download audio from a YouTube video using yt-dlp. Returns local path or None."""
    import subprocess
    try:
        tmp_dir = tempfile.mkdtemp(prefix="ytdlp_")
        out_template = os.path.join(tmp_dir, "audio.%(ext)s")
        cmd = [
            "yt-dlp",
            "-f", "bestaudio/best",
            "--extract-audio",
            "--audio-format", "mp3",
            "--audio-quality", "5",
            "-o", out_template,
            "--quiet",
            "--no-warnings",
            "--extractor-args", YT_EXTRACTOR_ARGS,
            video_url,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            print(f"    Warning: yt-dlp failed: {result.stderr.strip()[:200]}")
            return None
        for fname in os.listdir(tmp_dir):
            if fname.startswith("audio."):
                return os.path.join(tmp_dir, fname)
        return None
    except Exception as e:
        print(f"    Warning: yt-dlp error: {e}")
        return None


# =============================================================================
# TIER 1.5: APIFY YOUTUBE TRANSCRIPT ACTOR (runs on Apify infra, not IP-blocked)
# =============================================================================

APIFY_ACTOR_ID = os.environ.get("APIFY_ACTOR_ID", "starvibe~youtube-video-transcript")
APIFY_API_RUN_URL = (
    "https://api.apify.com/v2/acts/%s/run-sync-get-dataset-items"
    "?token={token}&timeout=240&format=json"
)


def get_youtube_transcript_via_apify(video_url: str) -> Optional[tuple[str, Optional[int]]]:
    """Fetch a YouTube transcript via the Apify starvibe/youtube-video-transcript actor.

    Returns (transcript_text, duration_seconds) or None. Runs on Apify's own
    infrastructure, so it bypasses YouTube's cloud-provider IP block that breaks
    yt-dlp and the caption API from GitHub Actions.

    Requires APIFY_API_KEY env var.
    """
    apify_key = os.environ.get("APIFY_API_KEY")
    if not apify_key:
        print("    Warning: APIFY_API_KEY not set, skipping Apify transcript fetch")
        return None

    url = APIFY_API_RUN_URL.replace("{token}", apify_key) % APIFY_ACTOR_ID
    payload = json.dumps({
        "youtube_url": video_url,
        "language": "en",
        "include_transcript_text": True,
    }).encode("utf-8")

    def _call_apify() -> list:
        ctx = ssl.create_default_context()
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": "DailyTechRoundup/1.0"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=300, context=ctx) as resp:
            return json.loads(resp.read().decode("utf-8"))

    try:
        items = _call_apify()

        # Retry once with a short backoff if the sync call came back empty or errored
        # (Apify's run-sync can return early under load, especially at scheduled time).
        if not items:
            print("    Apify sync returned empty, retrying once...")
            time.sleep(5)
            items = _call_apify()

        if not items:
            print(f"    Warning: Apify returned no dataset items for {video_url}")
            return None

        item = items[0]
        status = (item.get("status") or "").lower()
        if status == "error" or status == "failed":
            print(f"    Warning: Apify actor error: {item.get('message', '')[:200]}")
            return None

        text = item.get("transcript_text", "").strip()
        if not text or len(text) < 100:
            print(f"    Warning: Apify returned no usable transcript for {video_url}")
            return None

        duration = item.get("duration_seconds")
        duration = int(duration) if isinstance(duration, (int, float)) else None
        return text, duration

    except Exception as e:
        print(f"    Warning: Apify transcript fetch failed: {e}")
        return None


# =============================================================================
# TIER 1.5: YouTube CAPTION TRANSCRIPT (fallback from datacenter IPs, no bot-check)
# =============================================================================

def get_youtube_caption_transcript(video_id: str) -> Optional[tuple[str, Optional[int]]]:
    """Fetch a YouTube video's caption/auto-caption transcript.

    Returns (transcript_text, duration_seconds) or None. Duration is derived
    from the last caption snippet (start + duration), which lets us skip
    long livestreams without a yt-dlp download. Prefer captions over
    yt-dlp+Groq because yt-dlp is often blocked in cloud environments.
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        ytt_api = YouTubeTranscriptApi()
        fetched = ytt_api.fetch(video_id, languages=("en",))
        snippets = fetched.snippets
        if not snippets:
            return None

        text = " ".join(s.text for s in snippets)
        text = re.sub(r"\[Music\]", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\[Applause\]", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s+", " ", text).strip()

        if len(text) < 100:
            return None

        last = snippets[-1]
        duration_seconds = round(last.start + last.duration)
        return text, duration_seconds

    except Exception as e:
        print(f"    Warning: caption transcript fetch failed: {e}")
        return None


# =============================================================================
# TIER 2: GROQ WHISPER TRANSCRIPTION
# =============================================================================

def transcribe_local_audio_with_groq(tmp_path: str) -> Optional[str]:
    """Transcribe an existing local audio file with Groq Whisper (handles compression)."""
    groq_key = os.environ.get("GROQ_API_KEY")
    if not groq_key:
        print("    Warning: GROQ_API_KEY not set, skipping Whisper transcription")
        return None
    try:
        from groq import Groq
    except ImportError:
        print("    Warning: groq package not installed (pip install groq)")
        return None

    try:
        file_size = os.path.getsize(tmp_path)
        if file_size > 24 * 1024 * 1024:
            for bitrate in ("32k", "16k"):
                print(f"    Compressing audio ({file_size // 1024 // 1024}MB -> mono 16kHz {bitrate})...")
                compressed_path = tmp_path + f".{bitrate}.mp3"
                ret = os.system(
                    f'ffmpeg -y -i "{tmp_path}" -ac 1 -ar 16000 -b:a {bitrate} "{compressed_path}" -loglevel error 2>&1'
                )
                if ret != 0 or not os.path.exists(compressed_path):
                    print(f"    Warning: ffmpeg compression failed at {bitrate}")
                    continue
                new_size = os.path.getsize(compressed_path)
                print(f"    Compressed to {new_size // 1024 // 1024}MB")
                if new_size <= 24 * 1024 * 1024:
                    os.unlink(tmp_path)
                    tmp_path = compressed_path
                    file_size = new_size
                    break
                os.unlink(compressed_path)
            else:
                print(f"    Warning: Still too large after compression")
                os.unlink(tmp_path)
                return None

        print(f"    Transcribing with Groq Whisper ({file_size // 1024 // 1024}MB)...")
        client = Groq(api_key=groq_key)
        with open(tmp_path, "rb") as audio_file:
            transcription = client.audio.transcriptions.create(
                file=(os.path.basename(tmp_path), audio_file),
                model="whisper-large-v3-turbo",
                response_format="text",
            )
        os.unlink(tmp_path)
        text = str(transcription).strip()
        return text if len(text) > 100 else None
    except Exception as e:
        print(f"    Warning: Groq transcription error: {e}")
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        return None


def transcribe_youtube_with_groq(video_url: str) -> Optional[str]:
    """Download YouTube audio with yt-dlp, transcribe with Groq Whisper."""
    print(f"    Downloading YouTube audio with yt-dlp...")
    audio_path = download_youtube_audio(video_url)
    if not audio_path:
        return None
    return transcribe_local_audio_with_groq(audio_path)


def transcribe_with_groq(audio_url: str) -> Optional[str]:
    """Download podcast audio and transcribe with Groq Whisper."""
    groq_key = os.environ.get("GROQ_API_KEY")
    if not groq_key:
        print("    Warning: GROQ_API_KEY not set, skipping Whisper transcription")
        return None

    try:
        from groq import Groq
    except ImportError:
        print("    Warning: groq package not installed (pip install groq)")
        return None

    try:
        print(f"    Downloading audio...")
        ctx = ssl.create_default_context()
        req = urllib.request.Request(audio_url, headers={"User-Agent": "DailyTechRoundup/1.0"})
        with urllib.request.urlopen(req, timeout=300, context=ctx) as resp:
            suffix = ".mp3"
            if "mp4" in audio_url or "m4a" in audio_url:
                suffix = ".m4a"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(resp.read())
                tmp_path = tmp.name

        # Check file size — Groq has a 25MB limit, compress if needed
        file_size = os.path.getsize(tmp_path)
        if file_size > 24 * 1024 * 1024:
            for bitrate in ("32k", "16k"):
                print(f"    Compressing audio ({file_size // 1024 // 1024}MB -> mono 16kHz {bitrate})...")
                compressed_path = tmp_path + f".{bitrate}.mp3"
                ret = os.system(
                    f'ffmpeg -y -i "{tmp_path}" -ac 1 -ar 16000 -b:a {bitrate} "{compressed_path}" -loglevel error 2>&1'
                )
                if ret != 0 or not os.path.exists(compressed_path):
                    print(f"    Warning: ffmpeg compression failed at {bitrate}")
                    continue
                new_size = os.path.getsize(compressed_path)
                print(f"    Compressed to {new_size // 1024 // 1024}MB")
                if new_size <= 24 * 1024 * 1024:
                    os.unlink(tmp_path)
                    tmp_path = compressed_path
                    file_size = new_size
                    break
                os.unlink(compressed_path)
            else:
                print(f"    Warning: Still too large after compression")
                os.unlink(tmp_path)
                return None

        print(f"    Transcribing with Groq Whisper ({file_size // 1024 // 1024}MB)...")
        client = Groq(api_key=groq_key)

        with open(tmp_path, "rb") as audio_file:
            transcription = client.audio.transcriptions.create(
                file=(os.path.basename(tmp_path), audio_file),
                model="whisper-large-v3-turbo",
                response_format="text",
            )

        os.unlink(tmp_path)

        text = str(transcription).strip()
        if len(text) > 100:
            return text
        return None

    except Exception as e:
        print(f"    Warning: Groq transcription error: {e}")
        try:
            os.unlink(tmp_path)
        except:
            pass
        return None


# =============================================================================
# TIER 3: YOUTUBE TRANSCRIPT (FALLBACK)
# =============================================================================

def get_youtube_transcript(handle: str, episode_title: str) -> Optional[str]:
    """Try to get transcript from YouTube as last resort."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        from googleapiclient.discovery import build

        api_key = os.environ.get("YOUTUBE_API_KEY")
        if not api_key:
            return None

        youtube = build("youtube", "v3", developerKey=api_key)

        request = youtube.search().list(
            part="snippet",
            q=f"@{handle} {episode_title}",
            type="video",
            maxResults=1,
        )
        response = request.execute()

        if not response.get("items"):
            return None

        video_id = response["items"][0]["id"]["videoId"]
        ytt_api = YouTubeTranscriptApi()
        transcript = ytt_api.fetch(video_id)
        text = " ".join([s.text for s in transcript.snippets])
        text = re.sub(r"\[Music\]", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\[Applause\]", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    except Exception as e:
        return None


# =============================================================================
# TIERED TRANSCRIPT ORCHESTRATOR
# =============================================================================

def get_transcript_tiered(channel: dict, episode: dict) -> tuple[Optional[str], str]:
    """
    Try to get transcript using tiered approach.
    Returns (transcript_text, source) where source is 'rss', 'groq_whisper', 'youtube', or 'none'.
    """
    title = episode["title"]

    yt_url = episode.get("_youtube_url")
    if not yt_url and episode.get("youtube_match"):
        yt_url = episode["youtube_match"]["url"]
    if not yt_url and episode.get("url"):
        if "youtube.com/" in episode["url"] or "youtu.be/" in episode["url"]:
            yt_url = episode["url"]

    # Channels with a YouTube representation (hybrid or pure YouTube):
    # prefetched Apify/caption transcript -> yt-dlp -> Groq Whisper
    if channel.get("youtube_channel_id") and yt_url:
        if episode.get("_prefetched_transcript"):
            return episode["_prefetched_transcript"], "apify_or_captions"

        transcript = transcribe_youtube_with_groq(yt_url)
        if transcript:
            return transcript, "yt_dlp_groq"
        return None, "none"

    # RSS-only channels, or RSS fallback when no YouTube match was found:
    # RSS transcript tags -> Groq Whisper on the RSS audio
    # Tier 1: RSS transcript
    if channel.get("has_rss_transcript"):
        rss_entry = episode.get("rss_entry")
        if rss_entry:
            transcript = extract_rss_transcript(rss_entry)
            if transcript and len(transcript) > 100:
                return transcript, "rss"

        # Tier 1.5: Try raw XML parsing
        transcript = check_rss_transcript_xml(channel["rss_url"], title)
        if transcript and len(transcript) > 100:
            return transcript, "rss"

    # Tier 2: Groq Whisper
    audio_url = episode.get("audio_url")
    if audio_url:
        transcript = transcribe_with_groq(audio_url)
        if transcript:
            return transcript, "groq_whisper"

    # Tier 3: YouTube fallback
    handle = channel.get("handle")
    if handle:
        print(f"    Trying YouTube fallback...")
        transcript = get_youtube_transcript(handle, title)
        if transcript:
            return transcript, "youtube"

    return None, "none"


# =============================================================================
# MAIN PIPELINE
# =============================================================================

def fetch_all_podcasts(days_back: int = 2, max_per_channel: int = 2) -> list:
    """Fetch recent episodes and transcripts from all channels."""
    all_episodes = []

    print(f"\nFetching episodes from {len(CHANNELS)} channels (last {days_back} days)...\n")

    for channel in CHANNELS:
        name = channel["name"]
        print(f"  {name}...")

        skip_seconds = channel.get("skip_longer_than_seconds")
        min_seconds = channel.get("skip_shorter_than_seconds")
        max_episodes = channel.get("max_episodes", max_per_channel)
        use_apify = bool(os.environ.get("APIFY_API_KEY"))

        if channel.get("youtube_channel_id") and channel.get("rss_url"):
            # Hybrid: RSS lists only full episodes (no clip teasers). Discover via
            # RSS, then match each episode to its full-length YouTube upload so we
            # can fetch the transcript via Apify (free, bypasses cloud-IP block).
            rss_episodes = get_rss_episodes(channel["rss_url"], days_back, max_episodes)
            yt_candidates = get_youtube_channel_episodes(
                channel["youtube_channel_id"], days_back, max_results=30
            )

            episodes = []
            for ep in rss_episodes:
                match = find_matching_youtube_episode(ep["title"], yt_candidates)
                ep["youtube_match"] = match
                episodes.append(ep)
        else:
            # Pure YouTube channel (e.g. MTS): discover from channel feed directly.
            max_results = max(max_per_channel, max_episodes + 8)
            episodes = get_youtube_channel_episodes(
                channel["youtube_channel_id"],
                days_back,
                max_results,
                title_filter=channel.get("youtube_title_filter"),
            )

        ep_items = []
        for ep in episodes:
            transcript = None
            duration = None

            video_url = None
            if ep.get("youtube_match"):
                video_url = ep["youtube_match"]["url"]
            elif ep.get("url") and (ep["url"].startswith("https://www.youtube.com/") or "youtu.be" in ep["url"]):
                video_url = ep["url"]

            # Tier 1: Apify actor (runs on Apify infra, bypasses YouTube cloud-IP block)
            if use_apify and video_url:
                result = get_youtube_transcript_via_apify(video_url)
                if result:
                    transcript, duration = result
                    ep["_youtube_url"] = video_url

            # Tier 1.5: caption/auto-caption transcript (fallback)
            if not transcript:
                video_id = None
                if ep.get("youtube_match"):
                    video_id = ep["youtube_match"].get("video_id") or (video_url.split("v=")[-1] if video_url and "v=" in video_url else None)
                elif ep.get("video_id"):
                    video_id = ep["video_id"]
                if video_id:
                    caption = get_youtube_caption_transcript(video_id)
                    if caption:
                        transcript, duration = caption

            if transcript:
                # Skip long livestreams (e.g. 7-8hr MTS streams)
                if skip_seconds and duration is not None and duration >= skip_seconds:
                    print(f"  > SKIP (too long, {duration // 60}min): {ep['title'][:60]}...")
                    continue
                # Skip Shorts/reels (e.g. sub-2min MTS shorts)
                if min_seconds and duration is not None and duration < min_seconds:
                    print(f"  > SKIP (too short, {duration // 60}min): {ep['title'][:60]}...")
                    continue
                ep["_prefetched_transcript"] = transcript
                ep_items.append(ep)
                continue

            # Fallback: yt-dlp duration metadata (often blocked in cloud environments)
            if video_url:
                duration = get_youtube_video_duration(video_url)
                if duration is not None and skip_seconds and duration >= skip_seconds:
                    print(f"  > SKIP (too long, {duration // 60}min): {ep['title'][:60]}...")
                    continue
            ep_items.append(ep)

        # Cap number of videos parsed per channel per day
        if len(ep_items) > max_episodes:
            print(f"  (capping {len(ep_items)} -> {max_episodes} episodes)")
            ep_items = ep_items[:max_episodes]
        episodes = ep_items

        if not episodes:
            print(f"  (no new episodes)")
            continue

        for ep in episodes:
            print(f"  > {ep['title'][:60]}...")

            transcript, source = get_transcript_tiered(channel, ep)

            if transcript:
                print(f"    Got transcript via {source} ({len(transcript):,} chars)")
            else:
                print(f"    No transcript available")

            ep_url = ep.get("_youtube_url") or (ep.get("youtube_match") or {}).get("url") or ep.get("url", "")

            all_episodes.append({
                "podcast": name,
                "title": ep["title"],
                "url": ep_url,
                "published_at": ep["published_at"],
                "description": ep["description"],
                "transcript": transcript,
                "transcript_length": len(transcript) if transcript else 0,
                "has_transcript": transcript is not None,
                "transcript_source": source,
            })

    return all_episodes


def main():
    parser = argparse.ArgumentParser(
        description="Fetch transcripts for Daily Tech Roundup (TBPN, TITV, MTS Live)",
    )
    parser.add_argument("--days", type=int, default=2, help="Days to look back (default: 2)")
    parser.add_argument("--max-per-channel", type=int, default=2, help="Max episodes per channel (default: 2)")
    parser.add_argument("-o", "--output", type=str, help="Output file (default: print to stdout)")

    args = parser.parse_args()

    episodes = fetch_all_podcasts(
        days_back=args.days,
        max_per_channel=args.max_per_channel,
    )

    # Summary
    total = len(episodes)
    with_transcript = sum(1 for e in episodes if e["has_transcript"])
    by_source = {}
    for e in episodes:
        src = e["transcript_source"]
        by_source[src] = by_source.get(src, 0) + 1

    print(f"\n{'='*60}")
    print(f"SUMMARY")
    print(f"{'='*60}")
    print(f"Total episodes found: {total}")
    print(f"With transcripts: {with_transcript}")
    print(f"By source: {json.dumps(by_source)}")

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(episodes, f, indent=2, ensure_ascii=False)
        print(f"\nSaved to: {args.output}")
    else:
        print(json.dumps(episodes, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

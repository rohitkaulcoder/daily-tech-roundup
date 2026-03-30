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
        "has_rss_transcript": False,
        "handle": "tbpnlive",
    },
    {
        "name": "TITV",
        "rss_url": "https://anchor.fm/s/9add758/podcast/rss",
        "has_rss_transcript": False,
        "handle": "theinformation",
    },
    {
        "name": "Techmeme Ride Home",
        "rss_url": "https://feeds.megaphone.fm/techmemeridehome",
        "has_rss_transcript": True,
        "handle": "techmemeridehome",
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


def extract_rss_transcript(entry) -> Optional[str]:
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
# TIER 2: GROQ WHISPER TRANSCRIPTION
# =============================================================================

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
        rss_url = channel["rss_url"]
        print(f"  {name}...")

        episodes = get_rss_episodes(rss_url, days_back, max_per_channel)

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

            all_episodes.append({
                "podcast": name,
                "title": ep["title"],
                "url": ep["url"],
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
        description="Fetch transcripts for Daily Tech Roundup (TBPN, TITV, Techmeme Ride Home)",
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

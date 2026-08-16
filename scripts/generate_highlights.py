#!/usr/bin/env python3
"""
Generate Podcast Highlights — Daily Tech Roundup
=================================================
Reads episodes JSON (from fetch_podcasts.py), generates highlights
via Fireworks AI (OpenAI-compatible API), and outputs structured highlights JSON.

Uses the `openai` client pointed at Fireworks' inference endpoint —
works in GitHub Actions with a FIREWORKS_API_KEY secret.

Usage:
    python generate_highlights.py --input episodes.json --output highlights.json
    python generate_highlights.py --input episodes.json --dry-run
"""

import argparse
import json
import os
import sys

import openai


FIREWORKS_BASE_URL = os.environ.get(
    "FIREWORKS_BASE_URL", "https://api.fireworks.ai/inference/v1"
)
FIREWORKS_MODEL = os.environ.get(
    "FIREWORKS_MODEL", "accounts/fireworks/models/deepseek-v4-flash-0731"
)

# Skip reels/shorts: transcripts shorter than ~2 minutes of speech (~400 words)
# are not worth digesting. Applies to all sources regardless of feed metadata.
MIN_TRANSCRIPT_CHARS = int(os.environ.get("MIN_TRANSCRIPT_CHARS", "2400"))


HIGHLIGHT_PROMPT = """You are a high-signal tech-news editor. Read the podcast transcript below and extract ONLY items a busy tech professional genuinely needs to know.

STRICTLY EXCLUDE (low-signal noise — drop all of it):
- Banter, jokes, personal anecdotes, chat/community chatter, host riffing
- visual/stage direction, sound-effect tags, intros/outros
- Merch plugs, ad reads, sponsor segments, self-promotion
- Trivia that has no real-world stakes, pure speculation without evidence
- Retreads of already-widely-known news with nothing new added
Only keep items with real news, hard data, a fresh named insight, or a consequential shift for companies/markets. Fewer, sharper items are better than many. If everything in the episode is noise, output [].

For each item you keep, extract EXACTLY these fields:

1. "topic" — the single most relevant theme, chosen ONLY from this fixed list:
   "AI & Models" | "Chips & Infrastructure" | "Deals & Funding" | "Companies & Markets" | "Policy & Regulation"

2. "headline" — one short declarative headline (12 words max) stating the actual news/claim. No fluff, no marketing tone.

3. "context" — 1–2 sentences, in your own words (NOT quoted), that say what happened and why it matters to the industry. This is the scan-friendly summary.

4. "speaker" — who said it (host or guest), if determinable, else "".

5. "quote" — ONE compact verbatim quote, at most 2 sentences and ~40 words, lightly cleaned (remove fillers like "um"/"you know", [music]/[applause] tags, repeated starts; fix truncation). Only include the quote if the speaker actually said something punchy and quotable; otherwise set it to "". NEVER fabricate or paraphrase the quote — quote only real words from the transcript.

Output as a JSON array. Each element has exactly the keys "topic", "headline", "context", "speaker", "quote". Only output the JSON, nothing else.

EPISODE: {title}
PODCAST: {podcast}

TRANSCRIPT:
{transcript}"""


def generate_highlights(episode: dict, client: openai.OpenAI) -> list:
    """Generate highlights for an episode using the Fireworks API."""
    title = episode["title"]
    podcast = episode["podcast"]
    transcript = episode["transcript"]

    # Skip HTML content (false positive RSS transcript detection)
    if transcript.strip().startswith("<!DOCTYPE") or transcript.strip().startswith("<html"):
        print(f"    Warning: Transcript is HTML, not text — skipping")
        return []

    # Truncate very long transcripts to stay within token limits
    if len(transcript) > 80000:
        transcript = transcript[:80000]

    prompt = HIGHLIGHT_PROMPT.format(
        title=title,
        podcast=podcast,
        transcript=transcript,
    )

    try:
        print(f"    Calling Fireworks API ({FIREWORKS_MODEL})...")
        response = client.chat.completions.create(
            model=FIREWORKS_MODEL,
            max_tokens=8192,
            temperature=0.3,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = response.choices[0].message.content or ""

        # Strip markdown code fences
        raw = raw.replace("```json", "").replace("```", "").strip()
        # Find the JSON array
        bracket_idx = raw.find("[")
        if bracket_idx >= 0:
            raw = raw[bracket_idx:]
        rbracket_idx = raw.rfind("]")
        if rbracket_idx >= 0:
            raw = raw[:rbracket_idx + 1]

        highlights = json.loads(raw)

        # Log token usage
        usage = response.usage
        input_tokens = getattr(usage, "prompt_tokens", 0)
        output_tokens = getattr(usage, "completion_tokens", 0)
        print(f"    Tokens: {input_tokens:,} in / {output_tokens:,} out")

        return highlights

    except json.JSONDecodeError as e:
        print(f"    Error: Failed to parse API output as JSON: {e}")
        return []
    except openai.APIError as e:
        print(f"    Error: Fireworks API error: {e}")
        return []
    except Exception as e:
        print(f"    Error: {e}")
        return []


def main():
    parser = argparse.ArgumentParser(description="Generate podcast highlights via Fireworks API")
    parser.add_argument("--input", type=str, required=True, help="Input episodes JSON file")
    parser.add_argument("--output", type=str, help="Output highlights JSON file")
    parser.add_argument("--dry-run", action="store_true", help="Print prompt instead of calling API")

    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"Error: Input file not found: {args.input}")
        sys.exit(1)

    with open(args.input) as f:
        episodes = json.load(f)

    # Filter to episodes with transcripts, skipping reels/shorts (tiny transcripts)
    all_with_transcript = [e for e in episodes if e.get("has_transcript") and e.get("transcript")]
    episodes_with_transcript = [e for e in all_with_transcript if len(e["transcript"]) >= MIN_TRANSCRIPT_CHARS]
    skipped_short = len(all_with_transcript) - len(episodes_with_transcript)
    if skipped_short:
        print(f"Skipped {skipped_short} short clip(s) (below {MIN_TRANSCRIPT_CHARS} chars)")

    if not episodes_with_transcript:
        print("No episodes with transcripts found. Nothing to do.")
        # Write empty highlights file so downstream steps can handle gracefully
        if args.output:
            with open(args.output, "w") as f:
                json.dump([], f)
        sys.exit(0)

    print(f"\n{'='*60}")
    print(f"  DAILY TECH ROUNDUP — HIGHLIGHT GENERATION")
    print(f"{'='*60}")
    print(f"Episodes with transcripts: {len(episodes_with_transcript)}")
    print()

    if args.dry_run:
        for ep in episodes_with_transcript:
            prompt = HIGHLIGHT_PROMPT.format(
                title=ep["title"],
                podcast=ep["podcast"],
                transcript=ep["transcript"][:500] + "...",
            )
            print(f"--- {ep['podcast']} — {ep['title']} ---")
            print(prompt[:1000])
            print("...\n")
        print("(dry run — no API calls made)")
        return

    client = openai.OpenAI(
        api_key=os.environ.get("FIREWORKS_API_KEY"),
        base_url=FIREWORKS_BASE_URL,
        timeout=600.0,  # 10-min timeout per request
    )
    all_highlights = []

    for ep in episodes_with_transcript:
        print(f"  {ep['podcast']} — {ep['title'][:60]}...")
        highlights = generate_highlights(ep, client)

        if not highlights:
            print(f"    No highlights generated")
            continue

        print(f"    Generated {len(highlights)} highlights")

        all_highlights.append({
            "episode": {
                "title": ep["title"],
                "podcast": ep["podcast"],
                "url": ep.get("url", ""),
                "published_at": ep.get("published_at", ""),
            },
            "highlights": highlights,
        })
        print()

    # Output
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(all_highlights, f, indent=2, ensure_ascii=False)
        print(f"Highlights saved to: {args.output}")
    else:
        print(json.dumps(all_highlights, indent=2, ensure_ascii=False))

    total_highlights = sum(len(h["highlights"]) for h in all_highlights)
    print(f"\n{'='*60}")
    print(f"DONE — {len(all_highlights)} episodes, {total_highlights} highlights")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()

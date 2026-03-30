#!/usr/bin/env python3
"""
Generate Podcast Highlights — Daily Tech Roundup
=================================================
Reads episodes JSON (from fetch_podcasts.py), generates highlights
via Anthropic API, and outputs structured highlights JSON.

Replaces the `claude --print` subprocess approach from podcast-digest
with direct Anthropic SDK calls — works in GitHub Actions.

Usage:
    python generate_highlights.py --input episodes.json --output highlights.json
    python generate_highlights.py --input episodes.json --dry-run
"""

import argparse
import json
import os
import sys

import anthropic


HIGHLIGHT_PROMPT = """Read the podcast transcript below and extract 10-15 highlights.

Each highlight should be a 3-4 paragraph mini-essay that captures one complete idea arc from the conversation. It should read like a well-written blog excerpt — not a transcript and not a dry summary. Preserve the speaker's insight, their reasoning, the evidence they cite, and the conclusion, all in tight clear prose. Cut all filler, repetition, and meandering — just the substance.

Do NOT add any labels like "Key insight" or "Why this matters." The highlight should stand entirely on its own.

For each highlight, identify the speaker (host or guest). Use the episode title and context to determine speaker names.

Output as a JSON array. Each element has two keys: "speaker" (string) and "text" (string with the 3-4 paragraph highlight).

Only output the JSON, nothing else. Skip any ad reads or sponsor segments.

EPISODE: {title}
PODCAST: {podcast}

TRANSCRIPT:
{transcript}"""


def generate_highlights(episode: dict, client: anthropic.Anthropic) -> list:
    """Generate highlights for an episode using Anthropic API."""
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
        print(f"    Calling Anthropic API...")
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=8192,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = response.content[0].text.strip()

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
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        print(f"    Tokens: {input_tokens:,} in / {output_tokens:,} out")

        return highlights

    except json.JSONDecodeError as e:
        print(f"    Error: Failed to parse API output as JSON: {e}")
        return []
    except anthropic.APIError as e:
        print(f"    Error: Anthropic API error: {e}")
        return []
    except Exception as e:
        print(f"    Error: {e}")
        return []


def main():
    parser = argparse.ArgumentParser(description="Generate podcast highlights via Anthropic API")
    parser.add_argument("--input", type=str, required=True, help="Input episodes JSON file")
    parser.add_argument("--output", type=str, help="Output highlights JSON file")
    parser.add_argument("--dry-run", action="store_true", help="Print prompt instead of calling API")

    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"Error: Input file not found: {args.input}")
        sys.exit(1)

    with open(args.input) as f:
        episodes = json.load(f)

    # Filter to episodes with transcripts
    episodes_with_transcript = [e for e in episodes if e.get("has_transcript") and e.get("transcript")]

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

    client = anthropic.Anthropic()  # uses ANTHROPIC_API_KEY env var
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

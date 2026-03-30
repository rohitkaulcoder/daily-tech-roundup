#!/usr/bin/env python3
"""
Render Email HTML — Daily Tech Roundup
=======================================
Reads highlights JSON and renders an email-safe HTML page using Jinja2.

Usage:
    python render_email.py --input highlights.json --output email.html
    python render_email.py --input highlights.json  # writes to stdout
"""

import argparse
import json
import os
import sys
from datetime import datetime

from jinja2 import Environment, FileSystemLoader


def render_email(highlights_data: list, date_str: str) -> str:
    """Render email HTML from highlights data."""
    template_dir = os.path.join(os.path.dirname(__file__), "..", "templates")
    env = Environment(loader=FileSystemLoader(template_dir), autoescape=False)
    template = env.get_template("email_template.html")

    episode_count = len(highlights_data)
    highlight_count = sum(len(ep["highlights"]) for ep in highlights_data)

    # Format date for display
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        formatted_date = dt.strftime("%B %d, %Y")
    except ValueError:
        formatted_date = date_str

    return template.render(
        date=formatted_date,
        episode_count=episode_count,
        highlight_count=highlight_count,
        episodes=highlights_data,
    )


def main():
    parser = argparse.ArgumentParser(description="Render Daily Tech Roundup email HTML")
    parser.add_argument("--input", type=str, required=True, help="Input highlights JSON file")
    parser.add_argument("--output", type=str, help="Output HTML file (default: stdout)")
    parser.add_argument("--date", type=str, default=datetime.now().strftime("%Y-%m-%d"),
                        help="Date string (default: today)")

    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"Error: Input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    with open(args.input) as f:
        highlights_data = json.load(f)

    if not highlights_data:
        print("No highlights to render. Skipping.", file=sys.stderr)
        sys.exit(0)

    html = render_email(highlights_data, args.date)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"Email HTML saved to: {args.output}", file=sys.stderr)
    else:
        print(html)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Send Email — Daily Tech Roundup
================================
Sends the rendered HTML email via Resend API.

Usage:
    python send_email.py --input email.html
    python send_email.py --input email.html --dry-run   # print HTML to stdout
"""

import argparse
import os
import sys
from datetime import datetime

import resend


def send_roundup(html_content: str, date_str: str) -> bool:
    """Send the daily roundup email via Resend."""
    resend.api_key = os.environ.get("RESEND_API_KEY")
    if not resend.api_key:
        print("Error: RESEND_API_KEY not set", file=sys.stderr)
        return False

    recipient = os.environ.get("RECIPIENT_EMAIL")
    if not recipient:
        print("Error: RECIPIENT_EMAIL not set", file=sys.stderr)
        return False

    try:
        result = resend.Emails.send({
            "from": "Daily Tech Roundup <onboarding@resend.dev>",
            "to": [recipient],
            "subject": f"Daily Tech Roundup — {date_str}",
            "html": html_content,
        })
        print(f"Email sent successfully. ID: {result.get('id', 'unknown')}")
        return True

    except Exception as e:
        print(f"Error sending email: {e}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(description="Send Daily Tech Roundup email via Resend")
    parser.add_argument("--input", type=str, required=True, help="Input HTML email file")
    parser.add_argument("--dry-run", action="store_true", help="Print HTML to stdout instead of sending")
    parser.add_argument("--date", type=str, default=datetime.now().strftime("%B %d, %Y"),
                        help="Date for subject line (default: today)")

    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"Error: Input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    with open(args.input) as f:
        html_content = f.read()

    if not html_content.strip():
        print("Empty HTML file. Nothing to send.", file=sys.stderr)
        sys.exit(0)

    if args.dry_run:
        print(html_content)
        print("\n(dry run — email not sent)", file=sys.stderr)
        return

    if not send_roundup(html_content, args.date):
        sys.exit(1)


if __name__ == "__main__":
    main()

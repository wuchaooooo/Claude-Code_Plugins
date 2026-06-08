#!/usr/bin/env python3
"""
Format WeFlow API message JSON into chatlog lines.

Reads a WeFlow /api/v1/messages JSON response (from file or stdin) and outputs
formatted chatlog lines, one per message, sorted chronologically.

Usage:
    python format_messages.py -n "张三" -i response.json
    python format_messages.py -n "张三" -i response.json -o chatlog.txt
    curl ... | python format_messages.py -n "张三"

Output format:
    [YYYY-MM-DD HH:MM] <sender>: <content>
"""

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path


def format_timestamp(ts: int, tz_offset: int = 8) -> str:
    """Convert Unix timestamp to local time string.

    Defaults to UTC+8 (Asia/Shanghai) since WeChat timestamps are in that zone.
    Override with TZ_OFFSET environment variable (hours, e.g. '8' or '-5').
    """
    if tz_offset is None:
        tz_offset = int(Path("/etc/timezone").exists())  # will fail, but we handle below
    try:
        tz_offset = int(Path("/etc/timezone").read_text().strip()) if False else tz_offset
    except Exception:
        pass
    tz = timezone(timedelta(hours=tz_offset))
    dt = datetime.fromtimestamp(ts, tz=tz)
    return dt.strftime("%Y-%m-%d %H:%M")


def format_messages(
    data: dict,
    contact_name: str,
    tz_offset: int = 8,
) -> list[str]:
    """Convert WeFlow API response to formatted chatlog lines.

    Args:
        data: Parsed JSON from WeFlow /api/v1/messages response.
        contact_name: Display name for the other party (remark/nickname).
        tz_offset: UTC offset in hours (default 8 = Asia/Shanghai).

    Returns:
        List of formatted lines, sorted by createTime ascending.
    """
    messages = data.get("messages", [])
    if not messages:
        return []

    lines = []
    for msg in messages:
        content = (msg.get("content") or "").strip()
        if not content:
            continue

        ts = msg.get("createTime", 0)
        is_send = msg.get("isSend", 0)

        time_str = format_timestamp(ts, tz_offset)
        sender = "我" if is_send == 1 else "好友"

        lines.append((ts, f"[{time_str}] {sender}: {content}"))

    # Sort by timestamp ascending
    lines.sort(key=lambda x: x[0])
    return [line for _, line in lines]


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Format WeFlow API message JSON into chatlog lines"
    )
    parser.add_argument(
        "-i", "--input",
        help="Input JSON file (default: stdin)",
    )
    parser.add_argument(
        "-n", "--contact-name",
        required=True,
        help="Display name for the other party (remark or nickname)",
    )
    parser.add_argument(
        "-o", "--output",
        help="Output file (default: stdout)",
    )
    parser.add_argument(
        "--tz-offset",
        type=int,
        default=8,
        help="UTC offset in hours (default: 8, for Asia/Shanghai)",
    )
    parser.add_argument(
        "--jsonl",
        action="store_true",
        help="Input is JSONL (one JSON object per line) instead of a single JSON object",
    )
    args = parser.parse_args()

    # Read input
    if args.input:
        with open(args.input, "r", encoding="utf-8") as f:
            raw = f.read()
    else:
        raw = sys.stdin.read()

    # Parse input
    if args.jsonl:
        all_lines = []
        for line in raw.strip().split("\n"):
            if not line.strip():
                continue
            data = json.loads(line)
            all_lines.extend(format_messages(data, args.contact_name, args.tz_offset))
        output = "\n".join(all_lines) + ("\n" if all_lines else "")
    else:
        data = json.loads(raw)
        lines = format_messages(data, args.contact_name, args.tz_offset)
        output = "\n".join(lines) + ("\n" if lines else "")

    # Write output
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
    else:
        sys.stdout.write(output)


if __name__ == "__main__":
    main()

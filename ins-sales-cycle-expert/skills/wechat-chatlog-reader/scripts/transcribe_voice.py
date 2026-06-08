#!/usr/bin/env python3
"""
Transcribe WeChat voice messages using either a remote ASR API (volcano
doubao) or a local whisper-cpp server.

Reads a WeFlow messages JSON (with media=1&voice=1), finds all voice
messages, sends WAV files to the chosen backend, and outputs JSON with
transcribed text.

Usage:
    # Remote (volcano doubao) — default, same as before
    python transcribe_voice.py -i response.json -k <API_KEY>
    python transcribe_voice.py -i response.json -k <API_KEY> --replace-content -o out.json

    # Local whisper-cpp server
    python transcribe_voice.py -i response.json --backend local \
        --whisper-url http://127.0.0.1:8080 --replace-content -o out.json

Environment:
    ASR_API_KEY     — API key for remote backend (alternative to -k)
    WHISPER_URL     — base URL for local whisper server
"""

import json
import os
import re
import sys
import time
import base64
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


def _normalize_transcript(text: str) -> str:
    """Collapse newlines (and run-on punctuation) into Chinese commas so each
    voice message renders as a single chatlog line.

    Whisper-cpp commonly breaks long utterances at clause boundaries with
    ``\\n``; the remote LLM backend can do the same. ``format_messages.py``
    emits one message per line, so a multi-line transcript would otherwise
    split a single voice message across multiple chatlog rows.
    """
    if not text:
        return text
    text = re.sub(r"[\r\n]+", "，", text)
    text = re.sub(r"，+", "，", text)
    return text.strip("，").strip()

try:
    import requests
except ImportError:
    print("Error: requests library required. Install with: pip install requests")
    sys.exit(1)


# ── Remote backend (volcano doubao) ──────────────────────────────────────────

REMOTE_API_URL = "https://ark.cn-beijing.volces.com/api/v3/responses"
REMOTE_MODEL = "doubao-seed-2-0-lite-260428"

REMOTE_INSTRUCTIONS = (
    "You are a highly advanced AI specialized in Automatic Speech Recognition (ASR). "
    "Your sole function is to transcribe the audio provided by the user.\n"
    "You must adhere to the following rules STRICTLY:\n"
    "1. Your output must contain ONLY the transcribed text from the audio.\n"
    "2. Do not include any introductory phrases, explanations, apologies, or any other conversational text. "
    'For example, never start your response with "Here is the transcription:" or "The transcribed text is:".\n'
    "3. Do not use any formatting, such as markdown, bolding, or italics.\n"
    "4. If the audio is unclear, inaudible, or contains no speech, you must output an empty string."
)

REMOTE_PROMPT = "这段语音的内容是："


def _encode_audio_base64(file_path: str) -> str:
    """Read audio file and return as base64 data URI."""
    ext = os.path.splitext(file_path)[1].lower()
    mime_map = {
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".aac": "audio/aac",
    }
    mime = mime_map.get(ext, "audio/wav")
    with open(file_path, "rb") as f:
        data = base64.b64encode(f.read()).decode("ascii")
    return f"data:{mime};base64,{data}"


def _transcribe_remote(
    file_path: str,
    api_key: str,
    timeout: int = 120,
    max_retries: int = 3,
) -> dict:
    """Send audio to volcano doubao for transcription."""
    audio_uri = _encode_audio_base64(file_path)

    body = {
        "model": REMOTE_MODEL,
        "instructions": REMOTE_INSTRUCTIONS,
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_audio", "audio_url": audio_uri},
                    {"type": "input_text", "text": REMOTE_PROMPT},
                ],
            }
        ],
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    for attempt in range(max_retries):
        try:
            resp = requests.post(REMOTE_API_URL, headers=headers, json=body, timeout=timeout)
            if resp.status_code == 200:
                text = _extract_remote_text(resp.json())
                return {"success": True, "text": text, "error": ""}
            if resp.status_code == 429:
                time.sleep(min(2 ** attempt * 10, 60))
                continue
            error = f"HTTP {resp.status_code}: {resp.text[:300]}"
            if attempt < max_retries - 1:
                time.sleep(min(2 ** attempt * 5, 30))
                continue
            return {"success": False, "text": "", "error": error}
        except requests.exceptions.Timeout:
            if attempt < max_retries - 1:
                continue
            return {"success": False, "text": "", "error": "Request timeout"}
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(2)
                continue
            return {"success": False, "text": "", "error": str(e)}
    return {"success": False, "text": "", "error": "Max retries exceeded"}


def _extract_remote_text(response_data: dict) -> str:
    output = response_data.get("output", [])
    for item in output:
        if item.get("type") == "message":
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    return content.get("text", "").strip()
    choices = response_data.get("choices", [])
    if choices:
        return choices[0].get("message", {}).get("content", "").strip()
    return ""


# ── Local backend (whisper-cpp server) ───────────────────────────────────────

def _transcribe_local(
    file_path: str,
    server_url: str,
    language: str = "auto",
    timeout: int = 300,
    max_retries: int = 2,
) -> dict:
    """Send audio to local whisper-cpp server (POST /inference)."""
    url = server_url.rstrip("/") + "/inference"

    for attempt in range(max_retries):
        try:
            with open(file_path, "rb") as f:
                resp = requests.post(
                    url,
                    files={"file": (os.path.basename(file_path), f)},
                    data={"response_format": "json", "language": language},
                    timeout=timeout,
                )
            if resp.status_code == 200:
                data = resp.json()
                text = (data.get("text") or "").strip()
                return {"success": True, "text": text, "error": ""}
            error = f"HTTP {resp.status_code}: {resp.text[:300]}"
            if attempt < max_retries - 1:
                time.sleep(2)
                continue
            return {"success": False, "text": "", "error": error}
        except requests.exceptions.ConnectionError as e:
            return {"success": False, "text": "", "error": f"Connection refused: {e}"}
        except requests.exceptions.Timeout:
            if attempt < max_retries - 1:
                continue
            return {"success": False, "text": "", "error": "Request timeout"}
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(2)
                continue
            return {"success": False, "text": "", "error": str(e)}
    return {"success": False, "text": "", "error": "Max retries exceeded"}


# ── Dispatcher ───────────────────────────────────────────────────────────────

def transcribe_audio(
    file_path: str,
    backend: str,
    api_key: str = "",
    whisper_url: str = "",
    whisper_language: str = "auto",
    timeout: int = 120,
) -> dict:
    """Transcribe a single audio file via the chosen backend.

    Returns: {"success": bool, "text": str, "error": str}
    """
    if not os.path.exists(file_path):
        return {"success": False, "text": "", "error": f"File not found: {file_path}"}
    if os.path.getsize(file_path) == 0:
        return {"success": False, "text": "", "error": f"Empty file: {file_path}"}

    if backend == "remote":
        result = _transcribe_remote(file_path, api_key, timeout=timeout)
    elif backend == "local":
        result = _transcribe_local(file_path, whisper_url, language=whisper_language, timeout=timeout)
    else:
        return {"success": False, "text": "", "error": f"Unknown backend: {backend}"}
    if result.get("success") and result.get("text"):
        result["text"] = _normalize_transcript(result["text"])
    return result


def process_messages(
    data: dict,
    backend: str,
    api_key: str = "",
    whisper_url: str = "",
    whisper_language: str = "auto",
    replace_content: bool = False,
    max_workers: int = 3,
    timeout: int = 120,
) -> dict:
    """Process all voice messages in a WeFlow response."""
    messages = data.get("messages", [])
    voice_messages = [m for m in messages if m.get("mediaType") == "voice"]

    if not voice_messages:
        return data

    successes = 0
    failures = 0

    def transcribe_one(msg):
        path = msg.get("mediaLocalPath", "")
        result = transcribe_audio(
            path, backend,
            api_key=api_key,
            whisper_url=whisper_url,
            whisper_language=whisper_language,
            timeout=timeout,
        )
        msg["transcribedText"] = result.get("text", "")
        if replace_content and result["success"] and result["text"]:
            msg["content"] = f"[语音]({result['text']})"
        return msg, result

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(transcribe_one, m): i for i, m in enumerate(voice_messages)}
        for future in as_completed(futures):
            try:
                msg, result = future.result()
                if result["success"]:
                    successes += 1
                    text = result["text"]
                    preview = text[:50] + ("..." if len(text) > 50 else "")
                    print(f"  [{successes}/{len(voice_messages)}] {msg['mediaFileName']}: {preview}", flush=True)
                else:
                    failures += 1
                    print(f"  [FAIL] {msg['mediaFileName']}: {result['error']}", flush=True)
            except Exception as e:
                failures += 1
                print(f"  [ERROR] {e}", flush=True)

    data["_transcription"] = {
        "total": len(voice_messages),
        "success": successes,
        "failed": failures,
        "backend": backend,
    }
    return data


def main():
    parser = argparse.ArgumentParser(description="Transcribe WeChat voice messages")
    parser.add_argument("-i", "--input", required=True, help="WeFlow messages JSON file")
    parser.add_argument("-o", "--output", help="Output JSON file (default: stdout)")
    parser.add_argument("--backend", choices=["remote", "local"], default="remote",
                        help="ASR backend: 'remote' (volcano doubao) or 'local' (whisper-cpp). Default: remote")

    # Remote (volcano) options
    parser.add_argument("-k", "--api-key",
                        help="Remote ASR API key (or set ASR_API_KEY env var). Required when --backend=remote")

    # Local (whisper-cpp) options
    parser.add_argument("--whisper-url", default=os.environ.get("WHISPER_URL", "http://127.0.0.1:8080"),
                        help="Whisper-cpp server base URL (default: http://127.0.0.1:8080 or $WHISPER_URL)")
    parser.add_argument("--whisper-language", default="auto",
                        help="Language hint for whisper (e.g. 'zh', 'en', 'auto'). Default: auto")

    # Common options
    parser.add_argument("--replace-content", action="store_true",
                        help="Replace [语音消息] with transcribed text in message content")
    parser.add_argument("--max-workers", type=int, default=3,
                        help="Concurrent workers (default: 3)")
    parser.add_argument("--timeout", type=int, default=120,
                        help="Request timeout in seconds (default: 120)")

    args = parser.parse_args()

    # Per-backend arg validation
    if args.backend == "remote":
        args.api_key = args.api_key or os.environ.get("ASR_API_KEY")
        if not args.api_key:
            print("Error: --backend=remote requires an API key (-k or ASR_API_KEY env var).")
            sys.exit(1)
    elif args.backend == "local":
        if not args.whisper_url:
            print("Error: --backend=local requires --whisper-url.")
            sys.exit(1)
        # Local whisper tends to be slower per-request, give it a bigger default
        # only if the user didn't override.
        if args.timeout == 120:
            args.timeout = 300

    with open(args.input, "r", encoding="utf-8") as f:
        data = json.load(f)

    data = process_messages(
        data, args.backend,
        api_key=args.api_key or "",
        whisper_url=args.whisper_url,
        whisper_language=args.whisper_language,
        replace_content=args.replace_content,
        max_workers=args.max_workers,
        timeout=args.timeout,
    )

    output = json.dumps(data, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
    else:
        print(output)


if __name__ == "__main__":
    main()

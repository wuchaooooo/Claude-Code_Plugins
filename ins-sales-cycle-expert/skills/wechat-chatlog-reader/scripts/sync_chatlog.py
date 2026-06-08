#!/usr/bin/env python3
"""
WeChat chatlog sync — two modes:

  FULL mode (--full):
      Delete all files under raw/chatlog/, then pull ALL friends from scratch.
      No sessions pre-filtering, no since parameter.

  INCREMENTAL mode (default):
      Use sessions API to pre-filter: compare session.lastTimestamp vs local
      最后拉取时间. Only pull friends with new messages.

Usage:
    # Full sync — wipe and rebuild everything
    python sync_chatlog.py --weflow-token <TOKEN> --asr-key <KEY> --vault-chatlog <PATH> --full

    # Incremental — only pull friends with new messages
    python sync_chatlog.py --weflow-token <TOKEN> --asr-key <KEY> --vault-chatlog <PATH>

    # Dry-run to preview
    python sync_chatlog.py ... --dry-run
"""

import json, os, re, subprocess, sys, argparse, threading, time, random
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
from format_messages import format_messages

WEFLOW_BASE = "http://127.0.0.1:5031"
TZ = timezone(timedelta(hours=8))
SKILL_DIR = os.path.dirname(SCRIPT_DIR)
BLACKLIST = {'filehelper', 'weixin', 'mphelper'}
MEDIA_ACCOUNTS = {
    'wxid_9251842518611',  # 钱江晚报
    'wxid_7620846208112',  # 虎嗅APP
    'wxid_4332873328811',  # 19楼
    'nanfangzhoumo',       # 南方周末
}
DEFAULT_BLACKLIST_FILE = os.path.join(SKILL_DIR, 'references', 'blacklist.md')


def read_skill_frontmatter():
    """Read the SKILL.md frontmatter. Returns dict of fields (or empty dict)."""
    fm_path = os.path.join(SKILL_DIR, 'SKILL.md')
    if not os.path.exists(fm_path):
        return {}
    with open(fm_path, 'r', encoding='utf-8') as f:
        content = f.read()
    if not content.startswith('---'):
        return {}
    end = content.find('\n---', 3)
    if end == -1:
        return {}
    fm = {}
    for line in content[3:end].strip().split('\n'):
        m = re.match(r'^([a-zA-Z_][\w-]*):\s*(.*)', line)
        if m:
            fm[m.group(1)] = m.group(2).strip()
    return fm


DEFAULT_ASR_BACKEND = read_skill_frontmatter().get('default_asr_backend', 'local')
if DEFAULT_ASR_BACKEND not in ('remote', 'local'):
    DEFAULT_ASR_BACKEND = 'local'


_WXID_PATTERN = re.compile(r'^[a-zA-Z][a-zA-Z0-9_-]{3,}$')


def load_user_blacklist(path=DEFAULT_BLACKLIST_FILE):
    """Load user-maintained blacklist from references/blacklist.md.

    Format: one wxid per line. Lines starting with '#' are comments.
    Inline comments after a wxid (e.g. 'wxid_xxx  # reason') are also stripped.
    Lines that don't look like a valid wxid (must be ASCII alphanumeric /
    underscore / hyphen, start with a letter, >= 4 chars) are ignored — this
    lets the file contain prose/markdown without polluting the list.
    Returns a set. Returns empty set if the file is missing.
    """
    bl = set()
    if not os.path.exists(path):
        return bl
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            # Strip inline comment
            if '#' in line:
                line = line.split('#', 1)[0]
            line = line.strip()
            if not line:
                continue
            token = line.split()[0]
            if _WXID_PATTERN.match(token):
                bl.add(token)
    return bl

_log_lock = threading.Lock()


def log(msg):
    with _log_lock:
        print(msg, flush=True)


def load_contacts(contacts_file):
    with open(contacts_file, 'r') as f:
        data = json.load(f)
    friends = {}
    for c in data.get('contacts', []):
        if c.get('type') != 'friend':
            continue
        u = c.get('username', '')
        if u in BLACKLIST:
            continue
        friends[u] = {
            'username': u,
            'displayName': c.get('displayName', ''),
            'detailDescription': c.get('detailDescription', ''),
            'nickname': c.get('nickname', ''),
            'alias': c.get('alias', ''),
            'labels': c.get('labels', []),
        }
    return friends


def existing_wxids(vault_chatlog):
    if not os.path.exists(vault_chatlog):
        return set()
    return {f[:-3] for f in os.listdir(vault_chatlog) if f.endswith('.md')}


def parse_frontmatter(filepath):
    if not os.path.exists(filepath):
        return None, None
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    if not content.startswith('---'):
        return None, content
    end = content.find('\n---', 3)
    if end == -1:
        end = content.find('---', 3)
        if end == -1:
            return None, content
        body = content[end+3:].lstrip('\n')
    else:
        body = content[end+4:].lstrip('\n')
    fm_text = content[4:end] if content[3] == '\n' else content[3:end]
    fm = {}
    current_key = None
    for line in fm_text.strip().split('\n'):
        # List item continuation
        if current_key and line.strip().startswith('- '):
            val = line.strip()[2:]
            if not isinstance(fm[current_key], list):
                fm[current_key] = []
            fm[current_key].append(val)
            continue
        m = re.match(r'^([^:]+):\s*(.*)', line)
        if m:
            key = m.group(1).strip()
            val = m.group(2).strip()
            fm[key] = val
            current_key = key
    return fm, body


def get_contact_name(c):
    d = c.get('detailDescription') or ''
    first_line = d.split('\n')[0].strip() if d else ''
    return first_line or c.get('nickname') or c.get('displayName') or ''


def clean_detail(d):
    return d.replace('\n', '，') if d else ''


def last_pull_ts(fm):
    s = fm.get('最后拉取时间', '') if fm else ''
    if not s:
        return None
    try:
        return int(datetime.fromisoformat(s).timestamp())
    except Exception:
        return None


def now_iso():
    return datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S+08:00")


def update_identity(wxid, contact, vault_chatlog):
    """Update identity fields in existing file. Returns True if changed."""
    fp = os.path.join(vault_chatlog, f"{wxid}.md")
    fm, body = parse_frontmatter(fp)
    if fm is None:
        return False
    changed = False
    name = get_contact_name(contact)
    new_d = contact.get('displayName', '')
    new_dd = clean_detail(contact.get('detailDescription', ''))
    new_alias = contact.get('alias', '')
    if fm.get('显示名/昵称', '') != new_d:
        log(f"  [ID] {name} 显示名/昵称: '{fm.get('显示名/昵称', '')}' → '{new_d}'")
        fm['显示名/昵称'] = new_d
        changed = True
    if fm.get('微信备注', '') != new_dd:
        log(f"  [ID] {name} 微信备注: '{fm.get('微信备注', '')}' → '{new_dd}'")
        fm['微信备注'] = new_dd
        changed = True
    if fm.get('微信号', '') != new_alias:
        log(f"  [ID] {name} 微信号: '{fm.get('微信号', '')}' → '{new_alias}'")
        fm['微信号'] = new_alias
        changed = True
    # Build expected tags from labels + base wechat tag
    labels = contact.get('labels', [])
    expected_tags = ['wechat'] + [f'wechat-{l}' for l in labels if l.strip()]
    existing_tags = fm.get('tags', '')
    if isinstance(existing_tags, str):
        existing_tags = [t.strip() for t in existing_tags.split(',') if t.strip()]
    if sorted(existing_tags) != sorted(expected_tags):
        log(f"  [ID] {name} 标签: {sorted(existing_tags)} → {sorted(expected_tags)}")
        fm['tags'] = expected_tags
        changed = True
    if changed:
        write_file(fp, fm, body)
    return changed


def write_file(filepath, fm, body):
    lines = []
    for k, v in fm.items():
        if isinstance(v, list):
            lines.append(f"{k}:")
            for item in v:
                lines.append(f"  - {item}")
        else:
            lines.append(f"{k}: {v}")
    new_fm = "---\n" + "\n".join(lines) + "\n---"
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(new_fm + "\n\n" + (body or ''))


def fetch_sessions(weflow_token):
    """Fetch all sessions and return {wxid: lastTimestamp} for private chats only."""
    url = f"{WEFLOW_BASE}/api/v1/sessions?limit=10000&format=chatlab"
    try:
        r = subprocess.run(
            ['curl', '-s', '--connect-timeout', '10', '--max-time', '30',
             '-H', f'Authorization: Bearer {weflow_token}', url],
            capture_output=True, text=True, timeout=35
        )
        data = json.loads(r.stdout)
        if not data.get('success'):
            log(f"  Sessions API error: {data.get('error', r.stdout[:200])}")
            return {}
        sessions = {}
        for s in data.get('sessions', []):
            username = s.get('username', '')
            if '@chatroom' in username or username.startswith('gh_') or username in BLACKLIST:
                continue
            sessions[username] = s.get('lastTimestamp', 0)
        return sessions
    except Exception as e:
        log(f"  Sessions API exception: {e}")
        return {}


PAGE_SIZE = 200


def fetch_messages(wxid, since, weflow_token, max_retries=3):
    """Fetch all messages with offset pagination. Returns merged result or (None, error)."""
    all_msgs = []
    media_info = {"enabled": True, "exportPath": "", "count": 0}
    offset_val = 0

    page = 0
    while True:
        page += 1
        url = f"{WEFLOW_BASE}/api/v1/messages?talker={wxid}&limit={PAGE_SIZE}&offset={offset_val}&media=1&voice=1"
        if since:
            url += f"&start={since}"

        last_err = None
        page_data = None
        for attempt in range(max_retries):
            try:
                r = subprocess.run(
                    ['curl', '-s', '--connect-timeout', '10', '--max-time', '300',
                     '-H', f'Authorization: Bearer {weflow_token}', url],
                    capture_output=True, text=True, timeout=310
                )
                raw = r.stdout.strip()
                if not raw:
                    last_err = "empty response"
                    if attempt < max_retries - 1:
                        time.sleep(2 + random.uniform(0, 2))
                    continue
                data = json.loads(raw)
                if not data.get('success'):
                    return None, f"API: {data.get('error', raw[:200])}"
                page_data = data
                break
            except subprocess.TimeoutExpired:
                last_err = "timeout"
                if attempt < max_retries - 1:
                    time.sleep(1)
                continue
            except json.JSONDecodeError:
                last_err = f"bad JSON: {raw[:100]}"
                if attempt < max_retries - 1:
                    time.sleep(2 + random.uniform(0, 3))
                continue
            except Exception as e:
                last_err = str(e)
                if attempt < max_retries - 1:
                    time.sleep(1)
                continue

        if page_data is None:
            return None, last_err

        msgs = page_data.get('messages', [])
        all_msgs.extend(msgs)
        media_info = page_data.get('media', media_info)

        log(f"  [{wxid}] page {page}: +{len(msgs)} msgs, total={len(all_msgs)}")

        if not page_data.get('hasMore'):
            break

        offset_val += PAGE_SIZE
        time.sleep(0.2)

    # Dedup by serverId (fallback to localId)
    seen = set()
    deduped = []
    for m in all_msgs:
        key = m.get('serverId') or m.get('localId')
        if key not in seen:
            seen.add(key)
            deduped.append(m)

    merged = {
        "success": True,
        "talker": wxid,
        "count": len(deduped),
        "hasMore": False,
        "media": media_info,
        "messages": deduped,
    }
    return merged, None


def has_voice(data):
    return any(m.get('mediaType') == 'voice' for m in data.get('messages', []))


def transcribe_voice_msgs(input_file, asr_config):
    """Run voice transcription via the configured backend.

    asr_config = {
        'backend': 'remote' | 'local',
        'api_key': str (for remote),
        'whisper_url': str (for local),
    }
    Returns (output_path, error_str).
    """
    ts_script = os.path.join(SCRIPT_DIR, 'transcribe_voice.py')
    out = input_file.replace('.json', '_transcribed.json')
    cmd = ['python3', ts_script, '-i', input_file,
           '--backend', asr_config['backend'],
           '--replace-content', '-o', out]
    if asr_config['backend'] == 'remote':
        cmd += ['-k', asr_config['api_key']]
    else:  # local
        cmd += ['--whisper-url', asr_config['whisper_url']]
    r = subprocess.run(
        cmd,
        stdout=None, stderr=subprocess.PIPE, text=True, timeout=600
    )
    if r.returncode != 0:
        return None, r.stderr
    return out, None


def ensure_whisper_server(whisper_url):
    """Call start_whisper_server.sh. Returns (ok, message)."""
    script = os.path.join(SCRIPT_DIR, 'start_whisper_server.sh')
    port = urlparse(whisper_url).port or 8080
    env = os.environ.copy()
    env['WHISPER_PORT'] = str(port)
    try:
        r = subprocess.run(
            ['bash', script], env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        return False, "start_whisper_server.sh timed out after 120s"
    if r.returncode != 0:
        return False, (r.stdout or '').strip().splitlines()[-1] if r.stdout else "unknown error"
    return True, "ok"


def build_frontmatter(contact):
    labels = contact.get('labels', [])
    tag_lines = '\n'.join(['  - wechat'] + [f'  - wechat-{l}' for l in labels if l.strip()])
    dd = clean_detail(contact.get('detailDescription', ''))
    al = contact.get('alias', '')
    return (
        "---\n"
        f"微信ID: {contact['username']}\n"
        f"微信号: {al}\n"
        f"显示名/昵称: {contact['displayName']}\n"
        f"微信备注: {dd}\n"
        f"手机号:\n"
        f"最后拉取时间: {now_iso()}\n"
        "tags:\n"
        f"{tag_lines}\n"
        "---"
    )


def process_one(wxid, contact, exists, vault_chatlog, weflow_token, asr_config, index, total):
    """Process a single friend. Returns result dict. Called from thread pool."""
    name = get_contact_name(contact)
    n_iso = now_iso()
    tag = "incr" if exists else "NEW"
    log(f"[{index}/{total}] [{tag}] {name} ({wxid})")

    # Identity check
    id_changed = False
    if exists:
        id_changed = update_identity(wxid, contact, vault_chatlog)

    # Determine since
    since = None
    if exists:
        fp = os.path.join(vault_chatlog, f"{wxid}.md")
        fm, _ = parse_frontmatter(fp)
        since = last_pull_ts(fm)

    since_label = f"since={since}" if since else "full"
    log(f"  [{wxid}] Fetch ({since_label})...")

    data, err = fetch_messages(wxid, since, weflow_token)
    if err:
        log(f"  [{wxid}] FAIL: {err}")
        return {"wxid": wxid, "name": name, "status": "fail", "error": err,
                "count": 0, "lines": 0, "id_changed": id_changed}

    count = data.get('count', 0)
    if count == 0:
        if exists:
            fp = os.path.join(vault_chatlog, f"{wxid}.md")
            fm, body = parse_frontmatter(fp)
            if fm:
                fm['最后拉取时间'] = n_iso
                write_file(fp, fm, body)
        log(f"  [{wxid}] Skip: no new messages")
        return {"wxid": wxid, "name": name, "status": "skip", "error": "",
                "count": 0, "lines": 0, "id_changed": id_changed}

    # Save raw response
    tmp = f"/tmp/wc_{wxid}.json"
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)

    # Voice transcription (subprocess, serial within this friend)
    input_data = data
    if has_voice(data):
        log(f"  [{wxid}] Transcribing voice ({asr_config['backend']})...")
        tfile, terr = transcribe_voice_msgs(tmp, asr_config)
        if tfile:
            with open(tfile, 'r', encoding='utf-8') as f:
                input_data = json.load(f)
            log(f"  [{wxid}] Voice done")
        else:
            log(f"  [{wxid}] Voice FAIL: {terr[:80] if terr else 'unknown'}")

    # Format using imported function (no subprocess overhead)
    lines = format_messages(input_data, name)
    if not lines:
        log(f"  [{wxid}] Skip: empty after format")
        return {"wxid": wxid, "name": name, "status": "skip", "error": "",
                "count": 0, "lines": 0, "id_changed": id_changed}

    formatted = "\n".join(lines)
    msg_lines = len(lines)

    # Write file
    fp = os.path.join(vault_chatlog, f"{wxid}.md")
    if exists:
        fm, body = parse_frontmatter(fp)
        if fm:
            fm['最后拉取时间'] = n_iso
            full_body = (body + "\n" + formatted) if body else formatted
            write_file(fp, fm, full_body)
    else:
        fm_text = build_frontmatter(contact)
        with open(fp, 'w', encoding='utf-8') as f:
            f.write(fm_text + "\n\n# 微信聊天记录\n\n" + formatted)

    log(f"  [{wxid}] OK: {count} msgs, {msg_lines} lines")
    return {"wxid": wxid, "name": name, "status": "ok", "error": "",
            "count": count, "lines": msg_lines, "id_changed": id_changed}


def main():
    parser = argparse.ArgumentParser(description="Sync WeChat chatlogs to Obsidian vault")
    parser.add_argument('--weflow-token', required=True, help='WeFlow API token')
    parser.add_argument('--asr-backend', choices=['remote', 'local'], default=DEFAULT_ASR_BACKEND,
                        help=f'ASR backend: remote (volcano doubao) or local (whisper-cpp). Default: {DEFAULT_ASR_BACKEND} (read from SKILL.md frontmatter)')
    parser.add_argument('--asr-key',
                        help='ASR API key for remote backend. Required when --asr-backend=remote')
    parser.add_argument('--whisper-url', default=os.environ.get('WHISPER_URL', 'http://127.0.0.1:8080'),
                        help='Whisper-cpp server base URL (used when --asr-backend=local). Default: http://127.0.0.1:8080')
    parser.add_argument('--no-auto-start-whisper', action='store_true',
                        help='Do not call start_whisper_server.sh before transcribing (local backend only)')
    parser.add_argument('--contacts-file', default='/tmp/weflow_contacts.json',
                        help='Path to saved contacts JSON')
    parser.add_argument('--vault-chatlog', required=True,
                        help='Path to vault raw/chatlog directory')
    parser.add_argument('--full', action='store_true',
                        help='Full sync: delete all chatlog files and re-pull everything')
    parser.add_argument('--dry-run', action='store_true',
                        help='Print plan without executing')
    parser.add_argument('--workers', type=int, default=5,
                        help='Number of concurrent friends to process (default: 5)')
    parser.add_argument('--skip-media', nargs='*', default=MEDIA_ACCOUNTS,
                        help='Additional wxids to skip')
    parser.add_argument('--blacklist-file', default=DEFAULT_BLACKLIST_FILE,
                        help=f'Path to user-maintained blacklist file (one wxid per line). Default: {DEFAULT_BLACKLIST_FILE}')
    args = parser.parse_args()

    # Load user blacklist and merge into the built-in BLACKLIST set so all
    # subsequent filtering (load_contacts, to_process filters) honors it.
    user_blacklist = load_user_blacklist(args.blacklist_file)
    BLACKLIST.update(user_blacklist)
    if user_blacklist:
        log(f"Loaded {len(user_blacklist)} wxids from blacklist: {args.blacklist_file}")

    # Validate ASR config
    if args.asr_backend == 'remote':
        args.asr_key = args.asr_key or os.environ.get('ASR_API_KEY')
        if not args.asr_key:
            parser.error('--asr-key is required when --asr-backend=remote (or set ASR_API_KEY env var)')

    asr_config = {
        'backend': args.asr_backend,
        'api_key': args.asr_key or '',
        'whisper_url': args.whisper_url,
    }

    # Auto-start whisper-server for local backend
    if args.asr_backend == 'local' and not args.no_auto_start_whisper:
        log(f"Ensuring whisper-server is up at {args.whisper_url}...")
        ok, msg = ensure_whisper_server(args.whisper_url)
        if ok:
            log(f"  whisper-server ready")
        else:
            log(f"  ERROR: failed to start whisper-server: {msg}")
            log(f"  Tip: start it manually, or re-run with --no-auto-start-whisper after starting it yourself.")
            sys.exit(1)

    log("=== WeChat Chatlog Sync ===")
    log(f"ASR backend: {args.asr_backend}" + (
        f" (whisper @ {args.whisper_url})" if args.asr_backend == 'local' else " (volcano doubao)"
    ))
    friends = load_contacts(args.contacts_file)
    skip = set(args.skip_media) if args.skip_media else set()

    no_chat_friends = 0  # friends with no session record (never chatted)

    if args.full:
        log(f"Mode: FULL (wipe and rebuild) | Workers: {args.workers}")
        log(f"Total friends in contacts: {len(friends)}")

        to_process = [(w, friends[w]) for w in friends if w not in skip]
        existing = set()

        if not args.dry_run:
            import shutil
            if os.path.exists(args.vault_chatlog):
                for f in os.listdir(args.vault_chatlog):
                    if f.endswith('.md'):
                        os.remove(os.path.join(args.vault_chatlog, f))
                log(f"Deleted all chatlog files from {args.vault_chatlog}")
    else:
        log(f"Mode: INCREMENTAL (sessions pre-filter) | Workers: {args.workers}")
        existing = existing_wxids(args.vault_chatlog)

        existing_friends = {w: friends[w] for w in friends
                            if w in existing and w not in skip}
        log(f"Total friends in contacts: {len(friends)}")
        log(f"Existing chatlog files: {len(existing)}")
        log(f"Existing friends to process: {len(existing_friends)}")

        to_process = list(existing_friends.items())

        # ── Identity pre-check: validate all existing friends before sessions filter ──
        log(f"\nValidating identity info for {len(existing_friends)} existing friends...")
        id_update_count = 0
        for wxid, contact in existing_friends.items():
            if update_identity(wxid, contact, args.vault_chatlog):
                id_update_count += 1
        log(f"Identity updates: {id_update_count} friends")

        log("\nFetching sessions to check for new messages...")
        sessions = fetch_sessions(args.weflow_token)
        log(f"Got {len(sessions)} private chat sessions")

        new_friends_count = 0
        if sessions:
            filtered = []
            skipped_no_session = 0
            skipped_no_new = 0
            for wxid, contact in to_process:
                session_ts = sessions.get(wxid)
                if session_ts is None:
                    skipped_no_session += 1
                    continue
                fp = os.path.join(args.vault_chatlog, f"{wxid}.md")
                fm, _ = parse_frontmatter(fp)
                local_ts = last_pull_ts(fm)
                if local_ts is not None and session_ts <= local_ts:
                    skipped_no_new += 1
                    continue
                filtered.append((wxid, contact))
            log(f"After session filter: {len(filtered)} friends with new messages "
                f"(skipped: {skipped_no_new} no new, {skipped_no_session} no session)")

            # Also include new friends: no local file but have a session (chat history)
            for wxid, contact in friends.items():
                if wxid in skip or wxid in existing:
                    continue
                if wxid in sessions:
                    filtered.append((wxid, contact))
                    new_friends_count += 1
                else:
                    no_chat_friends += 1
            if new_friends_count:
                log(f"New friends (no local file, has chat): {new_friends_count}")
            if no_chat_friends:
                log(f"Friends with no chat history (never messaged): {no_chat_friends}")

            to_process = filtered

    if args.dry_run:
        log(f"\n[DRY RUN] Would process {len(to_process)} friends:\n")
        for wxid, contact in to_process:
            name = get_contact_name(contact)
            exists_flag = "incr" if wxid in existing else "NEW"
            log(f"  [{exists_flag}] {name} ({wxid})")
        return

    log(f"\nProcessing {len(to_process)} friends ({args.workers} workers)...\n")

    stats = {"ok": 0, "skip": 0, "fail": 0, "total_msgs": 0, "id_updates": 0}
    failures = []
    total = len(to_process)

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {}
        for i, (wxid, contact) in enumerate(to_process):
            exists = wxid in existing
            f = executor.submit(process_one, wxid, contact, exists,
                               args.vault_chatlog, args.weflow_token, asr_config,
                               i + 1, total)
            futures[f] = (wxid, contact)
            if i < args.workers:
                time.sleep(0.1)

        for future in as_completed(futures):
            result = future.result()
            if result.get("id_changed"):
                stats["id_updates"] += 1
            if result["status"] == "ok":
                stats["ok"] += 1
                stats["total_msgs"] += result.get("count", 0)
            elif result["status"] == "skip":
                stats["skip"] += 1
            else:
                stats["fail"] += 1
                failures.append((result["wxid"], result["name"], result.get("error", "unknown")))

    log("\n" + "=" * 50)
    log("本次拉取汇总：")
    log(f"- 扫描好友：{total} 个")
    log(f"- 新增消息：{stats['total_msgs']} 条（分布在 {stats['ok']} 个好友）")
    log(f"- 跳过（无新消息）：{stats['skip']} 个")
    log(f"- 从未聊过天（无 session）：{no_chat_friends} 个")
    log(f"- 身份信息更新：{stats['id_updates']} 个")
    log(f"- 失败：{stats['fail']} 个")
    for w, n, e in failures:
        log(f"  - {n} ({w}): {e}")


if __name__ == '__main__':
    main()

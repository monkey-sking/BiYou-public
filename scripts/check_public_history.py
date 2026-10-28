#!/usr/bin/env python3
"""Check the staged index or pushed history for runtime data and likely secrets."""
import re
import subprocess
import sys

PRIVATE_FILES = {
    "config/config.yaml", "config/publishing_automation.yaml",
    "config/context.json", "config/resource_doc.json",
    "project_history_record.txt", "core/utils/cookie.py.backup",
    "scripts/fix_fable_cover.py", "flagged_for_review.txt",
    "history_ids.txt", "notified_failures.txt", "pending_result_notifications.txt",
}
PRIVATE_DIRS = ("history/", "logs/", "data/", "core/data/", "analytics/")
SECRET = re.compile(
    rb"(?:\bsk-[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{30,}|"
    rb"\bAQ\.[A-Za-z0-9_-]{40,}|"
    rb"gh[pousr]_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{25,}|"
    rb"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}|"
    rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)"
)
PLACEHOLDER = re.compile(rb"(?i)(your[_-]|example|placeholder|dummy|test[_-]|replace|x{6,}|mock)")


def private_path(path):
    name = path.rsplit("/", 1)[-1].lower()
    return (
        path in PRIVATE_FILES or path.startswith(PRIVATE_DIRS)
        or (("cookie" in name) and ".example." not in name
            and (name.endswith((".json", ".txt", ".bak"))))
        or name == ".env" or name.startswith(".env.")
        or name.endswith((".db", ".sqlite", ".sqlite3", ".pem", ".p12", ".p8"))
    )


def main():
    revisions = sys.argv[1:]
    staged = revisions == ["--staged"]
    if "--staged" in revisions and not staged:
        raise ValueError("--staged cannot be combined with revisions")
    if any(revision.startswith("-") for revision in revisions) and not staged:
        raise ValueError("Expected a revision or --staged")
    paths = {}
    if staged:
        # Read the index, not worktree files: an unsafe staged blob may have
        # already been replaced with safe content on disk.
        raw = subprocess.check_output(["git", "ls-files", "--stage", "-z"])
        for entry in raw.split(b"\0"):
            if not entry:
                continue
            metadata, name = entry.split(b"\t", 1)
            _, sha, stage = metadata.split()
            if stage != b"0":
                raise ValueError("Unmerged index entry")
            path = name.decode(errors="replace")
            if private_path(path):
                print("BLOCKED private staged path:", path, file=sys.stderr)
                return 1
            paths[sha] = path
        revisions = []
    if not revisions and not staged:
        for line in sys.stdin:
            fields = line.split()
            if len(fields) != 4:
                raise ValueError("Invalid pre-push input")
            sha = fields[1]
            if set(sha) != {"0"}:
                revisions.append(sha)
    if not revisions and not staged:
        return 0
    for revision in revisions:
        raw = subprocess.check_output(["git", "rev-list", "--objects", revision])
        for line in raw.splitlines():
            sha, _, name = line.partition(b" ")
            if not name:
                continue
            path = name.decode(errors="replace")
            if private_path(path):
                print("BLOCKED private historical path:", path, file=sys.stderr)
                return 1
            paths[sha] = path
    batch = subprocess.Popen(["git", "cat-file", "--batch"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        for sha, path in paths.items():
            batch.stdin.write(sha + b"\n")
            batch.stdin.flush()
            header = batch.stdout.readline().split()
            if len(header) != 3:
                raise ValueError("Invalid git object response")
            content = batch.stdout.read(int(header[2]))
            batch.stdout.read(1)
            if header[1] == b"blob" and any(
                m.group().startswith(b"AQ.") or not PLACEHOLDER.search(m.group())
                for m in SECRET.finditer(content)
            ):
                print("BLOCKED likely secret in history:", path, file=sys.stderr)
                return 1
    finally:
        batch.stdin.close()
        batch.stdout.close()
        batch.wait()
    label = "Staged index guard" if staged else "Public history guard"
    print(label + " passed:", len(paths), "objects checked")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print("History check failed:", type(error).__name__, file=sys.stderr)
        sys.exit(1)

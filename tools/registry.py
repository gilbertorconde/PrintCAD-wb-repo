#!/usr/bin/env python3
"""The registry's checks and its index, with nothing beyond the standard
library (Python 3.11 or newer).

    registry.py check [--online] [FILE ...]   check entries (all by default)
    registry.py owners --author LOGIN --base REF
                                             check a change touches only
                                             entries its author maintains
    registry.py index [--out index.json]     build the index printCAD reads

`--online` and `index` read each package's latest GitHub release: they
download its `.pcbench`, check it against GitHub's sha256 and read the
`bench.toml` inside. `GITHUB_TOKEN`, when set, is sent with the requests.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGES = ROOT / "packages"

# The index's format; printCAD reads the versions it knows.
SCHEMA = 1

# What printCAD calls this store. A copy of the registry run as a store of
# its own gives it its own name.
NAME = "printCAD workbench registry"

# The workbench contract printCAD speaks: a package built against another
# major version (another minor one before 1.0) does not load.
API = "0.1"

CATEGORIES = {
    "modeling",
    "sketching",
    "parts",
    "fasteners",
    "printing",
    "analysis",
    "import-export",
    "assembly",
    "utilities",
}

REQUIRED = {
    "id": str,
    "name": str,
    "description": str,
    "repository": str,
    "maintainers": list,
    "license": str,
    "categories": list,
}
OPTIONAL = {
    "homepage": str,
    "removed": str,
}

ID = re.compile(r"^[a-z0-9_-]+(\.[a-z0-9_-]+)+$")
REPOSITORY = re.compile(r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$")
LOGIN = re.compile(r"^[A-Za-z0-9-]+$")

# The largest download a check takes, as printCAD's own installer does.
DOWNLOAD_LIMIT = 256 * 1024 * 1024


class Problem(Exception):
    pass


# ------------------------------------------------------------------ entries


def read_entry(path: Path) -> dict:
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise Problem(f"{path.name} does not read as TOML: {e}") from e


def entry_problems(path: Path, entry: dict) -> list[str]:
    """What is wrong with one entry, read alone."""
    problems = []
    for key, kind in REQUIRED.items():
        if key not in entry:
            problems.append(f"`{key}` is missing")
        elif not isinstance(entry[key], kind):
            problems.append(f"`{key}` should be a {kind.__name__}")
    for key, kind in OPTIONAL.items():
        if key in entry and not isinstance(entry[key], kind):
            problems.append(f"`{key}` should be a {kind.__name__}")
    unknown = set(entry) - set(REQUIRED) - set(OPTIONAL)
    if unknown:
        problems.append("unknown fields: " + ", ".join(sorted(unknown)))
    if problems:
        return problems

    if not ID.match(entry["id"]):
        problems.append(
            f"`{entry['id']}` is not a package id: lowercase, reverse-domain, such as `acme.cam`"
        )
    if path.name != f"{entry['id']}.toml":
        problems.append(f"the file should be named {entry['id']}.toml")
    if not REPOSITORY.match(entry["repository"]):
        problems.append("`repository` should be `owner/name` on GitHub")
    if not entry["name"].strip():
        problems.append("`name` is empty")
    description = entry["description"].strip()
    if not description:
        problems.append("`description` is empty")
    elif len(description) > 200:
        problems.append("`description` is longer than 200 characters")
    maintainers = entry["maintainers"]
    if not maintainers:
        problems.append("`maintainers` needs at least one GitHub login")
    for login in maintainers:
        if not isinstance(login, str) or not LOGIN.match(login):
            problems.append(f"`{login}` is not a GitHub login")
    categories = entry["categories"]
    if not categories:
        problems.append("`categories` needs at least one")
    for category in categories:
        if category not in CATEGORIES:
            problems.append(
                f"`{category}` is not a category: " + ", ".join(sorted(CATEGORIES))
            )
    if "homepage" in entry and not entry["homepage"].startswith("https://"):
        problems.append("`homepage` should be an https:// address")
    if "removed" in entry and not entry["removed"].strip():
        problems.append("`removed` should say why")
    return problems


def all_entries() -> list[Path]:
    return sorted(PACKAGES.glob("*.toml"))


def registry_problems(paths: list[Path]) -> list[str]:
    """What is wrong across the entries: ids and repositories listed twice."""
    problems = []
    seen_ids: dict[str, str] = {}
    seen_repos: dict[str, str] = {}
    for path in paths:
        try:
            entry = read_entry(path)
        except Problem:
            continue
        pid, repo = entry.get("id"), str(entry.get("repository", "")).lower()
        if pid in seen_ids:
            problems.append(f"{path.name}: `{pid}` is listed by {seen_ids[pid]} too")
        seen_ids.setdefault(pid, path.name)
        if repo and repo in seen_repos:
            problems.append(f"{path.name}: {repo} is listed by {seen_repos[repo]} too")
        seen_repos.setdefault(repo, path.name)
    return problems


# ------------------------------------------------------------------ GitHub


def request(url: str, accept: str = "application/vnd.github+json"):
    headers = {"Accept": accept, "User-Agent": "printcad-workbench-registry"}
    token = os.environ.get("GITHUB_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {token}"
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60)


def latest_release(repo: str) -> dict:
    try:
        with request(f"https://api.github.com/repos/{repo}/releases/latest") as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise Problem(f"{repo} has no published release") from e
        raise Problem(f"{repo}: GitHub answered {e.code}") from e
    except urllib.error.URLError as e:
        raise Problem(f"{repo}: {e.reason}") from e


def download(url: str) -> bytes:
    try:
        with request(url, accept="application/octet-stream") as r:
            data = r.read(DOWNLOAD_LIMIT + 1)
    except urllib.error.HTTPError as e:
        raise Problem(f"{url}: GitHub answered {e.code}") from e
    except urllib.error.URLError as e:
        raise Problem(f"{url}: {e.reason}") from e
    if len(data) > DOWNLOAD_LIMIT:
        raise Problem(f"{url} is larger than {DOWNLOAD_LIMIT // (1024 * 1024)} MiB")
    return data


def manifest_of(archive: bytes) -> dict:
    """The `bench.toml` a `.pcbench` archive holds, and that it holds the
    component beside it."""
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:*") as tar:
            names = {Path(m.name).as_posix().removeprefix("./") for m in tar.getmembers()}
            if "bench.wasm" not in names:
                raise Problem("the .pcbench holds no bench.wasm")
            member = next(
                (m for m in tar.getmembers() if Path(m.name).as_posix().removeprefix("./") == "bench.toml"),
                None,
            )
            if member is None:
                raise Problem("the .pcbench holds no bench.toml")
            text = tar.extractfile(member).read().decode("utf-8")
    except tarfile.TarError as e:
        raise Problem(f"the .pcbench is not an archive: {e}") from e
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise Problem(f"its bench.toml does not read: {e}") from e


def api_compatible(api: str) -> bool:
    def major(version: str) -> str:
        parts = version.split(".")
        return f"0.{parts[1] if len(parts) > 1 else ''}" if parts[0] == "0" else parts[0]

    return major(api.strip().removeprefix("printcad:workbench@")) == major(API)


def release_of(entry: dict) -> dict:
    """The entry's latest release as printCAD installs it: checked against
    GitHub's digest, its manifest read and matched with the entry."""
    repo = entry["repository"]
    release = latest_release(repo)
    asset = next(
        (a for a in release.get("assets", []) if a.get("name", "").endswith(".pcbench")),
        None,
    )
    if asset is None:
        raise Problem(f"{repo}'s latest release ({release.get('tag_name')}) has no .pcbench file")
    data = download(asset["browser_download_url"])
    sha256 = hashlib.sha256(data).hexdigest()
    digest = asset.get("digest") or ""
    if digest.startswith("sha256:") and digest.removeprefix("sha256:") != sha256:
        raise Problem(f"{asset['name']} does not match GitHub's checksum")
    manifest = manifest_of(data)
    if manifest.get("id") != entry["id"]:
        raise Problem(f"the release holds `{manifest.get('id')}`, the entry lists `{entry['id']}`")
    api = str(manifest.get("api", ""))
    if not api_compatible(api):
        raise Problem(f"the release targets {api}; printCAD speaks printcad:workbench@{API}")
    capabilities = manifest.get("capabilities", {})
    return {
        "tag": release.get("tag_name"),
        "version": manifest.get("version"),
        "api": api,
        "published": release.get("published_at"),
        "page": release.get("html_url"),
        "asset": asset["name"],
        "url": asset["browser_download_url"],
        "size": len(data),
        "sha256": sha256,
        "memory_mb": manifest.get("memory_mb", 1024),
        "capabilities": {
            "save_dialog": bool(capabilities.get("save_dialog", False)),
            "helper": bool(capabilities.get("helper", False)),
            "network": bool(capabilities.get("network", False)),
        },
    }


# ---------------------------------------------------------------- commands


def summary(text: str) -> None:
    """A line for the pull request's checks page, when run there."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text + "\n")


def cmd_check(args) -> int:
    paths = [Path(p) for p in args.files] if args.files else all_entries()
    paths = [p for p in paths if p.suffix == ".toml" and p.exists()]
    failed = False
    for problem in registry_problems(all_entries()):
        print(f"error: {problem}")
        failed = True
    for path in paths:
        entry: dict = {}
        try:
            entry = read_entry(path)
            problems = entry_problems(path, entry)
        except Problem as e:
            problems = [str(e)]
        for problem in problems:
            print(f"error: {path.name}: {problem}")
            failed = True
        if problems or not args.online or "removed" in entry:
            continue
        try:
            release = release_of(entry)
        except Problem as e:
            print(f"error: {path.name}: {e}")
            failed = True
            continue
        asks = [name for name, on in release["capabilities"].items() if on] or ["nothing beyond its folder"]
        line = (
            f"{entry['id']} {release['version']} ({release['tag']}): "
            f"{release['asset']}, {release['size']} bytes, asks for {', '.join(asks)}"
        )
        print(f"ok: {line}")
        summary(f"- {line}")
    if not failed:
        print(f"{len(paths)} entr{'y' if len(paths) == 1 else 'ies'} checked")
    return 1 if failed else 0


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout


def cmd_owners(args) -> int:
    """A change may add an entry its author maintains, and change or remove
    only entries its author maintains already."""
    if args.author in args.admins:
        print(f"{args.author} looks after the registry")
        return 0
    changed = git("diff", "--name-status", f"{args.base}...HEAD", "--", "packages").splitlines()
    failed = False
    for line in changed:
        status, *names = line.split("\t")
        name = names[-1]
        if status.startswith("A"):
            entry = tomllib.loads((ROOT / name).read_text(encoding="utf-8"))
            maintainers = entry.get("maintainers", [])
            if args.author not in maintainers:
                print(f"error: {name}: {args.author} adds it but is not among its maintainers")
                failed = True
            continue
        # Changed, renamed or removed: the maintainers it had decide.
        before = names[0]
        try:
            entry = tomllib.loads(git("show", f"{args.base}:{before}"))
        except subprocess.CalledProcessError:
            continue
        if args.author not in entry.get("maintainers", []):
            print(f"error: {before}: only its maintainers change it ({', '.join(entry.get('maintainers', []))})")
            failed = True
    if not failed:
        print(f"{len(changed)} entr{'y' if len(changed) == 1 else 'ies'} changed by their maintainers")
    return 1 if failed else 0


def cmd_index(args) -> int:
    packages = []
    for path in all_entries():
        entry = read_entry(path)
        problems = entry_problems(path, entry)
        if problems:
            print(f"skipped {path.name}: {'; '.join(problems)}", file=sys.stderr)
            continue
        item = {key: entry[key] for key in [*REQUIRED, *OPTIONAL] if key in entry}
        if "removed" not in entry:
            try:
                item["release"] = release_of(entry)
            except Problem as e:
                # Listed, but nothing to install until it is put right.
                item["release"] = None
                item["problem"] = str(e)
                print(f"{entry['id']}: {e}", file=sys.stderr)
        packages.append(item)
    index = {
        "schema": SCHEMA,
        "name": NAME,
        "api": API,
        "generated": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "packages": packages,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    print(f"{out}: {len(packages)} packages")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="check entries")
    check.add_argument("--online", action="store_true", help="check each package's latest release too")
    check.add_argument("files", nargs="*")
    check.set_defaults(run=cmd_check)
    owners = sub.add_parser("owners", help="check a change touches only its author's entries")
    owners.add_argument("--author", required=True)
    owners.add_argument("--base", required=True)
    owners.add_argument("--admins", nargs="*", default=[])
    owners.set_defaults(run=cmd_owners)
    index = sub.add_parser("index", help="build the index")
    index.add_argument("--out", default="site/index.json")
    index.set_defaults(run=cmd_index)
    args = parser.parse_args()
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())

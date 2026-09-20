"""Stage reviewed paper sources for an update pull request."""

import argparse
import difflib
import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

from research_catalog import load_catalog, paper_sources

ROOT = Path(__file__).resolve().parents[2]
MAX_BYTES = 50 * 1024 * 1024


def download(url, api=False):
    headers = {"User-Agent": "research-paper-sync"}
    if api:
        headers["Accept"] = "application/vnd.github+json"
        headers["X-GitHub-Api-Version"] = "2026-03-10"
        if token := os.environ.get("GH_TOKEN"):
            headers["Authorization"] = f"Bearer {token}"
    with urlopen(Request(url, headers=headers), timeout=60) as response:
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError(f"Download exceeds 50 MiB: {url}")
    return data


def load_sources(directory):
    return paper_sources(load_catalog(directory.parent.parent))


def validate_pdf(path):
    if not path.read_bytes().startswith(b"%PDF-"):
        raise ValueError(f"Not a PDF: {path.name}")
    result = subprocess.run(
        ["pdfinfo", str(path)], capture_output=True, text=True, check=True, timeout=30
    )
    pages = re.search(r"^Pages:\s+(\d+)$", result.stdout, re.MULTILINE)
    if not pages or int(pages[1]) < 1:
        raise ValueError(f"PDF has no readable pages: {path.name}")
    return int(pages[1])


def first_page(path):
    return subprocess.run(
        ["pdftotext", "-f", "1", "-l", "1", str(path), "-"],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout.splitlines()


def sync(directory, *, apply=False):
    sources = load_sources(directory)
    revisions = {}
    changes = []
    report = [
        "Updates from the explicit mapping in `research/catalog.json`.",
        "",
        "Review each PDF before merging, including its authors, date, title, "
        "and the matching research-page title and abstract. "
        "The workflow does not edit the research page or merge this PR.",
    ]
    with tempfile.TemporaryDirectory(prefix="paper-sync-") as temporary:
        for name, source in sources.items():
            repo, ref, path = (source[key] for key in ("repo", "ref", "path"))
            key = (repo, ref)
            if key not in revisions:
                encoded_ref = quote(ref, safe="")
                commit = json.loads(
                    download(
                        f"https://api.github.com/repos/{repo}/commits/{encoded_ref}",
                        True,
                    )
                )["sha"]
                if not re.fullmatch(r"[a-f0-9]{40}", commit):
                    raise ValueError(f"Invalid source commit: {repo}")
                revisions[key] = commit
            commit = revisions[key]
            data = download(f"https://raw.githubusercontent.com/{repo}/{commit}/{path}")
            destination = directory / name
            if data == destination.read_bytes():
                print(f"Unchanged: {name}")
                continue
            staged = Path(temporary) / name
            staged.write_bytes(data)
            new_pages = validate_pdf(staged)
            old_pages = validate_pdf(destination)
            diff = list(
                difflib.unified_diff(
                    first_page(destination),
                    first_page(staged),
                    fromfile="site first page",
                    tofile="source first page",
                    lineterm="",
                )
            )
            excerpt = "\n".join(diff[:100]).replace("```", "'''")
            if len(diff) > 100:
                excerpt += "\n[First-page diff truncated; inspect the full PDF.]"
            report.extend(
                [
                    "",
                    f"### {name}",
                    "",
                    f"Source: [{repo} at {commit[:12]}]"
                    f"(https://github.com/{repo}/blob/{commit}/{path})",
                    f"Pages: {old_pages} → {new_pages}",
                    f"SHA-256: `{hashlib.sha256(data).hexdigest()}`",
                    "",
                    "```diff",
                    excerpt or "First-page text is unchanged; PDF bytes differ.",
                    "```",
                ]
            )
            changes.append((destination, staged))
            print(f"Update available: {name} ({repo}@{commit[:12]})")
        if apply:
            for destination, staged in changes:
                destination.write_bytes(staged.read_bytes())
    if not changes:
        report.extend(["", "All mapped PDFs match their source files."])
    return "\n".join(report) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Update local PDF copies")
    parser.add_argument("--report", type=Path, help="Write the PR body to this path")
    args = parser.parse_args()
    report = sync(ROOT / "research/papers", apply=args.apply)
    if args.report:
        args.report.write_text(report)
    else:
        print(report)


if __name__ == "__main__":
    main()

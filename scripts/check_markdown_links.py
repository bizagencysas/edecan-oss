"""Fail when a repository Markdown file points at a missing local path.

External URLs and in-page anchors are intentionally not fetched: CI remains
offline and deterministic. This catches the common OSS breakage class where a
private planning document or renamed public guide is still linked.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

IGNORED_DIRECTORIES = {".git", ".venv", "node_modules", "target", "build", "dist"}
INLINE_LINK_RE = re.compile(r"!?\[[^\]]*\]\((?P<target>[^)]+)\)")
REFERENCE_LINK_RE = re.compile(r"^\s*\[[^\]]+\]:\s*(?P<target>\S+)", re.MULTILINE)
EXTERNAL_SCHEMES = {"http", "https", "mailto", "tel", "data"}


def markdown_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*.md")
        if not any(part in IGNORED_DIRECTORIES for part in path.relative_to(root).parts)
    )


def normalize_target(raw_target: str) -> str:
    target = raw_target.strip()
    if target.startswith("<") and ">" in target:
        return target[1 : target.index(">")]
    # Markdown permits an optional quoted title after a whitespace separator.
    return target.split(maxsplit=1)[0]


def local_target(raw_target: str) -> str | None:
    target = normalize_target(raw_target)
    if not target or target.startswith("#"):
        return None
    parsed = urlsplit(target)
    if parsed.scheme.lower() in EXTERNAL_SCHEMES or parsed.netloc:
        return None
    return unquote(parsed.path)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    missing: list[str] = []
    for markdown_file in markdown_files(root):
        text = markdown_file.read_text(encoding="utf-8")
        matches = [*INLINE_LINK_RE.finditer(text), *REFERENCE_LINK_RE.finditer(text)]
        for match in matches:
            target = local_target(match.group("target"))
            if target is None:
                continue
            resolved = (markdown_file.parent / target).resolve()
            try:
                resolved.relative_to(root)
            except ValueError:
                missing.append(
                    f"{markdown_file.relative_to(root)}: link escapes repository: {target}"
                )
                continue
            if not resolved.exists():
                line = text.count("\n", 0, match.start()) + 1
                missing.append(f"{markdown_file.relative_to(root)}:{line}: missing {target}")

    if missing:
        print("Broken local Markdown links:", file=sys.stderr)
        print("\n".join(f"- {item}" for item in missing), file=sys.stderr)
        return 1
    print("All local Markdown links resolve.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

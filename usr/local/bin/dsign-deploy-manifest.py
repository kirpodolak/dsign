#!/usr/bin/env python3
"""Deploy manifest engine for dsign-verify-install / dsign-apply-install."""

from __future__ import annotations

import argparse
import filecmp
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

MANIFEST_REL = Path("docs/deploy-manifest.yaml")
DEFAULT_PROJECT_ROOT = Path("/home/dsign/dsign")
DEFAULT_VENV = Path("/home/dsign/venv")
DSIGN_USER = "dsign"


@dataclass
class Entry:
    src: str
    dest: str
    mode: str = "always"
    perm: str = "0644"
    strip_crlf: bool = False
    owner: Optional[str] = None
    group: Optional[str] = None
    post: List[str] = field(default_factory=list)
    note: Optional[str] = None

    @property
    def dest_path(self) -> Path:
        return Path(self.dest)


@dataclass
class EntryStatus:
    entry: Entry
    status: str  # OK | DRIFT | MISSING | SKIP
    detail: str = ""


def _resolve_project_root(explicit: Optional[str] = None) -> Path:
    if explicit:
        return Path(explicit)
    env = os.environ.get("DSIGN_PROJECT_ROOT")
    if env:
        return Path(env)
    if (DEFAULT_PROJECT_ROOT / MANIFEST_REL).is_file():
        return DEFAULT_PROJECT_ROOT
    script = Path(__file__).resolve()
    parts = script.parts
    if len(parts) >= 4 and parts[-3] == "local" and parts[-2] == "bin":
        candidate = Path(*parts[:-3])
        if (candidate / MANIFEST_REL).is_file():
            return candidate
    return DEFAULT_PROJECT_ROOT


def _load_yaml_manifest(path: Path) -> Dict[str, Any]:
    try:
        import yaml  # type: ignore

        with path.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        if not isinstance(data, dict):
            raise ValueError("manifest root must be a mapping")
        return data
    except ImportError:
        return _parse_simple_yaml(path)


def _parse_simple_yaml(path: Path) -> Dict[str, Any]:
    """Minimal YAML reader for deploy-manifest.yaml (no PyYAML required)."""
    text = path.read_text(encoding="utf-8")
    data: Dict[str, Any] = {"entries": []}
    current_key: Optional[str] = None
    in_entries = False

    inline_entry = re.compile(
        r"^\s*-\s*\{\s*src:\s*(?P<src>[^,]+),\s*dest:\s*(?P<dest>[^,]+)"
        r"(?:,\s*mode:\s*(?P<mode>[^,}]+))?"
        r"(?:,\s*perm:\s*(?P<perm>[^,}]+))?"
        r"(?:,\s*strip_crlf:\s*(?P<strip>true|false))?"
        r"(?:,\s*owner:\s*(?P<owner>[^,}]+))?"
        r"(?:,\s*group:\s*(?P<group>[^,}]+))?"
        r"(?:,\s*note:\s*(?P<note>[^}]+))?"
        r"(?:,\s*post:\s*\[(?P<post>[^\]]*)\])?"
        r"\s*\}\s*$"
    )

    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if line.strip() == "entries:":
            in_entries = True
            continue
        if not in_entries:
            m = re.match(r"^(\w+):\s*(.+)$", line.strip())
            if m:
                key, val = m.group(1), m.group(2).strip()
                if val.startswith("[") and val.endswith("]"):
                    inner = val[1:-1].strip()
                    data[key] = [v.strip() for v in inner.split(",") if v.strip()] if inner else []
                else:
                    data[key] = val
            continue
        m = inline_entry.match(line)
        if m:
            entry: Dict[str, Any] = {
                "src": m.group("src").strip(),
                "dest": m.group("dest").strip(),
            }
            if m.group("mode"):
                entry["mode"] = m.group("mode").strip()
            if m.group("perm"):
                entry["perm"] = m.group("perm").strip().strip('"')
            if m.group("strip"):
                entry["strip_crlf"] = m.group("strip").strip() == "true"
            if m.group("owner"):
                entry["owner"] = m.group("owner").strip()
            if m.group("group"):
                entry["group"] = m.group("group").strip()
            if m.group("note"):
                entry["note"] = m.group("note").strip().strip('"')
            if m.group("post"):
                post_raw = m.group("post").strip()
                entry["post"] = [p.strip() for p in post_raw.split(",") if p.strip()]
            data["entries"].append(entry)
    return data


def _parse_entries(raw: Dict[str, Any], groups: Sequence[str]) -> List[Entry]:
    entries: List[Entry] = []
    for item in raw.get("entries") or []:
        if not isinstance(item, dict):
            continue
        group = item.get("group")
        if group and group not in groups:
            continue
        entries.append(
            Entry(
                src=str(item["src"]),
                dest=str(item["dest"]),
                mode=str(item.get("mode", "always")),
                perm=str(item.get("perm", "0644")),
                strip_crlf=bool(item.get("strip_crlf", False)),
                owner=item.get("owner"),
                group=item.get("group"),
                post=list(item.get("post") or []),
                note=item.get("note"),
            )
        )
    return entries


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _normalized_text(path: Path) -> Optional[str]:
    """Text fingerprint for compare; None if file looks binary."""
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in raw:
        return None
    try:
        text = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n").decode("utf-8")
    except UnicodeDecodeError:
        return None
    # Ignore trailing newline / CRLF-only deploy differences.
    return text.rstrip("\n")


def _compare_files(src_path: Path, dest_path: Path, *, strict: bool = False) -> Tuple[str, str]:
    """
    Returns (status, detail).
    status: exact | equivalent | different
    """
    if filecmp.cmp(src_path, dest_path, shallow=False):
        return "exact", ""
    if strict:
        return (
            "different",
            f"sha256 repo={_sha256(src_path)[:12]}… system={_sha256(dest_path)[:12]}…",
        )
    src_norm = _normalized_text(src_path)
    dest_norm = _normalized_text(dest_path)
    if src_norm is not None and src_norm == dest_norm:
        return "equivalent", "content match (differs only: EOF newline and/or CRLF)"
    return (
        "different",
        f"sha256 repo={_sha256(src_path)[:12]}… system={_sha256(dest_path)[:12]}…",
    )


def _check_entry(project_root: Path, entry: Entry, *, strict: bool = False) -> EntryStatus:
    src_path = project_root / entry.src
    dest_path = entry.dest_path

    if entry.mode == "never":
        return EntryStatus(entry, "SKIP", "mode=never")

    if not src_path.is_file():
        return EntryStatus(entry, "MISSING", f"repo missing: {src_path}")

    if not dest_path.is_file():
        return EntryStatus(entry, "MISSING", "not installed on system")

    cmp_status, cmp_detail = _compare_files(src_path, dest_path, strict=strict)
    if cmp_status in ("exact", "equivalent"):
        if cmp_status == "equivalent":
            return EntryStatus(entry, "OK", cmp_detail)
        return EntryStatus(entry, "OK", "")

    return EntryStatus(entry, "DRIFT", cmp_detail)


def _extra_bins(manifest_dests: set[str]) -> List[str]:
  extras: List[str] = []
  for path in sorted(Path("/usr/local/bin").glob("dsign-*")):
      if path.is_file() and str(path) not in manifest_dests:
          extras.append(str(path))
  return extras


def _run(cmd: Sequence[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, text=True, capture_output=True)


def _strip_crlf(path: Path) -> None:
    data = path.read_bytes()
    if b"\r" not in data:
        return
    path.write_bytes(data.replace(b"\r\n", b"\n").replace(b"\r", b"\n"))


def _ensure_newline_eof(path: Path) -> None:
    data = path.read_bytes()
    if not data.endswith(b"\n"):
        path.write_bytes(data + b"\n")


def _visudo_check(path: Path) -> None:
    _ensure_newline_eof(path)
    result = _run(["visudo", "-cf", str(path)], check=False)
    if result.returncode != 0:
        raise RuntimeError(f"visudo rejected {path}: {result.stderr.strip()}")


def _patch_digital_signage_unit(dest: Path) -> None:
    project = _resolve_project_root()
    venv = Path(os.environ.get("DSIGN_VENV", DEFAULT_VENV))
    db_dir = Path(os.environ.get("DSIGN_DB_DIR", "/var/lib/dsign"))
    text = dest.read_text(encoding="utf-8")
    text = re.sub(r"^WorkingDirectory=.*$", f"WorkingDirectory={project}", text, flags=re.M)
    text = re.sub(
        r"^Environment=DSIGN_PROJECT_ROOT=.*$",
        f"Environment=DSIGN_PROJECT_ROOT={project}",
        text,
        flags=re.M,
    )
    text = re.sub(
        r"^ExecStart=.*$",
        f"ExecStart={venv}/bin/python {project}/run.py",
        text,
        flags=re.M,
    )
    text = text.replace("/var/lib/dsign/mpv/socket", f"{db_dir}/mpv/socket")
    dest.write_text(text, encoding="utf-8")


def _connected_dri_card() -> str:
    for card in sorted(Path("/dev/dri").glob("card[0-9]*")):
        base = card.name
        for status in Path("/sys/class/drm").glob(f"{base}-*/status"):
            try:
                if status.read_text(encoding="utf-8").strip() == "connected":
                    return str(card)
            except OSError:
                continue
    for card in sorted(Path("/dev/dri").glob("card[0-9]*")):
        return str(card)
    return "/dev/dri/card0"


def _patch_wayland_env(dest: Path) -> None:
    if not dest.is_file():
        return
    dri = _connected_dri_card()
    lines = dest.read_text(encoding="utf-8").splitlines()
    out: List[str] = []
    for line in lines:
        if line.startswith("WLR_DRM_DEVICES="):
            out.append(f"WLR_DRM_DEVICES={dri}")
        elif line.startswith("WLR_RENDERER=") and "gles2" in line:
            out.append("WLR_RENDERER=pixman")
        else:
            out.append(line)
    dest.write_text("\n".join(out) + "\n", encoding="utf-8")


def _apply_post(entry: Entry, dest: Path) -> None:
    for action in entry.post:
        if action == "visudo":
            _visudo_check(dest)
        elif action == "tmpfiles":
            _run(["systemd-tmpfiles", "--create", str(dest)], check=False)
        elif action == "patch-digital-signage-unit":
            _patch_digital_signage_unit(dest)
        elif action == "patch-wayland-env":
            _patch_wayland_env(dest)
        elif action == "daemon-reload":
            _run(["systemctl", "daemon-reload"], check=False)
        else:
            raise RuntimeError(f"unknown post action: {action}")


def _install_entry(project_root: Path, entry: Entry, dry_run: bool) -> str:
    src_path = project_root / entry.src
    dest_path = entry.dest_path

    if entry.mode == "never":
        return "SKIP"

    if not src_path.is_file():
        return "MISSING_SRC"

    if entry.mode == "if-missing" and dest_path.is_file():
        return "SKIP"

    if dest_path.is_file():
        cmp_status, _ = _compare_files(src_path, dest_path)
        if cmp_status in ("exact", "equivalent"):
            return "OK"

    if dry_run:
        return "WOULD_APPLY"

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_path, dest_path)
    os.chmod(dest_path, int(entry.perm, 8))

    if entry.strip_crlf:
        _strip_crlf(dest_path)

    if entry.owner:
        if ":" in entry.owner:
            user, group = entry.owner.split(":", 1)
            shutil.chown(dest_path, user=user, group=group)
        else:
            shutil.chown(dest_path, user=entry.owner)

    for action in entry.post:
        _apply_post(entry, dest_path)

    return "APPLIED"


def _post_global(actions: Sequence[str], dry_run: bool) -> None:
    if dry_run:
        return
    for action in actions:
        if action == "strip_crlf_bins":
            for path in Path("/usr/local/bin").glob("dsign-*"):
                if path.is_file():
                    _strip_crlf(path)
                    try:
                        os.chmod(path, 0o755)
                    except OSError:
                        pass
        elif action == "daemon-reload":
            _run(["systemctl", "daemon-reload"], check=False)


def cmd_verify(args: argparse.Namespace) -> int:
    project_root = _resolve_project_root(args.project_root)
    manifest_path = project_root / MANIFEST_REL
    if not manifest_path.is_file():
        print(f"ERROR: manifest not found: {manifest_path}", file=sys.stderr)
        return 2

    raw = _load_yaml_manifest(manifest_path)
    entries = _parse_entries(raw, args.groups)
    results = [_check_entry(project_root, e, strict=args.strict) for e in entries]
    extras = _extra_bins({e.dest for e in entries})

    summary = {
        "OK": sum(1 for r in results if r.status == "OK"),
        "DRIFT": sum(1 for r in results if r.status == "DRIFT"),
        "MISSING": sum(1 for r in results if r.status == "MISSING"),
        "SKIP": sum(1 for r in results if r.status == "SKIP"),
        "EXTRA_ON_SYSTEM": len(extras),
    }
    overall = "OK"
    if summary["DRIFT"] or summary["MISSING"] or summary["EXTRA_ON_SYSTEM"]:
        overall = "DRIFT" if summary["DRIFT"] or summary["EXTRA_ON_SYSTEM"] else "MISSING"

    payload = {
        "overall": overall,
        "project_root": str(project_root),
        "manifest": str(manifest_path),
        "summary": summary,
        "entries": [
            {
                "src": r.entry.src,
                "dest": r.entry.dest,
                "status": r.status,
                "detail": r.detail,
                "note": r.entry.note,
            }
            for r in results
            if r.status != "OK" or args.verbose
        ],
        "extra_on_system": extras,
    }

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(f"DSign install verify: {overall}")
        print(f"  project_root: {project_root}")
        print(
            "  summary: "
            f"OK={summary['OK']} DRIFT={summary['DRIFT']} "
            f"MISSING={summary['MISSING']} SKIP={summary['SKIP']} "
            f"EXTRA={summary['EXTRA_ON_SYSTEM']}"
        )
        for r in results:
            if r.status == "OK" and not args.verbose:
                continue
            note = f" ({r.entry.note})" if r.entry.note else ""
            print(f"  [{r.status}] {r.entry.dest}{note}")
            if r.detail:
                print(f"           {r.detail}")
        for extra in extras:
            print(f"  [EXTRA_ON_SYSTEM] {extra}")

    return 0 if overall == "OK" else 1


def cmd_apply(args: argparse.Namespace) -> int:
    if os.geteuid() != 0 and not args.dry_run:
        print("ERROR: dsign-apply-install must run as root (sudo)", file=sys.stderr)
        return 2

    project_root = _resolve_project_root(args.project_root)
    manifest_path = project_root / MANIFEST_REL
    if not manifest_path.is_file():
        print(f"ERROR: manifest not found: {manifest_path}", file=sys.stderr)
        return 2

    raw = _load_yaml_manifest(manifest_path)
    entries = _parse_entries(raw, args.groups)

    if args.only == "drifted":
        statuses = {
            s.entry.dest: s
            for s in (_check_entry(project_root, e, strict=False) for e in entries)
        }
        entries = [e for e in entries if statuses[e.dest].status in ("DRIFT", "MISSING")]

    applied = 0
    for entry in entries:
        result = _install_entry(project_root, entry, args.dry_run)
        if result in ("APPLIED", "WOULD_APPLY"):
            applied += 1
            if not args.json:
                prefix = "would apply" if args.dry_run else "applied"
                print(f"  [{prefix}] {entry.dest}")
        elif args.verbose and not args.json:
            print(f"  [{result}] {entry.dest}")

    _post_global(raw.get("post_global") or [], args.dry_run)

    if args.json:
        print(json.dumps({"applied": applied, "dry_run": args.dry_run}, indent=2))
    elif not args.quiet:
        print(f"done ({applied} file(s) {'would be ' if args.dry_run else ''}updated)")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="DSign deploy manifest tool")
    parser.add_argument("--project-root", help="Override DSIGN_PROJECT_ROOT")
    parser.add_argument(
        "--group",
        action="append",
        dest="groups",
        default=[],
        help="Apply/verify only entries with this group tag (repeatable)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_verify = sub.add_parser("verify", help="Compare repo manifest vs system")
    p_verify.add_argument("--json", action="store_true")
    p_verify.add_argument("-v", "--verbose", action="store_true")
    p_verify.add_argument(
        "--strict",
        action="store_true",
        help="Byte-exact compare (treat EOF newline / CRLF as DRIFT)",
    )
    p_verify.set_defaults(func=cmd_verify)

    p_apply = sub.add_parser("apply", help="Install drifted/missing manifest files")
    p_apply.add_argument("--only", choices=["all", "drifted"], default="all")
    p_apply.add_argument("--dry-run", action="store_true")
    p_apply.add_argument("--json", action="store_true")
    p_apply.add_argument("-v", "--verbose", action="store_true")
    p_apply.add_argument("-q", "--quiet", action="store_true")
    p_apply.set_defaults(func=cmd_apply)

    args = parser.parse_args()
    if not args.groups:
        backend = os.environ.get("DSIGN_DISPLAY_BACKEND", "").strip().lower()
        if backend == "wayland":
            args.groups = ["wayland"]
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

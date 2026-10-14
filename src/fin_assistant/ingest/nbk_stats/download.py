import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .common import (
    _FILE,
    _RUBRIC,
    BASE,
    DELAY_S,
    DOWNLOAD_TRIES,
    INDEX,
    KINDS,
    TIMEOUT_S,
    USER_AGENT,
    NbkStatsError,
)


@dataclass(frozen=True)
class StatFile:
    file_id: str
    title: str
    kind: str | None


def _get(url: str) -> str:
    for attempt in range(1, DOWNLOAD_TRIES + 1):
        r = subprocess.run(
            ["curl", "-sL", "-m", str(TIMEOUT_S), "-A", USER_AGENT, url],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S + 10,
        )
        if r.returncode == 0 and r.stdout:
            return r.stdout
        time.sleep(DELAY_S * 3 * attempt)
    raise NbkStatsError(f"{url}: curl exit {r.returncode} after {DOWNLOAD_TRIES} tries")


def list_files() -> list[StatFile]:
    import html as _html

    rubrics = sorted(set(_RUBRIC.findall(_get(INDEX))))
    out: dict[str, StatFile] = {}
    for rubric in rubrics:
        page = _get(f"{INDEX}/rubrics/{rubric}")
        for file_id, title in _FILE.findall(page):
            title = _html.unescape(title).strip()
            if not title:
                continue
            kind = next((k for k, needle in KINDS.items() if needle in title.lower()), None)
            out.setdefault(file_id, StatFile(file_id, title, kind))
        time.sleep(DELAY_S)
    return list(out.values())


def download(f: StatFile, raw_dir: Path) -> Path:
    raw_dir.mkdir(parents=True, exist_ok=True)
    existing = list(raw_dir.glob(f"{f.kind}_{f.file_id}.*"))
    if existing:
        return existing[0]
    tmp = raw_dir / f"{f.kind}_{f.file_id}.part"
    head = b""
    for attempt in range(1, DOWNLOAD_TRIES + 1):
        r = subprocess.run(
            [
                "curl",
                "-sL",
                "-m",
                str(TIMEOUT_S),
                "-A",
                USER_AGENT,
                "-o",
                str(tmp),
                f"{BASE}/file/download/{f.file_id}",
            ],
            timeout=TIMEOUT_S + 10,
        )
        head = tmp.read_bytes()[:4] if tmp.exists() else b""
        if r.returncode == 0 and head:
            break
        time.sleep(DELAY_S * 3 * attempt)
    if not head:
        tmp.unlink(missing_ok=True)
        raise NbkStatsError(f"download {f.file_id} failed")
    ext = ".xlsx" if head[:2] == b"PK" else ".xls" if head == b"\xd0\xcf\x11\xe0" else None
    if ext is None:
        tmp.unlink(missing_ok=True)
        raise NbkStatsError(f"{f.file_id}: not a spreadsheet")
    target = tmp.with_suffix(ext)
    tmp.rename(target)
    time.sleep(DELAY_S)
    return target


def download_bytes(file_id: str) -> bytes:
    for attempt in range(1, DOWNLOAD_TRIES + 1):
        r = subprocess.run(
            ["curl", "-sL", "-m", str(TIMEOUT_S), "-A", USER_AGENT, f"{BASE}/file/download/{file_id}"],
            capture_output=True,
            timeout=TIMEOUT_S + 10,
        )
        if r.returncode == 0 and r.stdout[:4]:
            time.sleep(DELAY_S)
            return r.stdout
        time.sleep(DELAY_S * 3 * attempt)
    raise NbkStatsError(f"download {file_id} failed")

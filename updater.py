import json
import os
import re
from urllib import error, request


DEFAULT_REPO = os.environ.get("SEP_CLEANER_GITHUB_REPO", "Fery24K/SEP-Cleaner")


def normalize_version(value):
    text = str(value or "0").strip().lower()
    text = re.sub(r"[^0-9.]+", ".", text)
    text = re.sub(r"\.+", ".", text).strip(".")
    if not text:
        return (0,)
    parts = [int(part) for part in text.split(".") if part]
    return tuple(parts) if parts else (0,)


def compare_versions(local_version, remote_version):
    left = normalize_version(local_version)
    right = normalize_version(remote_version)
    max_len = max(len(left), len(right))
    left += (0,) * (max_len - len(left))
    right += (0,) * (max_len - len(right))

    if left < right:
        return -1
    if left > right:
        return 1
    return 0


def fetch_latest_release(repo=DEFAULT_REPO):
    repo = (repo or DEFAULT_REPO).strip()
    if not repo or "/" not in repo:
        return None

    url = f"https://api.github.com/repos/{repo}/releases/latest"
    req = request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "SEP-Cleaner-Updater",
        },
    )

    try:
        with request.urlopen(req, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (error.URLError, ValueError, TimeoutError):
        return None

    tag_name = (payload.get("tag_name") or payload.get("name") or "").strip()
    if not tag_name:
        return None

    html_url = payload.get("html_url") or f"https://github.com/{repo}/releases/latest"
    assets = payload.get("assets") or []
    download_url = None
    for asset in assets:
        if asset.get("browser_download_url"):
            download_url = asset["browser_download_url"]
            break

    body = payload.get("body") or ""
    return {
        "tag_name": tag_name,
        "html_url": html_url,
        "download_url": download_url,
        "body": body,
        "repo": repo,
    }


def check_for_update(current_version, repo=DEFAULT_REPO):
    release = fetch_latest_release(repo)
    if not release:
        return {
            "available": False,
            "current_version": str(current_version),
            "latest_version": None,
            "html_url": None,
            "download_url": None,
            "body": "",
            "error": "Tidak dapat mengecek update dari GitHub.",
        }

    latest_version = release["tag_name"]
    is_available = compare_versions(current_version, latest_version) < 0

    return {
        "available": is_available,
        "current_version": str(current_version),
        "latest_version": latest_version,
        "html_url": release["html_url"],
        "download_url": release["download_url"],
        "body": release["body"],
        "error": "",
    }

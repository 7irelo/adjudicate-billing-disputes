"""Fetch real benchmark documents into corpus/raw/, which is gitignored, and record their hashes."""

import hashlib

import httpx
import yaml

from adjudicate.config import REPO_ROOT

MANIFEST = REPO_ROOT / "corpus" / "manifest.yaml"
RAW = REPO_ROOT / "corpus" / "raw"


def fetch_all() -> dict[str, str]:
    manifest = yaml.safe_load(MANIFEST.read_text("utf-8"))
    RAW.mkdir(exist_ok=True)
    hashes = {}
    for doc in manifest["documents"]:
        response = httpx.get(doc["url"], follow_redirects=True, timeout=60)
        response.raise_for_status()
        (RAW / f"{doc['id']}.pdf").write_bytes(response.content)
        hashes[doc["id"]] = hashlib.sha256(response.content).hexdigest()
    return hashes


if __name__ == "__main__":
    for name, digest in fetch_all().items():
        print(name, digest)

"""Fetch and checksum the original GPVS-Faults CSV bundle and readme."""

import argparse
import hashlib
import json
from pathlib import Path

import requests


API = ("https://data.mendeley.com/public-api/datasets/n76t439f65/files"
       "?version=1&folder_id=root&%24start=0&%24limit=1000")
WANTED = {"CSV_Files.zip", "ReadMe.pdf"}


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(output):
    response = requests.get(API, headers={"Accept": "application/vnd.mendeley-public-dataset.1+json"},
                            timeout=30)
    response.raise_for_status()
    catalog = response.json()
    files = {item["filename"]: item["content_details"] for item in catalog
             if item.get("filename") in WANTED}
    if set(files) != WANTED:
        raise ValueError(f"Missing GPVS-Faults files: {WANTED-set(files)}")
    output.mkdir(parents=True, exist_ok=True)
    manifest = {"source": "https://doi.org/10.17632/n76t439f65.1",
                "license": "CC BY 4.0", "files": {}}
    for name in sorted(WANTED):
        details = files[name]
        path = output / name
        if not (path.exists() and path.stat().st_size == details["size"]
                and sha256(path) == details["sha256_hash"]):
            partial = output / (name + ".part")
            with requests.get(details["download_url"], stream=True, timeout=90) as response:
                response.raise_for_status()
                with partial.open("wb") as stream:
                    for chunk in response.iter_content(4 * 1024 * 1024):
                        stream.write(chunk)
            if partial.stat().st_size != details["size"] or sha256(partial) != details["sha256_hash"]:
                raise ValueError(f"Source checksum failed: {name}")
            partial.replace(path)
        manifest["files"][name] = {"bytes": details["size"],
                                   "sha256": details["sha256_hash"]}
        print(f"verified {name}: {details['size']} bytes", flush=True)
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("data/raw/gpvs_faults"))
    fetch(parser.parse_args().output)

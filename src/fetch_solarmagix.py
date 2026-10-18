"""Download the selected public SolarMagix files and verify source SHA-256."""

import argparse
import hashlib
import json
from pathlib import Path

import requests


API = ("https://data.mendeley.com/public-api/datasets/4zgsckxpdy/files"
       "?version=2&folder_id=root&%24start=0&%24limit=1000")
FILES = {"devices.csv", "stations.csv", "source.csv",
         "hourly_pv_weather_inverter.csv", "hourly_pv_weather_station.csv",
         "inverter_state_canonical.csv", "inverter_state_mapping_fusionsolar.csv",
         "inverter_state_mapping_solis.csv"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(output: Path) -> dict:
    response = requests.get(API, headers={"Accept": "application/vnd.mendeley-public-dataset.1+json"},
                            timeout=30)
    response.raise_for_status()
    catalog = response.json()
    if not isinstance(catalog, list):
        raise ValueError("Mendeley did not return a file catalog")
    selected = {item["filename"]: item for item in catalog if item.get("filename") in FILES}
    if set(selected) != FILES:
        raise ValueError(f"Missing files from source catalog: {FILES-set(selected)}")
    output.mkdir(parents=True, exist_ok=True)
    manifest = {"source": "https://doi.org/10.17632/4zgsckxpdy.2",
                "license": "CC BY 4.0", "files": {}}
    for name in sorted(FILES):
        details = selected[name]["content_details"]
        path = output/name
        if not (path.exists() and path.stat().st_size == details["size"] and
                sha256(path) == details["sha256_hash"]):
            partial = output/(name+".part")
            with requests.get(details["download_url"], stream=True, timeout=90) as download:
                download.raise_for_status()
                with partial.open("wb") as stream:
                    for chunk in download.iter_content(1024*1024):
                        stream.write(chunk)
            if partial.stat().st_size != details["size"] or sha256(partial) != details["sha256_hash"]:
                raise ValueError(f"Source hash/size mismatch: {name}")
            partial.replace(path)
        manifest["files"][name] = {"bytes": details["size"],
                                   "sha256": details["sha256_hash"]}
        print(f"verified {name}: {details['size']} bytes", flush=True)
    (output/"manifest.json").write_text(json.dumps(manifest, indent=2)+"\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("data/raw/solarmagix"))
    args = parser.parse_args()
    fetch(args.output)

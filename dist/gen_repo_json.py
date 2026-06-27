#!/usr/bin/env python3
"""Fill the image fields in dist/repo.json from a built image.

Computes the download (.img.xz) and extracted (.img) sizes + SHA256s and writes
them into the single AL subitem, along with the release URL and date.

Usage:
    gen_repo_json.py --img out.img --xz out.img.xz \
        [--repo wjhrdy/AL] [--tag vX.Y.Z] [--date YYYY-MM-DD] \
        [--repo-json dist/repo.json]

If --tag is given, the url points at that release tag; otherwise it uses the
stable ".../releases/latest/download/AL_arm64.img.xz" URL.
"""
import argparse
import datetime
import hashlib
import json
import os


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--img", required=True, help="uncompressed .img path")
    ap.add_argument("--xz", required=True, help="compressed .img.xz path")
    ap.add_argument("--repo", default="wjhrdy/AL")
    ap.add_argument("--tag", default="")
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    ap.add_argument("--repo-json", default=os.path.join(os.path.dirname(__file__), "repo.json"))
    args = ap.parse_args()

    asset = os.path.basename(args.xz)
    if args.tag:
        url = f"https://github.com/{args.repo}/releases/download/{args.tag}/{asset}"
    else:
        url = f"https://github.com/{args.repo}/releases/latest/download/{asset}"

    with open(args.repo_json) as f:
        data = json.load(f)

    item = data["os_list"][0]["subitems"][0]
    item["url"] = url
    item["release_date"] = args.date
    item["image_download_size"] = os.path.getsize(args.xz)
    item["image_download_sha256"] = sha256(args.xz)
    item["extract_size"] = os.path.getsize(args.img)
    item["extract_sha256"] = sha256(args.img)

    with open(args.repo_json, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    print(f"Updated {args.repo_json}: {asset} "
          f"({item['image_download_size']} bytes, extract {item['extract_size']} bytes)")


if __name__ == "__main__":
    main()

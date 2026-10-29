"""Download an NTSB public docket's flight-recorder material for AERIS replay.

    python scripts/fetch_ntsb_docket.py 105714            # FDR items only
    python scripts/fetch_ntsb_docket.py 105714 106001 --all
    python scripts/fetch_ntsb_docket.py 105714 --list     # show items, fetch nothing

The ProjectID is the number in data.ntsb.gov/Docket?ProjectID=<id>. Items go
to AERIS_ENGINE/data/ntsb/<NTSB number>/ (or $AERIS_NTSB_DIR), zips are
unpacked, and docket_index.json records what the docket listed.

If there's no tests/ntsb_cases/<NTSB number>.json yet, a skeleton is written
with what the CSV header says. Fill in route, QNH and perf_model from the
factual reports, then add expectations — see DCA22LA178.json.

Only dockets that include "FLIGHT DATA RECORDER - ATTACHMENT" CSV data can be
replayed. Many have only the PDF report with plots.
"""

import argparse
import html
import json
import os
import re
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / "AERIS_ENGINE"
BASE = "https://data.ntsb.gov"
_UA = {"User-Agent": "Mozilla/5.0 (AERIS research; NTSB docket fetch)"}

_ROW = re.compile(
    r"<tr>\s*<td><b>(\d+)</b></td>\s*<td>(.*?)</td>.*?href=\"(/Docket/Document/docBLOB\?[^\"]+)\"",
    re.S)


def ntsb_dir() -> Path:
    return Path(os.environ.get("AERIS_NTSB_DIR", ENGINE / "data" / "ntsb"))


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def docket_items(project_id: int) -> tuple[str, list[dict]]:
    page = _get(f"{BASE}/Docket?ProjectID={project_id}").decode("utf-8", "replace")
    m = re.search(r"NTSB Number:</b>\s*([A-Z0-9]+)", page)
    if not m:
        raise RuntimeError(f"ProjectID {project_id}: no NTSB number on the page "
                           f"(docket not public, or the site blocked the request)")
    items = []
    for num, title, href in _ROW.findall(page):
        href = html.unescape(href)
        fname = re.search(r"FileName=([^&]+)", href)
        items.append({
            "item": int(num),
            "title": html.unescape(re.sub(r"<[^>]+>", "", title)).strip(),
            "url": BASE + href,
            "file": urllib.request.unquote(fname.group(1)) if fname else f"item{num}",
        })
    return m.group(1), items


def is_fdr(item: dict) -> bool:
    t = item["title"].upper()
    return "FLIGHT DATA RECORDER" in t or "FDR" in t


def _skeleton(ntsb_id: str, project_id: int, case_dir: Path) -> dict:
    meta = {}
    csvs = sorted(case_dir.glob("*.csv"), key=lambda p: p.stat().st_size, reverse=True)
    if csvs:
        with open(csvs[0], encoding="latin-1") as f:
            for _ in range(12):
                line = f.readline()
                if ":" in line and "," not in line.split(":", 1)[0]:
                    k, _, v = line.partition(":")
                    meta[k.strip()] = v.strip().strip(",").strip()
    return {
        "docket": {
            "ntsb_id": ntsb_id,
            "docket_project_id": project_id,
            "summary": "TODO — from the factual reports",
            "aircraft_from_csv": meta.get("Source of Data", ""),
            "operator": meta.get("Operator", ""),
            "registration": meta.get("Vehicle ID/Registration", ""),
            "perf_model": "TODO — a key from aircraft_performance.list_models()",
            "origin_icao": "",
            "destination_icao": "",
            "qnh_inhg": 29.92,
        },
        "timeline": {},
        "expect": {"alerts": [], "silent": [], "grace": []},
        "known_false_alarms": [],
    }


def fetch(project_id: int, everything: bool, list_only: bool) -> None:
    ntsb_id, items = docket_items(project_id)
    print(f"{project_id} -> {ntsb_id}: {len(items)} items")
    for it in items:
        mark = "*" if is_fdr(it) else " "
        print(f"  {mark} {it['item']:>3}  {it['title']}")
    if list_only:
        return

    wanted = items if everything else [i for i in items if is_fdr(i)]
    if not wanted:
        print("  no flight-recorder items — nothing to replay")
        return

    case_dir = ntsb_dir() / ntsb_id
    case_dir.mkdir(parents=True, exist_ok=True)
    for it in wanted:
        dest = case_dir / it["file"]
        if dest.exists() and dest.stat().st_size > 0:
            print(f"  have  {dest.name}")
        else:
            data = _get(it["url"])
            dest.write_bytes(data)
            print(f"  got   {dest.name}  ({len(data) / 1e6:.1f} MB)")
        if dest.suffix.lower() == ".zip":
            with zipfile.ZipFile(dest) as z:
                for name in z.namelist():
                    target = (case_dir / name).resolve()
                    if case_dir.resolve() not in target.parents:
                        continue            # refuse paths that escape the folder
                    if not target.exists():
                        z.extract(name, case_dir)
                        print(f"  unzip {name}")

    (case_dir / "docket_index.json").write_text(
        json.dumps({"ntsb_id": ntsb_id, "project_id": project_id, "items": items}, indent=2),
        encoding="utf-8")

    fixture = ENGINE / "tests" / "ntsb_cases" / f"{ntsb_id}.json"
    if not fixture.exists():
        fixture.write_text(json.dumps(_skeleton(ntsb_id, project_id, case_dir), indent=2) + "\n",
                           encoding="utf-8")
        print(f"  wrote skeleton {fixture.relative_to(ROOT)} — fill in the TODOs")

    if not list(case_dir.glob("*.csv")):
        print("  note: no CSV in this docket's FDR items — plots only, can't replay")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("project_ids", nargs="+", type=int)
    ap.add_argument("--all", action="store_true", help="download every docket item")
    ap.add_argument("--list", action="store_true", help="list items only")
    args = ap.parse_args()
    for pid in args.project_ids:
        try:
            fetch(pid, args.all, args.list)
        except Exception as exc:
            print(f"{pid}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()

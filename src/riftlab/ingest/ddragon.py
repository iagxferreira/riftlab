"""
ddragon.py — Download and cache all League of Legends static data from Data Dragon.

Downloads:
  - All champions (name, id, numeric key, title, tags, stats, image)
  - All items (name, id, cost, description, tags, build paths, image)
  - All images saved to ddragon/img/champion/ and ddragon/img/item/
  - Exports champions.json, items.json, champions.csv, items.csv

No API key required — Data Dragon is a public CDN.

Usage:
  python -m riftlab.ingest.ddragon fetch          # download everything
  python -m riftlab.ingest.ddragon fetch --patch 16.8.1   # specific patch
  python -m riftlab.ingest.ddragon info           # show current cached version
"""

import json
import csv
import time
import argparse
from pathlib import Path

from riftlab.paths import DDRAGON_DIR, ROOT

import requests
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn
from rich.table import Table
from rich import box

console = Console()

DDRAGON_BASE = "https://ddragon.leagueoflegends.com"
CACHE_DIR    = DDRAGON_DIR
IMG_CHAMP    = CACHE_DIR / "img" / "champion"
IMG_ITEM     = CACHE_DIR / "img" / "item"
IMG_SPELL    = CACHE_DIR / "img" / "spell"


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _get_json(url: str) -> dict | list:
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _download_image(url: str, dest: Path):
    if dest.exists():
        return
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    dest.write_bytes(resp.content)


# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------

def get_latest_version() -> str:
    versions = _get_json(f"{DDRAGON_BASE}/api/versions.json")
    return versions[0]


def get_cached_version() -> str | None:
    f = CACHE_DIR / "version.txt"
    return f.read_text().strip() if f.exists() else None


# ---------------------------------------------------------------------------
# Champions
# ---------------------------------------------------------------------------

def fetch_champions(version: str) -> dict:
    console.print(f"[dim]Fetching champion data for {version}...[/dim]")
    data = _get_json(f"{DDRAGON_BASE}/cdn/{version}/data/en_US/champion.json")
    return data["data"]


def download_champion_images(champions: dict, version: str):
    IMG_CHAMP.mkdir(parents=True, exist_ok=True)
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Champion images", total=len(champions))
        for champ_id, champ in champions.items():
            img_file = champ["image"]["full"]
            url  = f"{DDRAGON_BASE}/cdn/{version}/img/champion/{img_file}"
            dest = IMG_CHAMP / img_file
            try:
                _download_image(url, dest)
            except Exception as e:
                console.print(f"[yellow]Skip {champ_id}: {e}[/yellow]")
            progress.advance(task)


def build_champions_records(champions: dict, version: str) -> list[dict]:
    records = []
    for champ_id, c in champions.items():
        stats = c.get("stats", {})
        records.append({
            "id":          champ_id,
            "key":         c["key"],          # numeric ID used in match API
            "name":        c["name"],
            "title":       c["title"],
            "tags":        "|".join(c.get("tags", [])),
            "resource":    c.get("partype", ""),
            "hp":          stats.get("hp", ""),
            "mp":          stats.get("mp", ""),
            "movespeed":   stats.get("movespeed", ""),
            "armor":       stats.get("armor", ""),
            "spellblock":  stats.get("spellblock", ""),
            "attackrange": stats.get("attackrange", ""),
            "ad":          stats.get("attackdamage", ""),
            "as":          stats.get("attackspeed", ""),
            "image":       c["image"]["full"],
            "image_path":  str((IMG_CHAMP / c["image"]["full"]).relative_to(ROOT)),
            "version":     version,
        })
    return sorted(records, key=lambda x: x["name"])


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------

def fetch_items(version: str) -> dict:
    console.print(f"[dim]Fetching item data for {version}...[/dim]")
    data = _get_json(f"{DDRAGON_BASE}/cdn/{version}/data/en_US/item.json")
    return data["data"]


def download_item_images(items: dict, version: str):
    IMG_ITEM.mkdir(parents=True, exist_ok=True)
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Item images", total=len(items))
        for item_id, item in items.items():
            img_file = item["image"]["full"]
            url  = f"{DDRAGON_BASE}/cdn/{version}/img/item/{img_file}"
            dest = IMG_ITEM / img_file
            try:
                _download_image(url, dest)
            except Exception as e:
                console.print(f"[yellow]Skip item {item_id}: {e}[/yellow]")
            progress.advance(task)


def build_items_records(items: dict, version: str) -> list[dict]:
    records = []
    for item_id, item in items.items():
        gold   = item.get("gold", {})
        stats  = item.get("stats", {})
        maps   = item.get("maps", {})
        # Only include items available on Summoner's Rift (map 11)
        if not maps.get("11", True):
            continue
        # Flatten stats dict — keys like FlatMagicDamageMod → ap, etc.
        STAT_MAP = {
            "FlatMagicDamageMod":         "ap",
            "FlatPhysicalDamageMod":      "ad",
            "FlatArmorMod":               "armor",
            "FlatSpellBlockMod":          "mr",
            "FlatHPPoolMod":              "hp",
            "FlatMPPoolMod":              "mana",
            "PercentAttackSpeedMod":      "as_pct",
            "FlatCritChanceMod":          "crit",
            "FlatMovementSpeedMod":       "ms",
            "PercentMovementSpeedMod":    "ms_pct",
            "FlatHPRegenMod":             "hp_regen",
            "PercentLifeStealMod":        "lifesteal",
        }
        stat_vals = {v: "" for v in STAT_MAP.values()}
        for raw_key, val in stats.items():
            mapped = STAT_MAP.get(raw_key)
            if mapped:
                stat_vals[mapped] = round(val * 100, 1) if "pct" in mapped or mapped in ("crit", "lifesteal") else int(val)

        records.append({
            "id":            item_id,
            "name":          item.get("name", ""),
            "description":   item.get("plaintext", ""),
            "full_desc":     item.get("description", "").replace("<br>", " ").replace("\n", " "),
            "cost_total":    gold.get("total", 0),
            "cost_base":     gold.get("base", 0),
            "sell":          gold.get("sell", 0),
            "purchasable":   int(gold.get("purchasable", True)),
            "tags":          "|".join(item.get("tags", [])),
            "builds_from":   "|".join(item.get("from", [])),
            "builds_into":   "|".join(item.get("into", [])),
            "depth":         item.get("depth", 1),
            # Flat stats
            **stat_vals,
            "image":         item["image"]["full"],
            "image_path":    str((IMG_ITEM / item["image"]["full"]).relative_to(ROOT)),
            "version":       version,
        })
    return sorted(records, key=lambda x: int(x["cost_total"]), reverse=True)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_json(records: list[dict], path: Path):
    path.write_text(json.dumps(records, indent=2, ensure_ascii=False))
    console.print(f"[green]Saved {len(records)} records → {path}[/green]")


def export_csv_file(records: list[dict], path: Path):
    if not records:
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=records[0].keys())
        writer.writeheader()
        writer.writerows(records)
    console.print(f"[green]Saved {len(records)} rows → {path}[/green]")


# ---------------------------------------------------------------------------
# Info display
# ---------------------------------------------------------------------------

def cmd_info():
    version = get_cached_version()
    if not version:
        console.print("[yellow]No cached data — run: python -m riftlab.ingest.ddragon fetch[/yellow]")
        return

    champ_file = CACHE_DIR / "champions.json"
    item_file  = CACHE_DIR / "items.json"

    n_champs = len(json.loads(champ_file.read_text())) if champ_file.exists() else 0
    n_items  = len(json.loads(item_file.read_text()))  if item_file.exists()  else 0
    n_cimgs  = len(list(IMG_CHAMP.glob("*.png")))      if IMG_CHAMP.exists()  else 0
    n_iimgs  = len(list(IMG_ITEM.glob("*.png")))       if IMG_ITEM.exists()   else 0

    t = Table(box=box.SIMPLE)
    t.add_column("Resource"); t.add_column("Count", justify="right")
    t.add_row("Patch",            version)
    t.add_row("Champions (JSON)", str(n_champs))
    t.add_row("Items (JSON)",     str(n_items))
    t.add_row("Champion images",  str(n_cimgs))
    t.add_row("Item images",      str(n_iimgs))
    console.print(t)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Data Dragon static data downloader")
    sub = parser.add_subparsers(dest="cmd")

    p_fetch = sub.add_parser("fetch", help="Download all static data and images")
    p_fetch.add_argument("--patch", default=None, help="Specific patch version (default: latest)")

    sub.add_parser("info", help="Show cached data info")

    args = parser.parse_args()

    if args.cmd == "fetch":
        CACHE_DIR.mkdir(exist_ok=True)

        version = args.patch or get_latest_version()
        console.print(f"\n[bold cyan]Data Dragon — patch {version}[/bold cyan]\n")

        # Champions
        champions = fetch_champions(version)
        champ_records = build_champions_records(champions, version)
        download_champion_images(champions, version)
        export_json(champ_records, CACHE_DIR / "champions.json")
        export_csv_file(champ_records, CACHE_DIR / "champions.csv")

        # Items
        items = fetch_items(version)
        item_records = build_items_records(items, version)
        download_item_images(items, version)
        export_json(item_records, CACHE_DIR / "items.json")
        export_csv_file(item_records, CACHE_DIR / "items.csv")

        # Save version
        (CACHE_DIR / "version.txt").write_text(version)

        console.print(f"\n[bold green]Done! All data cached in ./ddragon/[/bold green]")
        cmd_info()

    elif args.cmd == "info":
        cmd_info()

    else:
        parser.print_help()


if __name__ == "__main__":
    main()

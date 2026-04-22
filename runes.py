"""
runes.py — Recommended rune pages based on your champion and enemy comp.

Usage:
  python runes.py main           # pull from live game
  python runes.py main --last    # pull from last game
  python runes.py Rakan --enemy "Nautilus,Caitlyn,Yasuo,Zed,Viktor"  # manual
"""

import sys
import argparse
import time

import requests
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.rule import Rule
from rich import box

from lol_stats import get_account, get_match_ids, get_match, extract_participant, BASE_SUMMONER, _get, ACCOUNTS
from comp_check import analyze_comp, resolve, champ_name_from_id, _load_champ_id_map

load_dotenv()
console = Console()

# ---------------------------------------------------------------------------
# Rune definitions (ID + metadata)
# ---------------------------------------------------------------------------

RUNES = {
    # Resolve
    8437: dict(name="Grasp of the Undying", tree="Resolve",     slot="keystone"),
    8439: dict(name="Aftershock",           tree="Resolve",     slot="keystone"),
    8465: dict(name="Guardian",             tree="Resolve",     slot="keystone"),
    8446: dict(name="Demolish",             tree="Resolve",     slot="row1"),
    8463: dict(name="Font of Life",         tree="Resolve",     slot="row1"),
    8401: dict(name="Shield Bash",          tree="Resolve",     slot="row1"),
    8429: dict(name="Conditioning",         tree="Resolve",     slot="row2"),
    8444: dict(name="Second Wind",          tree="Resolve",     slot="row2"),
    8473: dict(name="Bone Plating",         tree="Resolve",     slot="row2"),
    8451: dict(name="Overgrowth",           tree="Resolve",     slot="row3"),
    8453: dict(name="Revitalize",           tree="Resolve",     slot="row3"),
    8242: dict(name="Unflinching",          tree="Resolve",     slot="row3"),
    # Sorcery
    8214: dict(name="Summon Aery",          tree="Sorcery",     slot="keystone"),
    8229: dict(name="Arcane Comet",         tree="Sorcery",     slot="keystone"),
    8230: dict(name="Phase Rush",           tree="Sorcery",     slot="keystone"),
    8226: dict(name="Manaflow Band",        tree="Sorcery",     slot="row1"),
    8275: dict(name="Nimbus Cloak",         tree="Sorcery",     slot="row1"),
    8224: dict(name="Axiom Arcanist",       tree="Sorcery",     slot="row1"),
    8210: dict(name="Transcendence",        tree="Sorcery",     slot="row2"),
    8234: dict(name="Celerity",             tree="Sorcery",     slot="row2"),
    8233: dict(name="Absolute Focus",       tree="Sorcery",     slot="row2"),
    8237: dict(name="Scorch",               tree="Sorcery",     slot="row3"),
    8232: dict(name="Waterwalking",         tree="Sorcery",     slot="row3"),
    8236: dict(name="Gathering Storm",      tree="Sorcery",     slot="row3"),
    # Inspiration
    8351: dict(name="Glacial Augment",      tree="Inspiration", slot="keystone"),
    8360: dict(name="Unsealed Spellbook",   tree="Inspiration", slot="keystone"),
    8369: dict(name="First Strike",         tree="Inspiration", slot="keystone"),
    8306: dict(name="Hextech Flashtraption",tree="Inspiration", slot="row1"),
    8304: dict(name="Magical Footwear",     tree="Inspiration", slot="row1"),
    8321: dict(name="Cash Back",            tree="Inspiration", slot="row1"),
    8313: dict(name="Triple Tonic",         tree="Inspiration", slot="row2"),
    8352: dict(name="Time Warp Tonic",      tree="Inspiration", slot="row2"),
    8345: dict(name="Biscuit Delivery",     tree="Inspiration", slot="row2"),
    8347: dict(name="Cosmic Insight",       tree="Inspiration", slot="row3"),
    8410: dict(name="Approach Velocity",    tree="Inspiration", slot="row3"),
    8316: dict(name="Jack Of All Trades",   tree="Inspiration", slot="row3"),
    # Precision
    8005: dict(name="Press the Attack",     tree="Precision",   slot="keystone"),
    8008: dict(name="Lethal Tempo",         tree="Precision",   slot="keystone"),
    8021: dict(name="Fleet Footwork",       tree="Precision",   slot="keystone"),
    8010: dict(name="Conqueror",            tree="Precision",   slot="keystone"),
    9101: dict(name="Absorb Life",          tree="Precision",   slot="row1"),
    9111: dict(name="Triumph",              tree="Precision",   slot="row1"),
    8009: dict(name="Presence of Mind",     tree="Precision",   slot="row1"),
    9104: dict(name="Legend: Alacrity",     tree="Precision",   slot="row2"),
    9105: dict(name="Legend: Haste",        tree="Precision",   slot="row2"),
    9103: dict(name="Legend: Bloodline",    tree="Precision",   slot="row2"),
    8014: dict(name="Coup de Grace",        tree="Precision",   slot="row3"),
    8017: dict(name="Cut Down",             tree="Precision",   slot="row3"),
    8299: dict(name="Last Stand",           tree="Precision",   slot="row3"),
    # Domination
    8112: dict(name="Electrocute",          tree="Domination",  slot="keystone"),
    8128: dict(name="Dark Harvest",         tree="Domination",  slot="keystone"),
    9923: dict(name="Hail of Blades",       tree="Domination",  slot="keystone"),
    8126: dict(name="Cheap Shot",           tree="Domination",  slot="row1"),
    8139: dict(name="Taste of Blood",       tree="Domination",  slot="row1"),
    8143: dict(name="Sudden Impact",        tree="Domination",  slot="row1"),
    8137: dict(name="Sixth Sense",          tree="Domination",  slot="row2"),
    8140: dict(name="Grisly Mementos",      tree="Domination",  slot="row2"),
    8141: dict(name="Deep Ward",            tree="Domination",  slot="row2"),
    8135: dict(name="Treasure Hunter",      tree="Domination",  slot="row3"),
    8105: dict(name="Relentless Hunter",    tree="Domination",  slot="row3"),
    8106: dict(name="Ultimate Hunter",      tree="Domination",  slot="row3"),
}

# ---------------------------------------------------------------------------
# Rune page database — keyed by champion + scenario
# ---------------------------------------------------------------------------

# Each page: primary tree, keystone, rows 1-3, secondary tree, 2 secondaries, shards
# shards: [offense, flex, defense] — common values: "AS"/"AD"/"AP"/"AH"/"Armor"/"MR"/"HP"

RUNE_PAGES: dict[str, list[dict]] = {
    "Rakan": [
        dict(
            name="Standard Engage",
            scenario="Default page — use in most games",
            conditions=["always"],
            anti=[],
            primary="Resolve",
            keystone=8439,    # Aftershock
            row1=8463,        # Font of Life
            row2=8473,        # Bone Plating
            row3=8453,        # Revitalize
            secondary="Sorcery",
            sec1=8234,        # Celerity
            sec2=8232,        # Waterwalking
            shards=["AH", "AH", "Armor"],
            why={
                "keystone": "Aftershock — massive armor/MR buff when you land W, lets you survive inside the enemy team after R",
                "row1":     "Font of Life — heals your ADC every time you CC an enemy, constant passive sustain",
                "row2":     "Bone Plating — reduces burst from the first 3 hits after you engage, keeps you alive",
                "row3":     "Revitalize — amplifies all heals and shields including your W and Redemption",
                "secondary":"Sorcery for extra mobility",
                "sec1":     "Celerity — more speed = more W range, more engage opportunities",
                "sec2":     "Waterwalking — speed in river is free roam power on Rakan",
                "shards":   "Double AH for more W/R casts, Armor to survive the early lane",
            }
        ),
        dict(
            name="Guardian (Peel ADC)",
            scenario="ADC is Vayne, Ezreal, Jhin or other peel-dependent carries",
            conditions=["peel_adc"],
            anti=[],
            primary="Resolve",
            keystone=8465,    # Guardian
            row1=8463,        # Font of Life
            row2=8473,        # Bone Plating
            row3=8453,        # Revitalize
            secondary="Inspiration",
            sec1=8345,        # Biscuit Delivery
            sec2=8347,        # Cosmic Insight
            shards=["AH", "AH", "Armor"],
            why={
                "keystone": "Guardian — shared shield with your ADC when you're near them, protects them from burst without you having to react",
                "row1":     "Font of Life — heals your ADC passively when you peel for them",
                "row2":     "Bone Plating — reduces the burst that hits your ADC when you body-block",
                "row3":     "Revitalize — amplifies Guardian shield and all heals",
                "secondary":"Inspiration for lane sustain and CDR",
                "sec1":     "Biscuit Delivery — sustain through the poke lanes that bully peel ADCs",
                "sec2":     "Cosmic Insight — lower summoner spell CD means more Flash/Ignite plays",
                "shards":   "Double AH to peel faster, Armor for laning phase",
            }
        ),
        dict(
            name="Aftershock + Inspiration (Long game)",
            scenario="Enemy has heavy CC chain or game will go late (Seraphine, Sona, Azir comps)",
            conditions=["heavy_cc_enemy", "late_game_enemy"],
            anti=[],
            primary="Resolve",
            keystone=8439,    # Aftershock
            row1=8463,        # Font of Life
            row2=8444,        # Second Wind
            row3=8453,        # Revitalize
            secondary="Inspiration",
            sec1=8345,        # Biscuit Delivery
            sec2=8347,        # Cosmic Insight
            shards=["AH", "AH", "MR"],
            why={
                "keystone": "Aftershock — survive inside their peel/CC long enough for your team to follow",
                "row1":     "Font of Life — passive healing even in poke-heavy games",
                "row2":     "Second Wind — sustain through poke in long laning phases",
                "row3":     "Revitalize — amplifies Second Wind and all heals/shields",
                "secondary":"Inspiration for extra survivability over time",
                "sec1":     "Biscuit Delivery — sustain through long poky laning phases",
                "sec2":     "Cosmic Insight — lower CD on Shurelya's and Knight's Vow actives",
                "shards":   "Double AH, MR instead of Armor vs magic-heavy poke",
            }
        ),
        dict(
            name="Summon Aery (Aggressive lane)",
            scenario="Fighting ADC (Draven, Samira, Jinx) — look to dominate early",
            conditions=["fighting_adc"],
            anti=["heavy_cc_enemy"],
            primary="Sorcery",
            keystone=8214,    # Summon Aery
            row1=8226,        # Manaflow Band
            row2=8234,        # Celerity
            row3=8237,        # Scorch
            secondary="Resolve",
            sec1=8473,        # Bone Plating
            sec2=8453,        # Revitalize
            shards=["AH", "AH", "Armor"],
            why={
                "keystone": "Summon Aery — pokes with every W and auto, extra damage in early trades and also shields allies",
                "row1":     "Manaflow Band — restores mana when you poke, keeps you from going oom in aggressive early games",
                "row2":     "Celerity — more movement speed = better W positioning in lane",
                "row3":     "Scorch — extra burn damage on your poke, strong in early all-ins",
                "secondary":"Resolve for durability after you engage",
                "sec1":     "Bone Plating — survive the first burst from their ADC after diving",
                "sec2":     "Revitalize — amplifies W shield value",
                "shards":   "Double AH for more W pokes, Armor to trade in lane",
            }
        ),
    ],

    "Ekko": [
        dict(
            name="Electrocute (Standard burst)",
            scenario="Default — burst pattern Q-E-W, kill before they react",
            conditions=["always"],
            anti=["heavy_cc_enemy"],
            primary="Domination",
            keystone=8112,    # Electrocute
            row1=8143,        # Sudden Impact — E dash procs it every engage
            row2=8137,        # Sixth Sense
            row3=8135,        # Treasure Hunter
            secondary="Sorcery",
            sec1=8210,        # Transcendence
            sec2=8237,        # Scorch
            shards=["AP", "AH", "Armor"],
            why={
                "keystone": "Electrocute — 3-hit proc lines up perfectly with Q-auto-E combo, bursts squishies in one rotation",
                "row1":     "Sudden Impact — every E dash gives 10 lethality + 9 magic pen for free, massive burst damage",
                "row2":     "Sixth Sense — vision on low HP enemies helps you find solo kills in fog of war",
                "row3":     "Treasure Hunter — gold on first kill in each bounty = faster item spike",
                "secondary":"Sorcery for CDR and early lane damage",
                "sec1":     "Transcendence — AH cap overflow → extra ability power, more R casts",
                "sec2":     "Scorch — early burn on Q chip, punishes enemies who don't respect level 3",
                "shards":   "AP shard for W burst scaling, AH for more rotations, Armor vs AD lane",
            }
        ),
        dict(
            name="Dark Harvest (Snowball/Late)",
            scenario="Enemy has tanks or game is expected to go late — scales harder than Electrocute",
            conditions=["late_game_enemy"],
            anti=["peel_adc"],  # Lulu/Janna protect their carry — need Electrocute burst, not ramp-up
            primary="Domination",
            keystone=8128,    # Dark Harvest
            row1=8143,        # Sudden Impact
            row2=8137,        # Sixth Sense
            row3=8135,        # Treasure Hunter
            secondary="Sorcery",
            sec1=8210,        # Transcendence
            sec2=8236,        # Gathering Storm
            shards=["AP", "AH", "Armor"],
            why={
                "keystone": "Dark Harvest — each kill/assist stacks it, by mid game one-shots without needing full combo",
                "row1":     "Sudden Impact — E dash magic pen procs on every engage, synergizes with Dark Harvest burst",
                "row2":     "Sixth Sense — find low HP enemies to stack Dark Harvest faster",
                "row3":     "Treasure Hunter — gold advantage lets you hit 2 items before enemy carries",
                "secondary":"Sorcery for AP scaling",
                "sec1":     "Transcendence — AH overflow → AP, more R uses late game",
                "sec2":     "Gathering Storm — free AP every 10 min, W bubble damage scales extremely hard",
                "shards":   "AP for scaling burst, AH for rotations, Armor vs physical lane",
            }
        ),
        dict(
            name="Phase Rush (vs CC)",
            scenario="Enemy has heavy CC chain (Lissandra, Leona, Nautilus) — need escape after W lands",
            conditions=["heavy_cc_enemy"],
            anti=[],
            primary="Sorcery",
            keystone=8230,    # Phase Rush
            row1=8226,        # Manaflow Band
            row2=8210,        # Transcendence
            row3=8236,        # Gathering Storm
            secondary="Domination",
            sec1=8143,        # Sudden Impact
            sec2=8135,        # Treasure Hunter
            shards=["AP", "AH", "Armor"],
            why={
                "keystone": "Phase Rush — 3 quick hits give 30-40% MS, lets you dive in, proc W stun, then sprint out before CC lands",
                "row1":     "Manaflow Band — mana sustain for longer games where you can't just one-shot",
                "row2":     "Transcendence — extra AH for more W/R casts in extended fights",
                "row3":     "Gathering Storm — AP scaling because you'll be playing safe early vs their CC",
                "secondary":"Domination for magic pen on your engages",
                "sec1":     "Sudden Impact — E dash still procs magic pen even when playing defensively",
                "sec2":     "Treasure Hunter — accelerate item spike to hit Phase Rush + AP breakpoint faster",
                "shards":   "AP for W bubble damage, AH for R cooldown, Armor vs physical CC",
            }
        ),
    ],

    "Draven": [
        dict(
            name="Lethal Tempo (Standard)",
            scenario="Default — attack speed lets you spin more axes and stack Adoration faster",
            conditions=["always"],
            anti=["heavy_cc_enemy"],
            primary="Precision",
            keystone=8008,    # Lethal Tempo
            row1=9101,        # Overheal
            row2=9104,        # Legend: Alacrity
            row3=8299,        # Cut Down
            secondary="Domination",
            sec1=8126,        # Cheap Shot
            sec2=8135,        # Treasure Hunter
            shards=["AD", "AS", "Armor"],
            why={
                "keystone": "Lethal Tempo — stacks to 60% bonus AS; more attacks = more axes caught = more Adoration stacks = bigger ult payoff",
                "row1":     "Overheal — excess heal converts to shield; keeps you alive through poke while you build stacks",
                "row2":     "Legend: Alacrity — permanent AS from assists, compounds with Lethal Tempo for even faster axe tempo",
                "row3":     "Cut Down — most targets have more HP than you; 8% bonus damage on them is free DPS on every axe",
                "secondary":"Domination for extra damage",
                "sec1":     "Cheap Shot — any CC in your kit procs true damage; free damage on every slowed target",
                "sec2":     "Treasure Hunter — Draven snowballs hard; extra gold from first kill accelerates your first item",
                "shards":   "AD for axe burst, AS for faster stacking, Armor vs bot lane poke",
            }
        ),
        dict(
            name="Hail of Blades (vs poke/dive)",
            scenario="Enemy has dive or you want early kill pressure — burst 3 attacks instantly",
            conditions=["heavy_cc_enemy"],
            anti=[],
            primary="Domination",
            keystone=9923,    # Hail of Blades
            row1=8126,        # Cheap Shot
            row2=8138,        # Eyeball Collection
            row3=8135,        # Treasure Hunter
            secondary="Precision",
            sec1=9101,        # Overheal
            sec2=9104,        # Legend: Alacrity
            shards=["AD", "AS", "Armor"],
            why={
                "keystone": "Hail of Blades — 3 instant attacks on engage procs both axes fast; level 2 all-in with Draven E+Q is a guaranteed kill",
                "row1":     "Cheap Shot — E slow procs true damage on every engage, free burst in the kill combo",
                "row2":     "Eyeball Collection — permanent AD stacking from kills, Draven is a kill-snowball champion",
                "row3":     "Treasure Hunter — extra gold from snowball kills; Draven's passive gives gold on kills so this amplifies it",
                "secondary":"Precision for sustain and AS",
                "sec1":     "Overheal — sustain between trades while you look for all-in windows",
                "sec2":     "Legend: Alacrity — AS scaling for mid/late when Hail of Blades becomes less relevant",
                "shards":   "AD for level 2 burst, AS for proc speed, Armor vs physical bot lane",
            }
        ),
    ],

    "Caitlyn": [
        dict(
            name="Lethal Tempo (Standard)",
            scenario="Default — attack speed stacks let you fire faster, headshots come more often",
            conditions=["always"],
            anti=["heavy_cc_enemy"],
            primary="Precision",
            keystone=8008,    # Lethal Tempo
            row1=9101,        # Overheal
            row2=9104,        # Legend: Alacrity
            row3=8299,        # Cut Down — punishes high-HP targets
            secondary="Domination",
            sec1=8126,        # Cheap Shot — trap immobilize procs it for free
            sec2=8135,        # Treasure Hunter
            shards=["AD", "AS", "Armor"],
            why={
                "keystone": "Lethal Tempo — stacks up to 6 AS, each stack = faster headshots; fully stacked you fire significantly faster than base",
                "row1":     "Overheal — excess healing converts to a shield, keeps you alive after sustain poke in lane",
                "row2":     "Legend: Alacrity — permanent AS stacks from assists, compounds with Lethal Tempo",
                "row3":     "Cut Down — most targets have more HP than you; 8% bonus damage on them is free DPS",
                "secondary":"Domination for extra damage on CC procs",
                "sec1":     "Cheap Shot — your E trap immobilizes enemies, procing Cheap Shot for free true damage every trap",
                "sec2":     "Treasure Hunter — first kill gold accelerates your first item spike",
                "shards":   "AD for headshot burst, AS for faster procs, Armor vs bot lane poke",
            }
        ),
        dict(
            name="Fleet Footwork (vs poke/dive)",
            scenario="Enemy has dive assassins or heavy poke — need sustain and escape MS",
            conditions=["heavy_cc_enemy"],
            anti=[],
            primary="Precision",
            keystone=8021,    # Fleet Footwork
            row1=9101,        # Overheal
            row2=9104,        # Legend: Alacrity
            row3=8299,        # Cut Down
            secondary="Domination",
            sec1=8126,        # Cheap Shot
            sec2=8135,        # Treasure Hunter
            shards=["AD", "AS", "Armor"],
            why={
                "keystone": "Fleet Footwork — empowered auto heals and gives MS; lets you kite engage and sustain through poke without recalling",
                "row1":     "Overheal — converts Fleet heal overflow into a shield for extra durability",
                "row2":     "Legend: Alacrity — AS stacks keep your DPS up even when playing safer",
                "row3":     "Cut Down — still relevant since most enemies have more HP than you",
                "secondary":"Domination for trap damage",
                "sec1":     "Cheap Shot — trap procs true damage, free damage even when playing defensively",
                "sec2":     "Treasure Hunter — item spike is critical vs assassins who snowball hard",
                "shards":   "AD for headshot damage, AS for sustain procs, Armor vs physical engage",
            }
        ),
    ],

    "Khazix": [
        dict(
            name="Dark Harvest (Standard)",
            scenario="Default — stacks on kills and jungle camps, scales into one-shot territory",
            conditions=["always"],
            anti=["heavy_cc_enemy"],
            primary="Domination",
            keystone=8128,    # Dark Harvest
            row1=8143,        # Sudden Impact — E leap procs magic pen
            row2=8138,        # Eyeball Collection — stacks on every camp/kill
            row3=8135,        # Treasure Hunter
            secondary="Sorcery",
            sec1=8210,        # Transcendence
            sec2=8236,        # Gathering Storm
            shards=["AD", "AH", "Armor"],
            why={
                "keystone": "Dark Harvest — every kill/assist stacks it, proc on low-HP enemies; by mid game your Q one-shots isolated targets",
                "row1":     "Sudden Impact — E leap gives lethality + magic pen on landing, amplifies your burst every dive",
                "row2":     "Eyeball Collection — stacks on jungle camps too, permanent AD that compounds through the game",
                "row3":     "Treasure Hunter — first kill per bounty tier = faster lethality items",
                "secondary":"Sorcery for haste and late scaling",
                "sec1":     "Transcendence — AH overflow → AD, more Q casts per fight",
                "sec2":     "Gathering Storm — free AD scaling for late games where teams group up",
                "shards":   "AD for Q burst, AH for more abilities, Armor vs physical junglers",
            }
        ),
        dict(
            name="Electrocute (Early burst)",
            scenario="Lane-heavy game or snowball comp — need burst damage early before Dark Harvest stacks",
            conditions=["late_game_enemy"],
            anti=[],
            primary="Domination",
            keystone=8112,    # Electrocute
            row1=8143,        # Sudden Impact
            row2=8138,        # Eyeball Collection
            row3=8135,        # Treasure Hunter
            secondary="Sorcery",
            sec1=8210,        # Transcendence
            sec2=8237,        # Scorch
            shards=["AD", "AH", "Armor"],
            why={
                "keystone": "Electrocute — Q-E-auto procs it instantly, spikes harder at level 6 first gank than Dark Harvest",
                "row1":     "Sudden Impact — lethality + magic pen on E leap, identical to Dark Harvest page",
                "row2":     "Eyeball Collection — permanent AD from kills, less dependent on low-HP procs than Dark Harvest",
                "row3":     "Treasure Hunter — gold advantage from early kills accelerates lethality items",
                "secondary":"Sorcery for early bonus damage",
                "sec1":     "Transcendence — AH for more Q resets in skirmishes",
                "sec2":     "Scorch — chip damage on Q poke between ganks, punishes enemies who don't back",
                "shards":   "AD for early Q damage, AH for rotations, Armor vs AD lanes",
            }
        ),
        dict(
            name="Phase Rush (vs heavy CC)",
            scenario="Enemy has hard CC that stops your E escape (Warwick, Nautilus, Lissandra)",
            conditions=["heavy_cc_enemy"],
            anti=[],
            primary="Sorcery",
            keystone=8230,    # Phase Rush
            row1=8226,        # Manaflow Band
            row2=8210,        # Transcendence
            row3=8236,        # Gathering Storm
            secondary="Domination",
            sec1=8143,        # Sudden Impact
            sec2=8135,        # Treasure Hunter
            shards=["AD", "AH", "Armor"],
            why={
                "keystone": "Phase Rush — Q-auto-E procs it, gives 30-40% MS to burst and disengage before CC lands",
                "row1":     "Manaflow Band — extended games need mana; CC comps drag fights longer",
                "row2":     "Transcendence — AH for more Q rotations when you can't all-in freely",
                "row3":     "Gathering Storm — AD scaling for safer late-game play vs CC-heavy comps",
                "secondary":"Domination for lethality on engages",
                "sec1":     "Sudden Impact — E leap lethality still procs even on Phase Rush page",
                "sec2":     "Treasure Hunter — item spike needed faster since early game is harder",
                "shards":   "AD for burst, AH for rotations, Armor vs physical CC engagers",
            }
        ),
    ],

    "Diana": [
        dict(
            name="Electrocute (Standard burst)",
            scenario="Default — Q-R in, passive procs Electrocute, burst squishies",
            conditions=["always"],
            anti=["heavy_cc_enemy"],
            primary="Domination",
            keystone=8112,    # Electrocute
            row1=8143,        # Sudden Impact — E dash procs it
            row2=8138,        # Eyeball Collection
            row3=8135,        # Treasure Hunter
            secondary="Sorcery",
            sec1=8210,        # Transcendence
            sec2=8236,        # Gathering Storm
            shards=["AP", "AP", "Armor"],
            why={
                "keystone": "Electrocute — passive 3-hit lines up with Q-auto-R, one-shots squishies on first back-reset",
                "row1":     "Sudden Impact — dash into target procs magic pen, amplifies your whole burst rotation",
                "row2":     "Eyeball Collection — stacks on kills, permanent AP that compounds through the game",
                "row3":     "Treasure Hunter — gold on first kill per bounty tier = faster item spike",
                "secondary":"Sorcery for CDR and late-game AP",
                "sec1":     "Transcendence — AH overflow → AP, more R casts per fight",
                "sec2":     "Gathering Storm — free AP scaling for late dives, W range hurts more each minute",
                "shards":   "Double AP for burst damage, Armor vs physical junglers/top",
            }
        ),
        dict(
            name="Dark Harvest (Snowball/Late)",
            scenario="Enemy has tanks or game expected to go late — scales harder, stacks on jungle camps",
            conditions=["late_game_enemy"],
            anti=[],
            primary="Domination",
            keystone=8128,    # Dark Harvest
            row1=8143,        # Sudden Impact
            row2=8138,        # Eyeball Collection
            row3=8135,        # Treasure Hunter
            secondary="Sorcery",
            sec1=8210,        # Transcendence
            sec2=8236,        # Gathering Storm
            shards=["AP", "AP", "Armor"],
            why={
                "keystone": "Dark Harvest — stacks on kills and low-HP enemies, by mid game your R one-shots without full combo",
                "row1":     "Sudden Impact — magic pen on every engage, synergizes with Dark Harvest burst",
                "row2":     "Eyeball Collection — stacks faster in jungle with camp kills",
                "row3":     "Treasure Hunter — accelerate item spike for faster Rabadon's",
                "secondary":"Sorcery for scaling AP",
                "sec1":     "Transcendence — AH overflow → AP, more R casts in teamfights",
                "sec2":     "Gathering Storm — AP ramp for late-game dives when enemy tanks are stacking MR",
                "shards":   "Double AP for burst, Armor vs physical damage",
            }
        ),
        dict(
            name="Phase Rush (vs heavy CC)",
            scenario="Enemy has CC chain (Rammus, Lux, Nautilus) — dive in, proc passive, sprint out",
            conditions=["heavy_cc_enemy"],
            anti=[],
            primary="Sorcery",
            keystone=8230,    # Phase Rush
            row1=8226,        # Manaflow Band
            row2=8210,        # Transcendence
            row3=8236,        # Gathering Storm
            secondary="Domination",
            sec1=8143,        # Sudden Impact
            sec2=8135,        # Treasure Hunter
            shards=["AP", "AP", "Armor"],
            why={
                "keystone": "Phase Rush — Q-auto-R procs it instantly, 30-40% MS lets you dive, burst, and exit before CC lands",
                "row1":     "Manaflow Band — extended games need mana sustain for repeated dives",
                "row2":     "Transcendence — more R casts in the long games CC comps drag you into",
                "row3":     "Gathering Storm — AP scales up for late-game dives when you're playing safer early",
                "secondary":"Domination for magic pen",
                "sec1":     "Sudden Impact — dash magic pen still procs on engage even with Phase Rush",
                "sec2":     "Treasure Hunter — item spike faster since early game will be slower vs CC",
                "shards":   "Double AP for burst, Armor vs physical CC engagers",
            }
        ),
    ],

    "Kayn": [
        dict(
            name="Dark Harvest (Shadow Assassin)",
            scenario="Default vs squishy/AP-heavy comps — one-shot isolated targets",
            conditions=["always"],
            anti=["heavy_cc_enemy"],
            primary="Domination",
            keystone=8128,    # Dark Harvest
            row1=8143,        # Sudden Impact — E wall-pass procs lethality + magic pen
            row2=8138,        # Eyeball Collection
            row3=8135,        # Treasure Hunter
            secondary="Sorcery",
            sec1=8210,        # Transcendence
            sec2=8236,        # Gathering Storm
            shards=["AD", "AH", "Armor"],
            why={
                "keystone": "Dark Harvest — stacks on kills and low-HP enemies; Shadow Assassin path amplifies burst so Dark Harvest procs faster",
                "row1":     "Sudden Impact — E wall-pass gives lethality + magic pen on landing, free burst amp on every engage",
                "row2":     "Eyeball Collection — permanent AD from kills, ramps up through the game",
                "row3":     "Treasure Hunter — gold on first kill per bounty tier = faster lethality items",
                "secondary":"Sorcery for haste and late scaling",
                "sec1":     "Transcendence — AH overflow → AD, more Q/E rotations",
                "sec2":     "Gathering Storm — free AD for late games where you can't end early",
                "shards":   "AD for burst, AH for rotations, Armor vs physical junglers",
            }
        ),
        dict(
            name="Conqueror (Rhaast)",
            scenario="Enemy has 2+ tanks or bruisers — go Rhaast, sustain through fights",
            conditions=["late_game_enemy"],
            anti=[],
            primary="Precision",
            keystone=8010,    # Conqueror
            row1=9101,        # Absorb Life
            row2=9103,        # Legend: Bloodline
            row3=8299,        # Last Stand — lower HP = more damage, Rhaast heals back up
            secondary="Resolve",
            sec1=8473,        # Bone Plating
            sec2=8453,        # Revitalize — amplifies Rhaast passive heals
            shards=["AD", "AH", "Armor"],
            why={
                "keystone": "Conqueror — stacks in sustained fights, converts to healing at max stacks; Rhaast needs extended combat to proc his passive heal",
                "row1":     "Absorb Life — heals on minion/monster kills, sustain during jungle clear and extended fights",
                "row2":     "Legend: Bloodline — lifesteal stacks up permanently, amplifies Rhaast's already strong healing",
                "row3":     "Last Stand — Rhaast heals back up in fights, so you spend more time at low HP where Last Stand fires",
                "secondary":"Resolve for durability in extended fights",
                "sec1":     "Bone Plating — reduces burst in the first exchange before Conqueror stacks up",
                "sec2":     "Revitalize — amplifies Rhaast passive heals and Conqueror healing",
                "shards":   "AD for damage, AH for more Q healing procs, Armor vs physical lanes",
            }
        ),
        dict(
            name="Phase Rush (vs heavy CC)",
            scenario="Enemy has hard CC that stops your E escape (Warwick R, Nautilus, Vi)",
            conditions=["heavy_cc_enemy"],
            anti=[],
            primary="Sorcery",
            keystone=8230,    # Phase Rush
            row1=8226,        # Manaflow Band
            row2=8210,        # Transcendence
            row3=8236,        # Gathering Storm
            secondary="Domination",
            sec1=8143,        # Sudden Impact
            sec2=8135,        # Treasure Hunter
            shards=["AD", "AH", "Armor"],
            why={
                "keystone": "Phase Rush — Q-auto-E procs it, 30-40% MS lets you burst and disengage before CC pins you",
                "row1":     "Manaflow Band — longer fights from CC comps drain mana; passive regen keeps you going",
                "row2":     "Transcendence — AH for more Q/E uses when you can't dive freely",
                "row3":     "Gathering Storm — AD scaling for safer late-game play vs CC-heavy comps",
                "secondary":"Domination for burst damage on engages",
                "sec1":     "Sudden Impact — E wall-pass lethality still procs even on Phase Rush page",
                "sec2":     "Treasure Hunter — item spike needed faster since early game is harder vs CC",
                "shards":   "AD for burst, AH for rotations, Armor vs physical CC engagers",
            }
        ),
    ],

    "Milio": [
        dict(
            name="Summon Aery (Standard)",
            scenario="Default — poke in lane, shield/heal empowers Aery proc",
            conditions=["always"],
            anti=[],
            primary="Sorcery",
            keystone=8214,    # Summon Aery
            row1=8226,        # Manaflow Band
            row2=8234,        # Celerity
            row3=8237,        # Scorch
            secondary="Resolve",
            sec1=8463,        # Font of Life
            sec2=8453,        # Revitalize
            shards=["AH", "AH", "Armor"],
            why={
                "keystone": "Summon Aery — procs on every shield and heal, pokes with E and W, adds constant pressure",
                "row1":     "Manaflow Band — restores mana when you poke, keeps you from going oom",
                "row2":     "Celerity — extra speed means better positioning to W your ADC",
                "row3":     "Scorch — burn on your Q chip damage, punishes enemies who walk up",
                "secondary":"Resolve for sustain and healing amplification",
                "sec1":     "Font of Life — marks enemies you slow (Q), heals your ADC when they attack them",
                "sec2":     "Revitalize — amplifies all your heals and shields including R",
                "shards":   "Double AH for more W/E/R casts, Armor for laning phase",
            }
        ),
        dict(
            name="Guardian (Peel Focus)",
            scenario="Enemy has assassins or heavy dive — protect ADC at all costs",
            conditions=["has_assassin"],
            anti=[],
            primary="Resolve",
            keystone=8465,    # Guardian
            row1=8463,        # Font of Life
            row2=8473,        # Bone Plating
            row3=8453,        # Revitalize
            secondary="Inspiration",
            sec1=8345,        # Biscuit Delivery
            sec2=8347,        # Cosmic Insight
            shards=["AH", "AH", "Armor"],
            why={
                "keystone": "Guardian — shared shield when near your ADC, blocks the assassin's burst window",
                "row1":     "Font of Life — passive healing on your ADC when you slow enemies with Q",
                "row2":     "Bone Plating — reduces burst on you or your ADC from the first hits",
                "row3":     "Revitalize — amplifies Guardian shield and all heals/shields",
                "secondary":"Inspiration for extra sustain and CDR",
                "sec1":     "Biscuit Delivery — sustain through poke lanes that assassins run behind",
                "sec2":     "Cosmic Insight — lower summoner spell CD, more Flash saves",
                "shards":   "Double AH for more shield/heal casts, Armor for laning phase",
            }
        ),
        dict(
            name="Aery + Sorcery (Enchanter Full)",
            scenario="Team has AP carry (Swain, Kassadin, Viktor) — Staff of Flowing Water synergy",
            conditions=["ally_ap_carry"],
            anti=["has_assassin"],
            primary="Sorcery",
            keystone=8214,    # Summon Aery
            row1=8226,        # Manaflow Band
            row2=8234,        # Celerity
            row3=8236,        # Gathering Storm
            secondary="Resolve",
            sec1=8463,        # Font of Life
            sec2=8453,        # Revitalize
            shards=["AH", "AP", "Armor"],
            why={
                "keystone": "Summon Aery — constant shields/heals proc it, scales with AP from Gathering Storm",
                "row1":     "Manaflow Band — mana sustain for long enchanter games",
                "row2":     "Celerity — movement speed for positioning around your AP carry",
                "row3":     "Gathering Storm — AP scales your heals/shields late, Staff of Flowing Water value goes up",
                "secondary":"Resolve for healing amplification",
                "sec1":     "Font of Life — heals AP carry passively when you poke",
                "sec2":     "Revitalize — amplifies all your enchanter heals and shields",
                "shards":   "AH + AP shard for more and stronger heals, Armor for lane",
            }
        ),
    ],
}


# ---------------------------------------------------------------------------
# Condition evaluation
# ---------------------------------------------------------------------------

def build_rune_context(ally_comp: dict, enemy_comp: dict) -> dict:
    fighting_adcs = {"Samira", "Draven", "Jinx", "Tristana", "Kaisa"}
    peel_adcs     = {"Vayne", "Ezreal", "Aphelios", "Caitlyn", "Jhin"}
    ap_carries    = {"Swain", "Kassadin", "Viktor", "Cassiopeia", "Ryze", "Syndra",
                     "Veigar", "Orianna", "Azir", "Lux", "Ahri", "Diana", "Katarina"}
    ally_names    = {n for n, _ in ally_comp["resolved"]}

    return {
        "always":           True,
        "fighting_adc":     bool(ally_names & fighting_adcs),
        "peel_adc":         bool(ally_names & peel_adcs),
        "ally_ap_carry":    bool(ally_names & ap_carries),
        "heavy_cc_enemy":   (enemy_comp["hard_cc"] + enemy_comp["soft_cc"]) >= 4,
        "late_game_enemy":  len(enemy_comp["late_scalers"]) >= 2,
        "has_assassin":     len(enemy_comp["assassins"]) >= 1,
        "assassin_enemy":   len(enemy_comp["assassins"]) >= 1,
        "magic_heavy":      enemy_comp["magic_dmg"] >= 3,
        "physical_heavy":   enemy_comp["phys_dmg"] >= 3,
    }


_RUNE_ALIASES = {
    "kha'zix": "Khazix", "khazix": "Khazix",
    "wukong": "MonkeyKing", "monkeyking": "MonkeyKing",
    "nunu & willump": "Nunu",
    "lee sin": "LeeSin",
    "tahm kench": "TahmKench",
    "twisted fate": "TwistedFate",
    "master yi": "MasterYi",
    "kai'sa": "Kaisa",
    "kog'maw": "KogMaw",
    "bel'veth": "Belveth",
    "k'sante": "KSante",
    "cho'gath": "Chogath",
    "vel'koz": "Velkoz",
    "rek'sai": "RekSai",
}


def _normalize_champ(name: str) -> str:
    return _RUNE_ALIASES.get(name.lower().strip(), name)


def pick_rune_page(champion: str, ctx: dict) -> dict | None:
    champion = _normalize_champ(champion)
    pages = RUNE_PAGES.get(champion, [])
    for page in pages:
        if any(ctx.get(a) for a in page["anti"]):
            continue
        if "always" in page["conditions"] or all(ctx.get(c) for c in page["conditions"]):
            return page
    # Fallback to first page
    return pages[0] if pages else None


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

TREE_COLOR = {
    "Resolve":     "green",
    "Sorcery":     "blue",
    "Inspiration": "cyan",
    "Precision":   "yellow",
    "Domination":  "red",
}

def rune_name(rune_id: int) -> str:
    return RUNES.get(rune_id, {}).get("name", f"Unknown({rune_id})")

def print_rune_page(page: dict, champion: str):
    tree_color = TREE_COLOR.get(page["primary"], "white")
    sec_color  = TREE_COLOR.get(page["secondary"], "white")

    lines = [
        f"[bold]Page:[/bold] {page['name']}",
        f"[bold]When:[/bold] {page['scenario']}",
        "",
        f"[{tree_color}][bold]PRIMARY — {page['primary']}[/bold][/{tree_color}]",
        f"  Keystone : [bold]{rune_name(page['keystone'])}[/bold]",
        f"             [dim]{page['why']['keystone']}[/dim]",
        f"  Row 1    : [bold]{rune_name(page['row1'])}[/bold]",
        f"             [dim]{page['why']['row1']}[/dim]",
        f"  Row 2    : [bold]{rune_name(page['row2'])}[/bold]",
        f"             [dim]{page['why']['row2']}[/dim]",
        f"  Row 3    : [bold]{rune_name(page['row3'])}[/bold]",
        f"             [dim]{page['why']['row3']}[/dim]",
        "",
        f"[{sec_color}][bold]SECONDARY — {page['secondary']}[/bold][/{sec_color}]",
        f"  {rune_name(page['sec1'])} + {rune_name(page['sec2'])}",
        f"  [dim]{page['why']['secondary']} — {page['why']['sec1']} / {page['why']['sec2']}[/dim]",
        "",
        f"[bold]SHARDS:[/bold] {' | '.join(page['shards'])}",
        f"[dim]{page['why']['shards']}[/dim]",
    ]

    console.print(Panel(
        "\n".join(lines),
        title=f"[bold]Recommended Runes — {champion}[/bold]",
        border_style=tree_color,
        padding=(1, 2),
    ))


def print_all_pages(champion: str):
    """Show all available pages for reference."""
    champion = _normalize_champ(champion)
    pages = RUNE_PAGES.get(champion, [])
    if not pages:
        console.print(f"[yellow]No rune pages defined for {champion}.[/yellow]")
        return

    t = Table(title=f"All Rune Pages — {champion}", box=box.ROUNDED)
    t.add_column("Page",      style="cyan", min_width=28)
    t.add_column("Keystone",  min_width=22)
    t.add_column("Secondary", min_width=12)
    t.add_column("When to use")

    for p in pages:
        t.add_row(
            p["name"],
            rune_name(p["keystone"]),
            p["secondary"],
            p["scenario"],
        )
    console.print(t)


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def get_comp_from_live(account_label: str) -> tuple[str, list[str], list[str]]:
    account_str = ACCOUNTS[account_label]
    game_name, tag = account_str.rsplit("#", 1)
    acct  = get_account(game_name, tag)
    puuid = acct["puuid"]

    try:
        data = _get(f"{BASE_SUMMONER}/lol/spectator/v5/active-games/by-summoner/{puuid}")
    except Exception as e:
        if "404" in str(e):
            console.print("[red]Not currently in a game.[/red]")
        else:
            console.print(f"[red]API error: {e}[/red]")
        sys.exit(1)

    _load_champ_id_map()
    my_team_id = next(p["teamId"] for p in data["participants"] if p["puuid"] == puuid)
    my_champ   = champ_name_from_id(next(p["championId"] for p in data["participants"] if p["puuid"] == puuid))
    allies, enemies = [], []
    for p in data["participants"]:
        name = champ_name_from_id(p["championId"])
        if p["puuid"] == puuid: continue
        if p["teamId"] == my_team_id: allies.append(name)
        else: enemies.append(name)

    return my_champ, allies, enemies


def get_comp_from_last(account_label: str) -> tuple[str, list[str], list[str]]:
    account_str = ACCOUNTS[account_label]
    game_name, tag = account_str.rsplit("#", 1)
    acct  = get_account(game_name, tag)
    puuid = acct["puuid"]
    mid   = get_match_ids(puuid, count=1)[0]
    match = get_match(mid)
    p     = extract_participant(match, puuid)
    my_champ   = p["championName"]
    my_team_id = p["teamId"]
    allies, enemies = [], []
    for part in match["info"]["participants"]:
        name = part["championName"]
        if part["puuid"] == puuid: continue
        if part["teamId"] == my_team_id: allies.append(name)
        else: enemies.append(name)
    return my_champ, allies, enemies


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Rune page recommendations")
    parser.add_argument("account_or_champ", help="Account label (main/lab) or champion name (manual mode)")
    parser.add_argument("--last",    action="store_true", help="Use last game instead of live")
    parser.add_argument("--all",     action="store_true", help="Show all available pages for the champion")
    parser.add_argument("--ally",    help="Allies (manual): 'Jinx,Yasuo,Garen,Viego'")
    parser.add_argument("--enemy",   help="Enemies (manual): 'Nautilus,Caitlyn,Zed,Morgana,Viktor'")
    args = parser.parse_args()

    # Determine mode
    if args.account_or_champ in ACCOUNTS:
        account_label = args.account_or_champ
        if args.last:
            console.print("[dim]Fetching last game...[/dim]")
            my_champ, ally_names, enemy_names = get_comp_from_last(account_label)
        else:
            console.print("[dim]Fetching live game...[/dim]")
            my_champ, ally_names, enemy_names = get_comp_from_live(account_label)
    else:
        # Manual mode
        if not args.ally or not args.enemy:
            parser.error("Manual mode requires --ally and --enemy")
        my_champ    = args.account_or_champ
        ally_names  = [n.strip() for n in args.ally.split(",")]
        enemy_names = [n.strip() for n in args.enemy.split(",")]

    console.print(f"\n[bold]Champion:[/bold] [cyan]{my_champ}[/cyan]")
    console.print(f"[dim]Allies:  {', '.join(ally_names)}[/dim]")
    console.print(f"[dim]Enemies: {', '.join(enemy_names)}[/dim]\n")

    if args.all:
        print_all_pages(my_champ)
        return

    ally_comp  = analyze_comp([my_champ] + ally_names)
    enemy_comp = analyze_comp(enemy_names)
    ctx        = build_rune_context(ally_comp, enemy_comp)

    page = pick_rune_page(my_champ, ctx)
    if not page:
        console.print(f"[yellow]No rune pages defined for {my_champ} yet.[/yellow]")
        return

    print_rune_page(page, my_champ)

    # Show other available pages as reference
    other_pages = [p for p in RUNE_PAGES.get(_normalize_champ(my_champ), []) if p["name"] != page["name"]]
    if other_pages:
        console.print()
        t = Table(title="Other available pages", box=box.SIMPLE)
        t.add_column("Page",     style="dim cyan")
        t.add_column("Keystone", style="dim")
        t.add_column("When")
        for p in other_pages:
            t.add_row(p["name"], rune_name(p["keystone"]), p["scenario"])
        console.print(t)


if __name__ == "__main__":
    main()

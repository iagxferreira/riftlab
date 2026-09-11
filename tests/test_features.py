from riftlab.rl.features import compute_reward, samples_from_match, team_context
from riftlab.rl.static import Champion, StaticData


def champ(key, tags, attack=5, magic=5):
    return Champion(key, f"C{key}", f"C{key}", tuple(tags), attack, magic)


MAGE = champ(1, ["Mage"], attack=2, magic=9)
MAGE_ASSASSIN = champ(2, ["Mage", "Assassin"], attack=3, magic=8)
TANK = champ(3, ["Tank"], attack=5, magic=5)
FIGHTER = champ(4, ["Fighter"], attack=8, magic=2)
MARKSMAN = champ(5, ["Marksman"], attack=9, magic=1)
SUPPORT = champ(6, ["Support"], attack=2, magic=7)

STATIC = StaticData("test", {c.key: c for c in (MAGE, MAGE_ASSASSIN, TANK, FIGHTER, MARKSMAN, SUPPORT)},
                    {8112: "Electrocute", 8128: "Dark Harvest"})


def participant(i, champion_id, team, win, k=2, d=2, a=2, keystone=8112):
    return {"puuid": f"p{i}", "championId": champion_id, "teamId": team, "win": win,
            "kills": k, "deaths": d, "assists": a,
            "perks": {"styles": [{"description": "primaryStyle", "selections": [{"perk": keystone}]}]}}


def match(parts, duration=1800):
    return {"info": {"gameDuration": duration, "gameCreation": 1_700_000_000_000, "participants": parts}}


def test_context_flags_from_enemy_classes():
    enemies = [MAGE, MAGE, MAGE_ASSASSIN, TANK, TANK]
    allies = [MAGE, SUPPORT, MARKSMAN, MAGE]
    assert team_context(allies, enemies) == {"enemy_ap_heavy", "enemy_assassin", "enemy_tanky", "ally_no_frontline"}


def test_balanced_comp_has_no_flags():
    enemies = [FIGHTER, MARKSMAN, MAGE, SUPPORT, TANK]
    allies = [FIGHTER, MARKSMAN, MAGE, SUPPORT]
    assert team_context(allies, enemies) == frozenset()


def test_reward_prefers_win_and_stays_bounded():
    team_w = [participant(i, 1, 100, True) for i in range(5)]
    team_l = [participant(i, 1, 100, False) for i in range(5)]
    assert compute_reward(team_w[0], team_w) > compute_reward(team_l[0], team_l)

    best = [participant(0, 1, 100, True, k=20, d=0, a=0)] + [participant(i, 1, 100, True, k=0, d=15, a=0) for i in range(1, 5)]
    worst = [participant(0, 1, 100, False, k=0, d=20, a=0)] + [participant(i, 1, 100, False, k=5, d=0, a=5) for i in range(1, 5)]
    assert compute_reward(best[0], best) == 1.0
    assert compute_reward(worst[0], worst) == -1.0


def test_reward_counts_deaths_relative_to_teammates():
    careful = [participant(0, 1, 100, False, d=1)] + [participant(i, 1, 100, False, d=6) for i in range(1, 5)]
    reckless = [participant(0, 1, 100, False, d=11)] + [participant(i, 1, 100, False, d=6) for i in range(1, 5)]
    assert compute_reward(careful[0], careful) > compute_reward(reckless[0], reckless)


def test_one_sample_per_participant_from_each_teams_view():
    blue = [participant(i, cid, 100, True) for i, cid in enumerate([1, 1, 2, 5, 6])]
    red = [participant(i + 5, cid, 200, False, keystone=8128) for i, cid in enumerate([3, 3, 4, 5, 6])]
    samples = samples_from_match("M1", match(blue + red), STATIC)

    assert len(samples) == 10
    blue_s, red_s = samples[0], samples[5]
    assert (blue_s.action, red_s.action) == ("Electrocute", "Dark Harvest")
    assert blue_s.win and not red_s.win
    assert "enemy_tanky" in blue_s.context          # red has two tanks
    assert "enemy_ap_heavy" in red_s.context         # blue has three magic-leaning champions
    assert blue_s.timestamp == 1_700_000_000


def test_remakes_and_unknown_champions_are_skipped():
    parts = [participant(i, 1, 100 if i < 5 else 200, i < 5) for i in range(10)]
    assert samples_from_match("M", match(parts, duration=240), STATIC) == []
    parts[3]["championId"] = 999
    assert samples_from_match("M", match(parts), STATIC) == []

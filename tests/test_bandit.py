import pytest

from riftlab.rl.bandit import ANY, MIN_ARM_N, MIN_CONTEXT_N, PRIOR_N, Bandit, fit, replay
from riftlab.rl.features import Sample

CTX = frozenset({"enemy_tanky"})


def sample(action, reward, match_id="M", ts=0, context=CTX, champion="Syndra", win=None):
    return Sample(match_id, ts, f"p-{match_id}-{action}", champion, context, action, reward,
                  reward > 0 if win is None else win)


def test_small_samples_are_shrunk_toward_zero():
    model = fit([sample("Electrocute", 1.0)])
    (est,) = model.rank("Syndra", CTX)
    assert est.value == pytest.approx(1.0 / (1 + PRIOR_N))
    assert est.mean == 1.0


def test_sparse_context_falls_back_to_champion_level():
    model = fit([sample("Electrocute", 0.5, match_id=f"M{i}") for i in range(MIN_CONTEXT_N - 1)])
    assert model.rank("Syndra", CTX)[0].level == ANY

    model.update(sample("Electrocute", 0.5, match_id="last"))
    assert model.rank("Syndra", CTX)[0].level == "enemy_tanky"


def test_greedy_ignores_keystones_with_too_few_games():
    few_great = [sample("Dark Harvest", 1.0, match_id=f"a{i}") for i in range(MIN_ARM_N - 1)]
    many_ok = [sample("Electrocute", 0.3, match_id=f"b{i}") for i in range(MIN_ARM_N)]
    model = fit(few_great + many_ok)
    assert model.rank("Syndra", CTX)[0].action == "Dark Harvest"   # best estimate...
    assert model.greedy("Syndra", CTX) == "Electrocute"             # ...but not enough games to recommend


def test_explore_bonus_favors_less_tried_keystones():
    model = fit([sample("Electrocute", 0.2, match_id=f"a{i}") for i in range(20)]
                + [sample("Dark Harvest", 0.2, match_id=f"b{i}") for i in range(2)])
    assert model.rank("Syndra", CTX)[0].action == "Electrocute"
    assert model.rank("Syndra", CTX, explore=True)[0].action == "Dark Harvest"


def test_replay_only_uses_earlier_matches():
    first = [sample("Electrocute", 0.5, match_id="M1", ts=1) for _ in range(MIN_ARM_N)]
    second = [sample("Electrocute", 0.5, match_id="M2", ts=2), sample("Dark Harvest", -0.5, match_id="M2", ts=2)]
    res = replay(first + second)
    assert res["uncovered"] == MIN_ARM_N        # nothing learned before M1
    assert [s.action for s in res["agree"]] == ["Electrocute"]
    assert [s.action for s in res["disagree"]] == ["Dark Harvest"]


def test_champions_are_estimated_separately():
    model = Bandit()
    model.update(sample("Electrocute", 1.0))
    assert model.rank("Ahri", CTX) == []

import pytest
from pydantic import SecretStr

from cyberlab.architect import RulesModel
from cyberlab.architect.evaluate import check, load_cases, run
from cyberlab.cli import main
from cyberlab.config import QuotaSettings

from .test_architect import ScriptedModel, answer, machine


def test_builtin_planner_passes_the_whole_evaluation_set(registry):
    results = run(RulesModel(registry), registry, QuotaSettings(), load_cases())
    failed = [(r.prompt, r.problems) for r in results if not r.passed]
    assert not failed
    assert len(results) >= 20


def test_evaluation_set_covers_refusals_both_ways():
    cases = load_cases()
    assert any(c["expect"].get("refused") is True for c in cases)
    assert any(c["expect"].get("refused") is False for c in cases)


def one(registry, prompt, model):
    (r,) = run(model, registry, QuotaSettings(), [{"prompt": prompt, "expect": EXPECT}])
    return r


EXPECT = {
    "refused": False,
    "valid": True,
    "machines": 1,
    "system_any": ["kali-rolling"],
    "packs_include": ["network-analysis"],
    "packs_exclude": ["forensics"],
    "network": "lab",
    "no_internet": True,
    "desktop": False,
    "approval": False,
}


def test_checker_passes_a_correct_draft(registry):
    assert one(registry, "packets on kali", ScriptedModel(answer(machine()))).passed


@pytest.mark.parametrize(
    "bad, problem",
    [
        (answer(machine(baseImage="debian-12")), "system 'debian-12'"),
        (answer(machine(toolPacks=[])), "missing tool packs"),
        (answer(machine(toolPacks=["network-analysis", "forensics"])), "unwanted tool packs"),
        (answer(machine(network="internet")), "has full internet access"),
        (answer(machine(network="isolated")), "network ['isolated']"),
        (answer(machine(), machine(name="target")), "2 machines"),
        (answer(machine(desktop=True)), "has a desktop"),
        (answer(machine(baseImage="debian-12", toolPacks=["web-appsec"])), "policy engine"),
        (answer(refused=True, refusalReason="no", environments=[]), "was refused"),
        ({"environments": "junk"}, "error:"),
    ],
)
def test_checker_catches_bad_drafts(registry, bad, problem):
    r = one(registry, "packets on kali", ScriptedModel(bad))
    assert not r.passed
    assert any(problem in p for p in r.problems), r.problems


def test_check_should_have_been_refused(registry):
    from cyberlab.architect import draft_lab

    result = draft_lab("a lab", registry, QuotaSettings(), ScriptedModel(answer(machine())))
    assert check(result, {"refused": True}) == ["should have been refused"]


def test_cli_eval_and_cost_guard(capsys, monkeypatch, tmp_path):
    assert main(["architect", "eval"]) == 0
    assert "rules: " in capsys.readouterr().out
    bad = tmp_path / "bad.yaml"
    bad.write_text('- {prompt: "a debian box", expect: {system_any: [kali-rolling]}}\n')
    assert main(["architect", "eval", "--file", str(bad)]) == 1
    assert "FAIL" in capsys.readouterr().out

    from cyberlab import config

    paid = config.Settings(
        ai_provider="anthropic", ai_api_key=SecretStr("sk-x"), allow_paid_providers=True
    )
    monkeypatch.setattr("cyberlab.cli.get_settings", lambda: paid)
    assert main(["architect", "eval"]) == 2  # refuses to spend money without --yes
    assert "--yes" in capsys.readouterr().err

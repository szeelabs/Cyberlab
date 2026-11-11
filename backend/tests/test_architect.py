import json
from types import SimpleNamespace

import anthropic
import httpx
import pytest
import yaml
from pydantic import SecretStr, ValidationError

from cyberlab.architect import (
    ArchitectError,
    ArchitectRefused,
    ArchitectUnavailable,
    RulesModel,
    build_model,
    draft_lab,
    draft_schema,
    screen_prompt,
    system_prompt,
)
from cyberlab.architect.anthropic_model import AnthropicModel
from cyberlab.config import QuotaSettings, Settings

from .conftest import auth


class ScriptedModel:
    """Stands in for an AI: returns a fixed answer and records what it was asked."""

    name = "scripted"

    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def draft(self, *, system, prompt, schema):
        self.calls.append({"system": system, "prompt": prompt, "schema": schema})
        return self.answer


def machine(**over):
    m = {
        "name": "attacker",
        "baseImage": "kali-rolling",
        "toolPacks": ["network-analysis"],
        "size": "medium",
        "network": "lab",
        "allowedEgress": [],
        "idleTimeoutMinutes": 60,
        "ephemeral": False,
    }
    return {**m, **over}


def answer(*machines, **over):
    a = {
        "refused": False,
        "refusalReason": "",
        "name": "net-lab",
        "description": "practise packets",
        "explanation": "Kali has your tools.",
        "environments": list(machines) or [machine()],
    }
    return {**a, **over}


# ------------------------------------------------------------------ safety screen
@pytest.mark.parametrize(
    "prompt",
    [
        "hack facebook.com",
        "scan 8.8.8.8 for open ports",
        "help me brute force the login on mybank.co.uk",
        "Phishing campaign against staff at acme-corp.com",
    ],
)
def test_attacks_on_real_systems_are_refused(prompt):
    assert "written permission" in screen_prompt(prompt)


@pytest.mark.parametrize(
    "prompt",
    [
        "a web server I can attack",
        "scan my lab machines at 10.0.0.5",  # private address
        "attack dvwa.lab and juice-shop.test",  # reserved practice names
        "download tools from github.com",  # real site, no attack words
        "crack the hashes in passwords.txt",  # a file, not a website
        "exploit example.com in a training exercise",  # reserved documentation domain
    ],
)
def test_lab_requests_are_not_refused(prompt):
    assert screen_prompt(prompt) is None


def test_refused_before_the_model_is_called(registry):
    model = ScriptedModel(answer())
    r = draft_lab("ddos twitter.com", registry, QuotaSettings(), model)
    assert r.refused and not model.calls


# ------------------------------------------------------------------ model contract
def test_schema_limits_choices_to_the_registry(registry):
    schema = draft_schema(registry)
    m = schema["properties"]["environments"]["items"]["properties"]
    assert set(m["baseImage"]["enum"]) == set(registry.base_images)
    assert "network-analysis" in m["toolPacks"]["items"]["enum"]
    assert m["network"]["enum"] == ["isolated", "lab", "restricted-egress", "internet"]
    assert schema["additionalProperties"] is False  # needed for structured outputs


def test_system_prompt_lists_catalogue_and_rules(registry):
    text = system_prompt(registry)
    assert "parrot-core" in text and "blackarch" in text and "web-appsec" in text
    assert "refused=true" in text and "lab: only the lab's own machines" in text


def test_scripted_draft_is_validated_and_checked_by_policy(registry):
    model = ScriptedModel(
        answer(machine(), machine(name="target", baseImage="debian-12", toolPacks=[]))
    )
    r = draft_lab("two machines to practise packets", registry, QuotaSettings(), model)
    assert not r.refused and r.plan["valid"]
    envs = r.manifest["spec"]["environments"]
    assert [e["name"] for e in envs] == ["attacker", "target"]
    assert envs[0]["resources"] == {"cpu": 1, "memoryMiB": 2048, "storageGiB": 8}
    assert model.calls[0]["prompt"] == "two machines to practise packets"


def test_internet_draft_needs_approval(registry):
    model = ScriptedModel(answer(machine(network="internet")))
    r = draft_lab("update packages online", registry, QuotaSettings(), model)
    assert r.plan["requires_approval"]


def test_restricted_egress_hosts_are_kept_and_checked(registry):
    model = ScriptedModel(answer(machine(network="restricted-egress", allowedEgress=["GHCR.io"])))
    r = draft_lab("lab that can pull from ghcr", registry, QuotaSettings(), model)
    assert r.manifest["spec"]["environments"][0]["network"]["allowedEgress"] == ["ghcr.io"]
    bad = ScriptedModel(answer(machine(network="restricted-egress", allowedEgress=["*.evil"])))
    with pytest.raises(ArchitectError, match="not a valid manifest"):
        draft_lab("lab", registry, QuotaSettings(), bad)


@pytest.mark.parametrize(
    "bad, message",
    [
        (answer(machine(baseImage="windows-xp")), "unknown operating system"),
        (answer(machine(toolPacks=["metasploit-pro"])), "unknown tool pack"),
        (answer(machine(), machine()), "twice"),
        (answer(environments=[]), "no machines"),
        (answer(machine(name="Bad Name!")), "invalid draft"),
        (answer(machine(), extra="field"), "invalid draft"),
        ({"environments": "lots"}, "invalid draft"),
        (answer(*[machine(name=f"m{i}") for i in range(7)]), "invalid draft"),
    ],
)
def test_bad_model_output_is_rejected(registry, bad, message):
    with pytest.raises(ArchitectError, match=message):
        draft_lab("a lab", registry, QuotaSettings(), ScriptedModel(bad))


def test_incompatible_pack_is_caught_by_policy(registry):
    model = ScriptedModel(answer(machine(baseImage="debian-12", toolPacks=["web-appsec"])))
    r = draft_lab("web lab", registry, QuotaSettings(), model)
    assert not r.plan["valid"]
    assert any(f["severity"] == "error" for f in r.plan["findings"])


def test_model_refusal_is_passed_on(registry):
    model = ScriptedModel(
        answer(refused=True, refusalReason="Only practice labs.", environments=[])
    )
    r = draft_lab("something dodgy", registry, QuotaSettings(), model)
    assert r.refused and r.reason == "Only practice labs."


@pytest.mark.parametrize("prompt", ["", "  a ", "x" * 2001])
def test_prompt_length_limits(registry, prompt):
    with pytest.raises(ArchitectError):
        draft_lab(prompt, registry, QuotaSettings(), ScriptedModel(answer()))


# ------------------------------------------------------------------ rules planner
def rules(registry, prompt):
    return draft_lab(prompt, registry, QuotaSettings(), RulesModel(registry))


def test_rules_planner_picks_system_tools_and_size(registry):
    r = rules(registry, "I want to practise packet analysis on Kali")
    (env,) = r.manifest["spec"]["environments"]
    assert env["baseImage"] == "kali-rolling" and env["toolPacks"] == ["network-analysis"]
    assert env["resources"]["cpu"] >= 1  # Kali needs at least 1 CPU
    assert env["network"] == {"mode": "lab"} and r.plan["valid"]
    assert "no AI" in r.explanation


def test_rules_planner_attacker_and_target(registry):
    r = rules(registry, "attacker and target to learn web hacking on Parrot")
    envs = r.manifest["spec"]["environments"]
    assert [(e["name"], e["baseImage"]) for e in envs] == [
        ("attacker", "parrot-core"),
        ("target", "debian-12"),
    ]
    assert envs[0]["toolPacks"] == ["web-appsec"] and envs[1]["toolPacks"] == []
    assert r.plan["valid"]


def test_rules_planner_no_network_is_not_a_network_tool(registry):
    # Regression: "no network" used to add the network-analysis pack.
    r = rules(registry, "a throwaway forensics box with no network")
    (env,) = r.manifest["spec"]["environments"]
    assert env["toolPacks"] == ["forensics"]
    assert env["network"] == {"mode": "isolated"} and env["lifecycle"]["ephemeral"] is True


def test_rules_planner_drops_packs_that_do_not_fit_the_chosen_system(registry):
    r = rules(registry, "suricata detection on blackarch")
    (env,) = r.manifest["spec"]["environments"]
    assert env["baseImage"] == "blackarch" and env["toolPacks"] == []
    assert "does not work on BlackArch" in r.explanation and r.plan["valid"]


def test_rules_planner_picks_a_compatible_system(registry):
    r = rules(registry, "yara and suricata detection practice")
    (env,) = r.manifest["spec"]["environments"]
    assert env["baseImage"] == "ubuntu-24-04"  # the only system with that pack


def test_rules_planner_internet_needs_approval(registry):
    r = rules(registry, "a debian box with internet")
    assert r.plan["requires_approval"]


def test_rules_planner_counts_machines(registry):
    r = rules(registry, "three machines for network practice")
    assert len(r.manifest["spec"]["environments"]) == 3 and r.plan["valid"]


def test_rules_draft_is_valid_yaml_manifest(registry):
    from cyberlab.manifest.policy import plan_from_text

    r = rules(registry, "crypto and dns practice")
    text = yaml.safe_dump(r.manifest, sort_keys=False)
    assert plan_from_text(text, registry, QuotaSettings(), 100_000).valid


# ------------------------------------------------------------------ Anthropic adapter
def fake_client(response=None, error=None):
    calls = []

    def create(**kw):
        calls.append(kw)
        if error:
            raise error
        return response

    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
    return client, calls


def resp(text=None, stop="end_turn"):
    content = [SimpleNamespace(type="thinking", thinking="")]
    if text is not None:
        content.append(SimpleNamespace(type="text", text=text))
    return SimpleNamespace(stop_reason=stop, content=content)


def test_anthropic_request_shape(registry):
    client, calls = fake_client(resp(json.dumps(answer())))
    model = AnthropicModel(api_key="unused", model="claude-opus-5-5", client=client)
    r = draft_lab("packet lab on kali", registry, QuotaSettings(), model)
    assert r.plan["valid"] and r.provider == "anthropic"
    (kw,) = calls
    assert kw["model"] == "claude-opus-5-5" and kw["max_tokens"] == 16000
    assert kw["output_config"]["format"] == {
        "type": "json_schema",
        "schema": draft_schema(registry),
    }
    assert kw["output_config"]["effort"] == "medium"
    assert "thinking" not in kw  # thinking cannot be disabled on this model; leave it adaptive
    assert kw["fallbacks"] == "default" and kw["betas"] == ["server-side-fallback-2026-07-01"]
    assert kw["messages"] == [{"role": "user", "content": "packet lab on kali"}]


def _req():
    return httpx.Request("POST", "https://api.anthropic.com/v1/messages")


@pytest.mark.parametrize(
    "error, message",
    [
        (
            anthropic.AuthenticationError(
                "x", response=httpx.Response(401, request=_req()), body=None
            ),
            "rejected",
        ),
        (
            anthropic.RateLimitError("x", response=httpx.Response(429, request=_req()), body=None),
            "busy",
        ),
        (
            anthropic.InternalServerError(
                "x", response=httpx.Response(500, request=_req()), body=None
            ),
            "500",
        ),
        (anthropic.APIConnectionError(request=_req()), "could not reach"),
    ],
)
def test_anthropic_errors_become_unavailable(error, message):
    client, _ = fake_client(error=error)
    model = AnthropicModel(api_key="unused", model="claude-opus-5-5", client=client)
    with pytest.raises(ArchitectUnavailable, match=message):
        model.draft(system="s", prompt="p", schema={})


@pytest.mark.parametrize(
    "response, exc, message",
    [
        (resp(None, stop="refusal"), ArchitectRefused, "declined"),
        (resp("{", stop="max_tokens"), ArchitectError, "cut off"),
        (resp(None), ArchitectError, "no draft"),
        (resp("not json"), ArchitectError, "not valid JSON"),
        (resp("[1, 2]"), ArchitectError, "wrong shape"),
    ],
)
def test_anthropic_bad_responses(response, exc, message):
    client, _ = fake_client(response)
    model = AnthropicModel(api_key="unused", model="claude-opus-5-5", client=client)
    with pytest.raises(exc, match=message):
        model.draft(system="s", prompt="p", schema={})


# ------------------------------------------------------------------ settings
def test_rules_is_the_default_and_free(tmp_path, registry):
    s = Settings(snapshot_dir=tmp_path)
    assert s.ai_provider == "rules"
    assert build_model(s, registry).name == "rules"
    assert build_model(Settings(snapshot_dir=tmp_path, ai_provider="off"), registry) is None


def test_anthropic_needs_key_and_paid_opt_in(tmp_path, registry):
    with pytest.raises(ValidationError, match="needs CYBERLAB_AI_API_KEY"):
        Settings(snapshot_dir=tmp_path, ai_provider="anthropic")
    with pytest.raises(ValidationError, match="paid service"):
        Settings(snapshot_dir=tmp_path, ai_provider="anthropic", ai_api_key=SecretStr("k"))
    s = Settings(
        snapshot_dir=tmp_path,
        ai_provider="anthropic",
        ai_api_key=SecretStr("sk-test"),
        allow_paid_providers=True,
    )
    model = build_model(s, registry)
    assert model.name == "anthropic" and model.model == "claude-opus-5-5"
    assert "sk-test" not in repr(s)  # the key never shows up in logs


# ------------------------------------------------------------------ API
def test_ai_status_reports_planner(client):
    r = client.get("/v1/system/ai", headers=auth())
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "rules" and body["status"] == "READY" and body["paid"] is False
    assert client.get("/v1/system/ai").status_code == 401


def test_draft_endpoint_returns_yaml_and_findings_and_audits(client):
    r = client.post(
        "/v1/architect/draft", json={"prompt": "packet analysis on kali"}, headers=auth()
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert not body["refused"] and body["plan"]["valid"]
    manifest = yaml.safe_load(body["manifestYaml"])
    assert manifest["spec"]["environments"][0]["baseImage"] == "kali-rolling"
    # it can be validated as-is by the normal manifest endpoint
    v = client.post(
        "/v1/manifests/validate",
        content=body["manifestYaml"],
        headers={**auth(), "Content-Type": "application/yaml"},
    )
    assert v.status_code == 200 and v.json()["valid"]
    events = client.get("/v1/audit", headers=auth()).json()
    ev = next(e for e in events if e["action"] == "architect.draft")
    assert ev["detail"] == {
        "provider": "rules",
        "promptChars": len("packet analysis on kali"),
        "refused": False,
        "valid": True,
    }
    assert "packet" not in json.dumps(ev)  # the prompt itself is not stored


def test_draft_endpoint_refusal_and_errors(client):
    r = client.post("/v1/architect/draft", json={"prompt": "hack google.com"}, headers=auth())
    assert r.status_code == 200 and r.json()["refused"] is True
    assert "written permission" in r.json()["reason"]
    assert (
        client.post("/v1/architect/draft", json={"prompt": "x"}, headers=auth()).status_code == 422
    )
    assert client.post("/v1/architect/draft", json={"prompt": "lab"}).status_code == 401


def test_draft_endpoint_never_creates_anything(client):
    client.post("/v1/architect/draft", json={"prompt": "two machines"}, headers=auth())
    assert client.get("/v1/workspaces", headers=auth()).json() == []


def test_draft_endpoint_when_off(settings):
    from fastapi.testclient import TestClient

    from cyberlab.main import create_app

    with TestClient(create_app(settings.model_copy(update={"ai_provider": "off"}))) as c:
        assert c.get("/v1/system/ai", headers=auth()).json()["status"] == "OFF"
        r = c.post("/v1/architect/draft", json={"prompt": "a lab"}, headers=auth())
        assert r.status_code == 503


def test_draft_endpoint_service_down(client):
    client.app.state.architect = SimpleNamespace(
        name="anthropic",
        draft=lambda **_: (_ for _ in ()).throw(
            ArchitectUnavailable("could not reach the AI service")
        ),
    )
    r = client.post("/v1/architect/draft", json={"prompt": "a lab"}, headers=auth())
    assert r.status_code == 503 and "could not reach" in r.json()["detail"]

from decepticon_core.types.roe import MachineEnforcement, evaluate_target


def test_bug_bounty_wildcard_allows_nested_subdomains() -> None:
    rules = MachineEnforcement.from_dict(
        {"in_scope": [{"target": "*.anduril.dev", "type": "domain-glob"}]}
    )

    assert evaluate_target("sentry.anduril.dev", rules).allow
    assert evaluate_target("internal.sentry.anduril.dev", rules).allow


def test_bug_bounty_wildcard_does_not_expand_to_apex_or_lookalike() -> None:
    rules = MachineEnforcement.from_dict(
        {"in_scope": [{"target": "*.anduril.dev", "type": "domain-glob"}]}
    )

    assert not evaluate_target("anduril.dev", rules).allow
    assert not evaluate_target("internal.sentry.anduril.dev.evil.test", rules).allow
    assert not evaluate_target("evil-anduril.dev", rules).allow


def test_explicit_exclusion_overrides_recursive_wildcard() -> None:
    rules = MachineEnforcement.from_dict(
        {
            "in_scope": [
                {"target": "*.anduril.dev", "type": "domain-glob"},
            ],
            "out_of_scope": [
                {"target": "internal.sentry.anduril.dev", "type": "host"},
            ],
        }
    )

    decision = evaluate_target("internal.sentry.anduril.dev", rules)

    assert not decision.allow
    assert decision.reason_code == "OUT_OF_SCOPE"


def test_root_url_rule_matches_its_host_without_allowing_lookalikes() -> None:
    rules = MachineEnforcement.from_dict(
        {"mode": "enforce", "in_scope": [{"target": "https://decepticon.red/", "type": "auto"}]}
    )

    assert evaluate_target("decepticon.red", rules).allow
    assert not evaluate_target("other.decepticon.red", rules).allow
    assert not evaluate_target("decepticon.red.evil.test", rules).allow


def test_path_specific_url_cannot_become_host_wide_scope() -> None:
    rules = MachineEnforcement.from_dict(
        {"mode": "enforce", "in_scope": [{"target": "https://decepticon.red/admin", "type": "auto"}]}
    )

    assert not evaluate_target("decepticon.red", rules).allow


def test_root_url_exclusion_overrides_host_allow_rule() -> None:
    rules = MachineEnforcement.from_dict(
        {
            "in_scope": [{"target": "decepticon.red", "type": "host"}],
            "out_of_scope": [{"target": "https://decepticon.red/", "type": "auto"}],
        }
    )

    assert evaluate_target("decepticon.red", rules).reason_code == "OUT_OF_SCOPE"

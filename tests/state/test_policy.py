import pytest

from studio.state import policy as policy_module
from studio.state.policy import PolicyError, field_errors, issues, parse_policy
from tests.state.helpers import PAGES, POLICY, field

CLIENT_ONLY = parse_policy(POLICY)
WITH_COUNTERSIGNER = parse_policy({"countersigner": True, "required": []})


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"countersigner": False}, "exactly the keys"),
        ({"countersigner": "no", "required": []}, "true or false"),
        ({"countersigner": False, "required": {}}, "must be a list"),
        ({"countersigner": False, "required": [{"signer": 0}]}, "signer and type"),
        ({"countersigner": False, "required": [{"signer": True, "type": "text"}]}, "signer 0"),
        ({"countersigner": False, "required": [{"signer": 1, "type": "text"}]}, "countersigner"),
        ({"countersigner": True, "required": [{"signer": 1, "type": "text-merge"}]}, "type is"),
        (
            {"countersigner": False, "required": [{"signer": 0, "type": "text"}] * 2},
            "duplicate",
        ),
    ],
)
def test_a_malformed_policy_is_refused(raw, message):
    with pytest.raises(PolicyError, match=message):
        parse_policy(raw)


def test_the_roles_follow_the_policy():
    assert CLIENT_ONLY.roles == ["Client"]
    assert WITH_COUNTERSIGNER.roles == ["Client", "CounterSigner"]
    assert parse_policy(CLIENT_ONLY.to_dict()) == CLIENT_ONLY


MERGE = {"type": "text-merge", "signer": "sender", "name": "email"}


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"type": "stamp"}, "unknown type"),
        ({"extra": 1}, "exactly the keys"),
        ({"type": "text-merge", "signer": "sender"}, "exactly the keys"),  # no name
        ({**MERGE, "signer": 0}, "a merge field has signer"),
        ({**MERGE, "name": "ssn"}, "a merge field has signer"),
        ({"type": "text", "signer": "sender"}, "only merge fields"),
        ({"signer": 1}, "not allowed by the policy"),
        ({"signer": True}, "not allowed by the policy"),
        ({"required": "yes"}, "required"),
        ({"page": 9}, "not in the source"),
        ({"page": 1.0}, "integers"),
        ({"page": 2}, "rotated"),
        ({"page": 3}, "could not be measured"),
        ({"x": float("nan")}, "finite"),
        ({"x": "72"}, "finite"),
        ({"width": 0}, "positive size"),
        ({"x": -1}, "starts on the page"),
        ({"x": 500, "width": 200}, "outside the page"),
        ({"y": 780, "height": 24}, "outside the page"),
        ({"id": "a b"}, "id is"),
    ],
)
def test_a_field_that_cannot_exist_is_an_error(overrides, message):
    (error,) = field_errors([field(**overrides)], CLIENT_ONLY, PAGES)
    assert error.startswith("field 0:") and message in error


def test_valid_fields_have_no_errors_including_a_countersigner_the_policy_allows():
    fields = [field(), field(id="f2", signer=1), field(id="f3", **MERGE)]
    assert field_errors(fields, WITH_COUNTERSIGNER, PAGES) == []
    assert field_errors([field(x=412.0, width=200.0)], CLIENT_ONLY, PAGES) == []  # at the edge


def test_field_ids_are_unique_and_the_map_is_bounded(monkeypatch):
    assert "duplicate id" in field_errors([field(), field()], CLIENT_ONLY, PAGES)[0]
    assert field_errors({"f1": field()}, CLIENT_ONLY, PAGES) == ["fields must be a list"]
    monkeypatch.setattr(policy_module, "MAX_FIELDS", 1)
    assert "at most 1" in field_errors([field(), field(id="f2")], CLIENT_ONLY, PAGES)[0]


def test_an_empty_map_is_an_issue():
    assert issues([], WITH_COUNTERSIGNER) == ["the field map is empty"]


def test_a_requirement_is_met_only_by_a_required_field_of_that_signer_and_type():
    assert issues([field(required=False)], CLIENT_ONLY) == [
        "the policy requires a required signature field for Client"
    ]
    assert issues([field(type="initials")], CLIENT_ONLY) != []
    assert issues([field()], CLIENT_ONLY) == []


def test_there_is_no_universal_signature_rule():
    no_requirements = parse_policy({"countersigner": False, "required": []})
    assert issues([field(type="text", required=False)], no_requirements) == []

"""backend/schema.py flags; it must never rewrite a draft, and it must catch the failures the
nine-game run actually produced."""
import pytest
from conftest import record


@pytest.fixture
def S():
    import backend.schema as S
    return S


def test_clean_draft_has_no_structural_flags(S):
    f = S.check(record(), "some entry text")
    assert f["schema"] == [] and f["inconsistent"] == []


def test_missing_section_is_caught(S):
    """Every field has a default, so an absent section validates silently unless presence is
    checked. This is the failure the complex tier produced: five sections nested inside gates."""
    r = record(); del r["route"]; del r["dig"]
    problems = " ".join(S.check(r, "")["schema"])
    assert "route" in problems and "dig" in problems


def test_misnesting_is_caught(S):
    r = record(); r["gates"]["route"] = r.pop("route")
    assert any("gates.route" in x for x in S.check(r, "")["schema"])


def test_invalid_op_is_caught(S):
    r = record(); r["dig"]["M"]["op"] = "Kept/Transformed"
    assert any("dig.M.op" in x for x in S.check(r, "")["schema"])


def test_altnames_may_be_a_list(S):
    """Models return either; normalise() joins a list. Flagging it on every draft was noise."""
    assert S.check(record(altNames=["a", "b"]), "")["schema"] == []


def test_g3_contradicting_its_own_dig_is_caught(S):
    r = record()
    r["gates"]["G3"]["pass"] = False                      # but S is Kept
    assert any("G3" in x for x in S.check(r, "")["inconsistent"])


def test_g3_classes_must_name_surviving_classes(S):
    r = record()
    r["dig"]["S"]["op"] = "Removed"; r["dig"]["M"]["op"] = "Kept"
    r["gates"]["G3"]["classes"] = ["S"]                   # S does not survive
    assert any("classes" in x for x in S.check(r, "")["inconsistent"])


def test_human_only_fields_are_flagged(S):
    """Design rule 2: the model never sets verified or the review flags."""
    r = record(); r["distinctiveness"]["verified"] = True
    assert "distinctiveness.verified" in S.check(r, "")["humanOnly"]


def test_a_clean_v05_draft_sets_no_human_only_field(S):
    """The v0.5 skeleton prints level null and verified false; that is not a fault."""
    assert S.check(record(), "")["humanOnly"] == []


# ---- evidence ---------------------------------------------------------------

ENTRY = ("The players stand in a circle and the leader throws the stone onto a numbered square.\n"
         "Whoever steps on a line is out of the game for that round.")


def test_verbatim_quotation_scores_one(S):
    assert S.coverage("the leader throws the stone onto a numbered square", ENTRY) == 1.0


def test_prose_about_the_entry_scores_zero(S):
    assert S.coverage("The rules are legible without cultural knowledge.", ENTRY) < 0.4


def test_stitched_quotation_still_scores(S):
    """Model quotations join fragments with an ellipsis; requiring one span flags almost all."""
    q = '"the players stand in a circle... whoever steps on a line is out"'
    assert S.coverage(q, ENTRY) > 0.8


def test_line_wrapped_entry_still_matches(S):
    """The corpus hard-wraps mid-sentence; matching must be whitespace-insensitive."""
    wrapped = ENTRY.replace(" onto a numbered", "\n     onto a numbered")
    assert S.coverage("throws the stone onto a numbered square", wrapped) == 1.0


def test_check_never_mutates_the_draft(S):
    import copy
    r = record(); before = copy.deepcopy(r)
    S.check(r, ENTRY)
    assert r == before, "schema.check modified the draft it was given"

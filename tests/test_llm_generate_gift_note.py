"""
Regression test: llm.generate_gift_note's `def` line had been dropped
in a previous edit, leaving its body as dead, unreachable code sitting
inside _heuristic_find_gifts instead of its own function. The module
had no `generate_gift_note` attribute at all, even though three call
sites depend on it:
  - routes/contacts.py's new_order (the "let AI write the note" option
    on a manually-placed gift order)
  - services/suggestion_engine.py, twice (the automated flow/campaign
    note-generation path)

None of this was caught by the existing suite because nothing
exercised those three call sites directly. This test doesn't need a
live Anthropic API key -- with none configured (the test config's
default), generate_gift_note falls through to its plain-template
fallback, which is enough to prove the function exists, is callable
with the (contact, event, gift_item, prompt_hint=None) signature used
at every call site, and returns a usable string instead of raising
AttributeError.
"""
from types import SimpleNamespace

from app.services import llm


def make_contact(name="Jane Doe", head_first="Jane", head_last="Doe"):
    # A lightweight stand-in, not a real Contact model -- generate_gift_note
    # (and _practice_type_label, which it calls) touch .household_name,
    # .org, and .primary_person() (for the head-of-household's name --
    # see the regression this guards against below).
    head = SimpleNamespace(first_name=head_first, last_name=head_last)
    return SimpleNamespace(household_name=name, org=None, primary_person=lambda: head)


def make_event(label="birthday"):
    return SimpleNamespace(display_label=lambda: label)


def make_gift_item(name="Starbucks Gift Card"):
    return SimpleNamespace(name=name)


def test_generate_gift_note_exists_with_expected_signature():
    import inspect
    assert hasattr(llm, "generate_gift_note")
    params = list(inspect.signature(llm.generate_gift_note).parameters)
    assert params == ["contact", "event", "gift_item", "prompt_hint"]


def test_generate_gift_note_with_event_falls_back_to_template(app):
    with app.app_context():
        note = llm.generate_gift_note(make_contact("Jane Doe"), make_event("birthday"), make_gift_item())
        assert "Jane Doe" in note
        assert "birthday" in note


def test_generate_gift_note_with_no_event_falls_back_to_template(app):
    """The one-off manual order path (routes/contacts.py) always calls
    this with event=None, since a one-off order isn't tied to any
    timeline event."""
    with app.app_context():
        note = llm.generate_gift_note(make_contact("Jane Doe"), None, make_gift_item())
        assert "Jane Doe" in note


def test_generate_gift_note_appends_prompt_hint(app):
    with app.app_context():
        note = llm.generate_gift_note(
            make_contact("Jane Doe"), None, make_gift_item(), prompt_hint="Enjoy!"
        )
        assert "Enjoy!" in note


def test_generate_gift_note_uses_head_of_household_name_not_title(app):
    """Regression: the note used to address contact.household_name (the
    household title, e.g. "The Starks") instead of the actual head of
    household's name (e.g. "Jason Stark"). It should reference the
    person, not the household title."""
    with app.app_context():
        contact = make_contact("The Starks", head_first="Jason", head_last="Stark")
        note = llm.generate_gift_note(contact, None, make_gift_item())
        assert "Jason Stark" in note
        assert "The Starks" not in note

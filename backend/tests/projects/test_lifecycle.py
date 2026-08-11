"""services/projects/lifecycle.py — the project status state machine.
Exhaustive: every state x every state is asserted either valid or invalid,
not just a happy-path sample, since this is the one place that decides
whether a transition is legal at all."""

from types import SimpleNamespace

import pytest

from app.core.errors import AppError
from app.services.projects import lifecycle

ALL_STATUSES = (
    "draft", "submitted", "approved", "active", "paused", "completed", "closed", "rejected", "cancelled",
)


def _project(status: str):
    return SimpleNamespace(status=status)


@pytest.mark.parametrize(
    "frm,to",
    [(frm, to) for frm, tos in lifecycle.VALID_TRANSITIONS.items() for to in tos],
)
def test_every_declared_transition_succeeds(frm, to):
    project = _project(frm)
    lifecycle.transition(project, to)
    assert project.status == to


@pytest.mark.parametrize(
    "frm,to",
    [
        (frm, to)
        for frm in ALL_STATUSES
        for to in ALL_STATUSES
        if to not in lifecycle.VALID_TRANSITIONS.get(frm, set())
    ],
)
def test_every_undeclared_transition_is_rejected(frm, to):
    project = _project(frm)
    with pytest.raises(AppError) as exc_info:
        lifecycle.transition(project, to)
    assert exc_info.value.status_code == 409
    assert project.status == frm  # rejected transition must not mutate state


def test_closed_and_cancelled_are_terminal():
    assert lifecycle.VALID_TRANSITIONS["closed"] == set()
    assert lifecycle.VALID_TRANSITIONS["cancelled"] == set()


def test_rejected_can_only_go_back_to_draft():
    assert lifecycle.VALID_TRANSITIONS["rejected"] == {"draft"}


def test_approval_then_auto_activate_path():
    project = _project("draft")
    lifecycle.transition(project, "submitted")
    lifecycle.transition(project, "approved")
    lifecycle.transition(project, "active")
    assert project.status == "active"

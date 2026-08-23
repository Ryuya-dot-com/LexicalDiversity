import os
import time
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from ldfreq.server_only_gate import SERVER_ONLY_CONTROL_PROFILE


SERVER_LIST_NAME = "BNC/COCA 25,000 word families (Nation)"
GUARD_KEY = "_server_only_query_guard"


pytestmark = [
    pytest.mark.skipif(os.name != "posix", reason="POSIX process isolation"),
    pytest.mark.skipif(
        os.environ.get("LDFREQ_RUN_SERVER_INTEGRATION") != "1",
        reason=(
            "operator-only integration requires an installed, verified Nation "
            "artifact and LDFREQ_RUN_SERVER_INTEGRATION=1"
        ),
    ),
]


def _frequency_list(app):
    return next(
        item for item in app.selectbox if item.label == "Frequency list"
    )


def _click(app, label):
    next(button for button in app.button if button.label == label).click()


def test_server_only_guard_survives_reruns_counts_rejections_and_is_deleted():
    distinct = (
        "alpha bravo charlie delta echo foxtrot golf hotel india juliet "
        "kilo lima mike november oscar papa quebec romeo sierra tango"
    )
    eligible_text = " ".join([distinct] * 5)
    environment = {
        "LDFREQ_ALLOW_LOCAL_RESTRICTED": "0",
        "LDFREQ_SERVER_ONLY_RESOURCE_IDS": "nation_bnc_coca_families",
        "LDFREQ_SERVER_ONLY_RIGHTS_ACKNOWLEDGED": "1",
        "LDFREQ_SERVER_ONLY_CONTROL_ATTESTATION": SERVER_ONLY_CONTROL_PROFILE,
        "LDFREQ_SERVER_ONLY_CONTROL_EVIDENCE_ID": "GRC-2026-08-24-001",
    }

    with patch.dict(os.environ, environment, clear=False):
        app = AppTest.from_file("app.py")
        app.run(timeout=30)
        assert not app.exception
        selector = _frequency_list(app)
        assert SERVER_LIST_NAME in selector.options
        selector.set_value(SERVER_LIST_NAME)
        app.text_area[0].set_value(eligible_text)
        _click(app, "Analyze")
        app.run(timeout=90)

        assert not app.exception
        state = dict(app.session_state[GUARD_KEY])
        assert state["attempts"] == 1
        assert state["successes"] == 1
        assert state["consecutive_failures"] == 0
        assert state["short_rejections"] == 0
        assert 180 <= state["credits"] < 181
        assert eligible_text not in repr(app.session_state.filtered_state)

        # An ordinary Streamlit rerun neither spends nor resets the budget.
        app.run(timeout=30)
        assert dict(app.session_state[GUARD_KEY]) == state

        app.text_area[0].set_value("common")
        _click(app, "Analyze")
        app.run(timeout=90)

        assert not app.exception
        rejected = dict(app.session_state[GUARD_KEY])
        assert rejected["attempts"] == 2
        assert rejected["successes"] == 1
        assert rejected["consecutive_failures"] == 1
        assert rejected["short_rejections"] == 1
        assert "common" not in repr(app.session_state.filtered_state)

        # Exercise the serving adapter's Retry-After-equivalent denial without
        # waiting through ten full analyses.
        exhausted = dict(rejected)
        exhausted.update({
            "credits": 0.0,
            "updated_at": time.monotonic(),
            "blocked_until": 0.0,
            "consecutive_failures": 0,
        })
        app.session_state[GUARD_KEY] = exhausted
        app.text_area[0].set_value(eligible_text)
        _click(app, "Analyze")
        app.run(timeout=30)

        assert not app.exception
        assert any("Retry-After:" in item.value for item in app.warning)
        assert app.session_state[GUARD_KEY]["attempts"] == 2
        assert eligible_text not in repr(app.session_state.filtered_state)

        _click(app, "Delete data")
        app.run(timeout=30)
        assert GUARD_KEY not in app.session_state.filtered_state

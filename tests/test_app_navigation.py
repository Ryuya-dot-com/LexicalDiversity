import os

import pytest
from streamlit.testing.v1 import AppTest

from ldfreq.privacy import sensitive_paths


pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX process isolation")


def _click_analyze(app):
    for button in app.button:
        if button.label == "Analyze":
            button.click()
            return
    raise AssertionError("Analyze button not found")


def _click_delete(app):
    for button in app.button:
        if button.label == "Delete data":
            button.click()
            return
    raise AssertionError("Delete data button not found")


def _set_detail_view(app, value):
    for group in app.button_group:
        if group.label == "Detail view":
            group.set_value(value)
            return
    raise AssertionError("Detail view control not found")


def test_detail_view_switches_keep_analysis_results_visible():
    app = AppTest.from_file("app.py")
    app.run(timeout=30)
    assert not any(selectbox.label == "Counting unit" for selectbox in app.selectbox)
    assert any(
        "remove names, student numbers" in item.value for item in app.warning
    )
    assert any("Submit only synthetic or already-public text" in item.value for item in app.error)
    frequency_lists = next(
        selectbox for selectbox in app.selectbox
        if selectbox.label == "Frequency list"
    )
    assert set(frequency_lists.options) == {
        "New JACET8000",
        "NGSL (New General Service List)",
    }

    _click_analyze(app)
    app.run(timeout=60)

    assert not app.exception
    assert app.session_state["input_text"] == ""
    assert "_server_only_query_guard" not in app.session_state.filtered_state
    assert sensitive_paths(app.session_state["analysis_state"]) == []
    assert "The study of lexical diversity" not in repr(app.session_state.filtered_state)
    assert not any("**Unit:**" in item.value for item in app.markdown)
    assert any(group.label == "Detail view" for group in app.button_group)

    assert any("Panel A and Panel B start from the same tokenized text" in item.value for item in app.info)

    _set_detail_view(app, "Panel B: List coverage")
    app.run(timeout=60)

    assert not app.exception
    assert any(header.value == "Lexical Frequency Profile" for header in app.subheader)
    assert not any("Set options in the sidebar" in item.value for item in app.info)

    _set_detail_view(app, "Semantic network")
    app.run(timeout=60)

    assert not app.exception
    assert any(header.value == "Open semantic-network indices" for header in app.subheader)

    _set_detail_view(app, "Profile")
    app.run(timeout=60)

    assert not app.exception
    assert any(header.value == "Index profile radar" for header in app.subheader)
    assert not any("Set options in the sidebar" in item.value for item in app.info)

    _set_detail_view(app, "Export")
    app.run(timeout=60)

    assert not app.exception
    assert len(app.json) == 1
    assert not any("Set options in the sidebar" in item.value for item in app.info)

    _click_delete(app)
    app.run(timeout=30)

    assert "analysis_state" not in app.session_state.filtered_state
    assert app.session_state["input_text"] == ""
    assert any(
        "Input and retained analysis results were deleted." in item.value
        for item in app.success
    )
    assert any("Set options in the sidebar" in item.value for item in app.info)

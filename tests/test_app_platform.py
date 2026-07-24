import os

import pytest
from streamlit.testing.v1 import AppTest


@pytest.mark.skipif(os.name == "posix", reason="non-POSIX host behavior")
def test_non_posix_host_explains_and_disables_analysis():
    app = AppTest.from_file("app.py")
    app.run(timeout=30)

    assert not app.exception
    analyze = next(button for button in app.button if button.label == "Analyze")
    assert analyze.disabled
    assert any(
        "privacy-preserving one-shot worker requires a POSIX host" in item.value
        for item in app.error
    )

from __future__ import annotations

from rnaseq_workflow.core.cancellation import CancellationToken


def test_cancellation_token():
    token = CancellationToken()

    assert not token.is_cancelled()
    token.cancel()
    assert token.is_cancelled()

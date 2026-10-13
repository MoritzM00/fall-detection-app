"""Shared fixtures for routing inference HTTP traffic to in-process handlers."""

import httpx
import pytest

import scripts.validate_vllm_contract as vllm_contract
from fall_detection import prediction, serving_status


@pytest.fixture
def route_inference(monkeypatch):
    """Send every same-origin inference client request to a handler; the origin check stays on."""

    def install(handler):
        real = prediction.same_origin_client

        def routed(base_url, timeout, transport=None):
            return real(base_url, timeout, httpx.MockTransport(handler))

        for module in (prediction, serving_status, vllm_contract):
            monkeypatch.setattr(module, "same_origin_client", routed)

    return install


@pytest.fixture(autouse=True)
def no_real_modal_control_plane(monkeypatch):
    """Fail any test that would reach the developer's real Modal app through its CLI login.

    Tests that need these calls replace them; the function objects themselves stay
    importable for tests of their own behavior.
    """

    def refuse(*_args, **_kwargs):
        raise AssertionError("tests must not call the real Modal control plane")

    monkeypatch.setattr(serving_status, "modal_stop_containers", refuse)
    monkeypatch.setattr(serving_status, "modal_container_stats", refuse)

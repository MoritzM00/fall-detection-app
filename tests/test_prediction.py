import httpx
import pytest

from fall_detection.config import Settings
from fall_detection.prediction import (
    ACTIVITY_LABELS,
    InferenceClient,
    InferenceServiceError,
    ParsedAnswer,
    PredictionParseError,
    build_inference_payload,
    parse_activity_label,
    parse_answer,
    require_encrypted_transport,
    sample_timestamps,
)


@pytest.mark.parametrize("chain_of_thought", [False, True])
@pytest.mark.parametrize("label", ACTIVITY_LABELS)
def test_parser_accepts_every_canonical_label(label: str, chain_of_thought: bool) -> None:
    for response in (f"The best answer is: {label}", f" \n{label}\n"):
        assert parse_activity_label(response, chain_of_thought=chain_of_thought) == label


@pytest.mark.parametrize(
    ("response", "label"),
    [
        # Formatting the research parser tolerates: case, punctuation, markdown, word forms.
        ("The best answer is: fall.", "fall"),
        ("**The best answer is: fall**", "fall"),
        ("the best answer is - Sits Down", "sit_down"),
        # The research -ing form does not double consonants: "sitting down" reads as sitting.
        ("The best answer is: sitting down", "sitting"),
        ("The best answer is: standing up", "stand_up"),
        ("The best answer is: lie-down", "lie_down"),
        ("The best answer is: walking. The person keeps a steady pace.", "walk"),
        ('{"label":"walk"}', "walk"),
        ("The person is kneeling.", "kneeling"),
    ],
)
def test_parser_reads_the_answer_like_the_research_keyword_parser(response, label):
    assert parse_activity_label(response) == label


@pytest.mark.parametrize(
    "response",
    [
        "unknown",
        "The best answer is: unknown",
        "The best answer is: fall or fallen",
        "walk or fall",
        "The best answer is: ",
        "",
    ],
)
def test_parser_fails_instead_of_using_other(response: str) -> None:
    with pytest.raises(PredictionParseError):
        parse_activity_label(response)


@pytest.mark.parametrize(
    "response",
    [
        "The person loses balance and drops quickly.\nThe best answer is: fall",
        "<think>Sudden downward motion.</think>\n\nThe best answer is: fall\n",
        # Qwen chat templates may open the think tag in the prompt itself.
        "Sudden downward motion, no bracing.\n</think>\nThe best answer is: fall",
        "<think>The person is standing, then drops.</think>\nfall",
        "Downward motion, then stillness.\n\n**Final Answer:** fall",
        "Downward motion. The answer is fall.",
        "The best answer is: fall\nOn reflection the person may be sitting.",
    ],
)
def test_chain_of_thought_parser_reads_the_answer_after_reasoning(response):
    assert parse_activity_label(response, chain_of_thought=True) == "fall"


@pytest.mark.parametrize(
    "response",
    [
        "<think>Sudden downward motion. The best answer is: fall",
        "<think>Reasoning only.</think>",
        "The person was standing and then sitting.",
        "",
    ],
)
def test_chain_of_thought_parser_fails_without_one_answer(response):
    with pytest.raises(PredictionParseError):
        parse_activity_label(response, chain_of_thought=True)


def test_parsed_answer_separates_reasoning_from_the_answer():
    parsed = parse_answer(
        "<think>Drops fast.</think>\nNo bracing.\n\nThe best answer is: fall.\n",
        chain_of_thought=True,
    )
    assert parsed == ParsedAnswer("fall", "Drops fast.\n\nNo bracing.", "The best answer is: fall")
    assert parse_answer("The best answer is: walk") == ParsedAnswer(
        "walk", None, "The best answer is: walk"
    )
    assert parse_answer(" walk\n") == ParsedAnswer("walk", None, "walk")


def test_only_the_best_answer_marker_counts_without_chain_of_thought():
    # Research applies the other markers only to chain-of-thought content.
    with pytest.raises(PredictionParseError):
        parse_activity_label("Not sitting. Final answer: fall")
    assert parse_activity_label("Not sitting. Final answer: fall", chain_of_thought=True) == "fall"


def test_payload_puts_the_video_before_the_prompt_unless_saved_otherwise():
    content = build_inference_payload("m", "data:", "p")["messages"][0]["content"]
    assert [part["type"] for part in content] == ["video_url", "text"]
    legacy = build_inference_payload("m", "data:", "p", video_first=False)
    assert [part["type"] for part in legacy["messages"][0]["content"]] == ["text", "video_url"]


def test_payload_sends_only_the_sampling_values_that_were_set():
    base = {"temperature": 0.5, "max_tokens": 64}
    default = build_inference_payload("m", "data:", "p", generation=base)
    assert not {"top_p", "top_k", "seed"} & default.keys()
    explicit = build_inference_payload(
        "m", "data:", "p", generation={**base, "top_p": 0.95, "top_k": 20, "seed": 0}
    )
    assert (explicit["top_p"], explicit["top_k"], explicit["seed"]) == (0.95, 20, 0)


def test_sample_timestamps_preserve_window_edges() -> None:
    timestamps = sample_timestamps(4.0, 6.0)
    assert len(timestamps) == 16
    assert timestamps[0] == 4.0
    assert timestamps[-1] == 6.0


def test_sample_timestamps_reject_invalid_window() -> None:
    with pytest.raises(ValueError):
        sample_timestamps(2.0, 2.0)


@pytest.mark.parametrize("start,end", [(-1, 2), (0, float("inf")), (float("nan"), 2)])
def test_sample_timestamps_reject_invalid_numbers(start, end) -> None:
    with pytest.raises(ValueError):
        sample_timestamps(start, end)


def respond(response: httpx.Response) -> httpx.MockTransport:
    return httpx.MockTransport(lambda _request: response)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, text="unavailable"),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"id": "x", "choices": []}),
        httpx.Response(200, json={"id": "x", "choices": [{"message": {"content": ["not text"]}}]}),
    ],
)
def test_inference_boundary_rejects_http_and_envelope_errors(response):
    with pytest.raises(InferenceServiceError, match="inference"):
        InferenceClient("http://test", transport=respond(response)).complete({})


def test_inference_boundary_reports_timeout():
    def timeout(_request):
        raise httpx.ReadTimeout("timed out")

    client = InferenceClient("http://test", transport=httpx.MockTransport(timeout))
    with pytest.raises(InferenceServiceError, match="timed out"):
        client.complete({})


def test_inference_client_sends_configured_credentials():
    sent = {}

    def handler(request):
        sent.update(request.headers)
        return httpx.Response(200, json={"id": "x", "choices": [{"message": {"content": "fall"}}]})

    InferenceClient(
        "http://test",
        auth_headers={"Authorization": "Bearer dummy-key"},
        transport=httpx.MockTransport(handler),
    ).complete({})
    assert sent["authorization"] == "Bearer dummy-key"


COMPLETION = {"id": "x", "choices": [{"message": {"content": "The best answer is: fall"}}]}
DUMMY_PROXY_HEADERS = {"Modal-Key": "dummy-key", "Modal-Secret": "dummy-secret"}


def test_inference_client_follows_same_origin_303_to_poll_for_the_result():
    seen = []

    def handler(request):
        seen.append((request.method, str(request.url), request.headers.get("modal-key")))
        if request.method == "POST":
            return httpx.Response(303, headers={"Location": "/v1/result?call=dummy"})
        return httpx.Response(200, json=COMPLETION)

    response = InferenceClient(
        "https://gpu.test/v1",
        auth_headers=DUMMY_PROXY_HEADERS,
        transport=httpx.MockTransport(handler),
    ).complete({"frames": "dummy"})
    assert response.content == "The best answer is: fall"
    assert seen == [
        ("POST", "https://gpu.test/v1/chat/completions", "dummy-key"),
        ("GET", "https://gpu.test/v1/result?call=dummy", "dummy-key"),
    ]


@pytest.mark.parametrize(
    "location",
    [
        "https://elsewhere.test/v1/chat/completions",
        "http://gpu.test/v1/chat/completions",
        "https://gpu.test:8443/v1/chat/completions",
    ],
)
@pytest.mark.parametrize("status", [302, 303, 307, 308])
def test_inference_client_refuses_redirects_off_the_configured_origin(location, status):
    configured, foreign = [], []

    def handler(request):
        if (request.url.scheme, request.url.host, request.url.port) != ("https", "gpu.test", None):
            foreign.append(request)
            return httpx.Response(200, json=COMPLETION)
        configured.append(request)
        return httpx.Response(status, headers={"Location": location})

    client = InferenceClient(
        "https://gpu.test/v1",
        auth_headers=DUMMY_PROXY_HEADERS,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(InferenceServiceError, match="Refusing redirect") as refused:
        client.complete({"frames": "dummy"})
    assert len(configured) == 1
    assert foreign == []
    assert "dummy" not in str(refused.value)


def test_settings_build_auth_headers_and_hide_secrets(monkeypatch):
    monkeypatch.setenv("FALL_DETECTION_INFERENCE_API_KEY", "api-secret")
    monkeypatch.setenv("FALL_DETECTION_INFERENCE_PROXY_KEY", "wk-id")
    monkeypatch.setenv("FALL_DETECTION_INFERENCE_PROXY_SECRET", "ws-secret")
    settings = Settings.from_env()
    assert settings.inference_auth_headers() == {
        "Authorization": "Bearer api-secret",
        "Modal-Key": "wk-id",
        "Modal-Secret": "ws-secret",
    }
    assert "secret" not in repr(settings)


def test_settings_reject_half_configured_proxy_token(monkeypatch):
    monkeypatch.setenv("FALL_DETECTION_INFERENCE_PROXY_KEY", "wk-id")
    monkeypatch.delenv("FALL_DETECTION_INFERENCE_PROXY_SECRET", raising=False)
    with pytest.raises(ValueError, match="together"):
        Settings.from_env().inference_auth_headers()


@pytest.mark.parametrize(
    ("usage", "expected"),
    [
        ({"prompt_tokens": 1700, "completion_tokens": 6}, (1700, 6)),
        (None, (None, None)),
        ({"prompt_tokens": "1700", "completion_tokens": True}, (None, None)),
        ({"prompt_tokens": -1}, (None, None)),
    ],
)
def test_inference_client_reads_optional_token_usage(usage, expected):
    body = {"id": "x", "choices": [{"message": {"content": "fall"}}]}
    if usage is not None:
        body["usage"] = usage
    result = InferenceClient(
        "http://test", transport=respond(httpx.Response(200, json=body))
    ).complete({})
    assert (result.prompt_tokens, result.completion_tokens) == expected


@pytest.mark.parametrize(
    "base_url",
    ["https://gpu.example/v1", "http://127.0.0.1:8001/v1", "http://localhost:8001/v1"],
)
def test_encrypted_or_local_transport_is_allowed(base_url):
    require_encrypted_transport(base_url)


@pytest.mark.parametrize(
    "base_url", ["http://gpu.example/v1", "http://10.0.0.5:8000/v1", "ftp://gpu.example/v1"]
)
def test_plaintext_transport_off_this_machine_is_refused(base_url):
    with pytest.raises(InferenceServiceError, match="Refusing to send video frames"):
        require_encrypted_transport(base_url)

"""SSE wire parsing: comments must survive (httpx-sse drops them)."""

from scripts.contract_smoke import SseEvent, parse_sse_lines


def test_parse_sse_lines_events_and_comments() -> None:
    lines = [": connected", "", "event: simulation.tick", 'data: {"tick": 1}', "", ": keepalive", ""]
    events, comments = parse_sse_lines(lines)
    assert events == [SseEvent("simulation.tick", {"tick": 1})]
    assert comments == ["connected", "keepalive"]


def test_parse_sse_lines_non_json_data() -> None:
    events, _ = parse_sse_lines(["event: simulator.notice", "data: hello", ""])
    assert events == [SseEvent("simulator.notice", "hello")]


def test_parse_sse_lines_trailing_event_without_blank_is_dropped() -> None:
    events, _ = parse_sse_lines(["event: a", "data: 1", "", "event: b", "data: 2"])
    assert events == [SseEvent("a", 1)]


def test_parse_sse_lines_multiline_data_and_default_event_name() -> None:
    events, _ = parse_sse_lines(["data: {\"a\":", "data: 1}", ""])
    assert events == [SseEvent("message", {"a": 1})]

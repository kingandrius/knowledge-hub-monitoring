"""Unit tests for the docker-agent (no Docker or API needed)."""
from agent import collect, cpu_percent, memory_percent, safe_hostname, send


def test_safe_hostname():
    assert safe_hostname("/knowledge-hub_api_1") == "knowledge-hub-api-1"
    assert safe_hostname("KH-Frontend") == "kh-frontend"
    assert safe_hostname("___") == "container"
    assert len(safe_hostname("a" * 100)) == 63


def test_cpu_percent_is_share_of_whole_host():
    stats = {
        "cpu_stats": {"cpu_usage": {"total_usage": 300}, "system_cpu_usage": 2000},
        "precpu_stats": {"cpu_usage": {"total_usage": 100}, "system_cpu_usage": 1000},
    }
    assert cpu_percent(stats) == 20.0


def test_cpu_percent_without_previous_sample():
    assert cpu_percent({}) == 0.0


def test_memory_percent_leaves_out_cache():
    stats = {"memory_stats": {"usage": 600, "limit": 1000,
                              "stats": {"inactive_file": 100}}}
    assert memory_percent(stats) == 50.0


def test_memory_percent_without_limit():
    assert memory_percent({"memory_stats": {}}) == 0.0


class FakeContainer:
    def __init__(self, name, status):
        self.name, self.status = name, status

    def stats(self, stream):
        return {"memory_stats": {"usage": 500, "limit": 1000}}


class FakeClient:
    class containers:
        @staticmethod
        def list(all):
            return [FakeContainer("kh_api", "running"), FakeContainer("old", "exited")]


def test_collect_running_and_stopped():
    data = collect(FakeClient())
    assert [m["metric"] for m in data["kh-api"]] == [
        "container_up", "container_cpu", "container_memory"]
    assert data["kh-api"][0]["value"] == 1.0
    assert data["old"] == [{"metric": "container_up", "value": 0.0,
                            "timestamp": data["old"][0]["timestamp"]}]


class FakeResponse:
    def __init__(self, status):
        self.status_code, self.text = status, ""


class FakeSession:
    def __init__(self, status):
        self.status, self.posts = status, []

    def post(self, url, json, timeout):
        self.posts.append((url, json))
        return FakeResponse(self.status)


def test_send_one_request_per_container():
    session = FakeSession(201)
    failed = send(session, "http://api/api/v1", "10.0.0.5",
                  {"a": [{"metric": "container_up"}], "b": [{"metric": "container_up"}]})
    assert failed == 0
    assert [p[1]["hostname"] for p in session.posts] == ["a", "b"]
    assert session.posts[0][0] == "http://api/api/v1/measurements"
    assert session.posts[0][1]["ip_address"] == "10.0.0.5"


def test_send_counts_rejected():
    assert send(FakeSession(400), "http://api", "1.2.3.4", {"a": []}) == 1

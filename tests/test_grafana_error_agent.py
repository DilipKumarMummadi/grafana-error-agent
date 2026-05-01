"""
Unit tests for Grafana Error Agent helpers.

These tests focus on response normalization and MCP argument construction so
the agent can tolerate different Grafana MCP tool contracts.
"""

from types import SimpleNamespace

import pytest

from agent.grafana_error_agent import (
    GrafanaAgentConfig,
    GrafanaError,
    ErrorPatternAnalysis,
    analyze_error_patterns,
    build_tool_arguments,
    collect_dashboard_loki_scan,
    mcp_request_headers,
    normalize_grafana_errors,
    select_grafana_tool,
)


def test_normalize_loki_stream_response():
    config = GrafanaAgentConfig(
        github_owner="test-org",
        github_repo="test-service",
        max_errors=10,
    )
    data = {
        "data": {
            "result": [
                {
                    "stream": {
                        "service": "checkout-api",
                        "level": "error",
                        "pod": "checkout-api-123",
                    },
                    "values": [
                        ["1710000000000000000", "NullPointerException in CheckoutService"],
                    ],
                }
            ]
        }
    }

    errors = normalize_grafana_errors(data, config)

    assert len(errors) == 1
    assert errors[0].severity == "ERROR"
    assert errors[0].service == "checkout-api"
    assert "NullPointerException" in errors[0].message
    assert errors[0].labels["pod"] == "checkout-api-123"


def test_normalize_alert_style_response_with_limit():
    config = GrafanaAgentConfig(
        github_owner="test-org",
        github_repo="test-service",
        max_errors=1,
        severity_filter="warning",
    )
    data = {
        "alerts": [
            {
                "fingerprint": "one",
                "severity": "warning",
                "title": "High 5xx rate",
                "message": "HTTP 500 rate above threshold",
                "service": "orders",
                "count": "12",
            },
            {
                "fingerprint": "two",
                "severity": "info",
                "title": "Informational alert",
            },
        ]
    }

    errors = normalize_grafana_errors(data, config)

    assert len(errors) == 1
    assert errors[0].key == "one"
    assert errors[0].count == 12
    assert errors[0].title == "High 5xx rate"


def test_select_grafana_tool_prefers_error_log_tool():
    tools = [
        SimpleNamespace(name="get_dashboard", description="Read dashboard", inputSchema={}),
        SimpleNamespace(name="query_loki_logs", description="Search logs and errors", inputSchema={}),
    ]

    selected = select_grafana_tool(tools, "")

    assert selected.name == "query_loki_logs"


def test_build_tool_arguments_from_schema(monkeypatch):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    config = GrafanaAgentConfig(
        github_owner="test-org",
        github_repo="inventory-api",
        service_name="inventory",
        query='{service="inventory"} |= "error"',
        lookback_hours=2,
        max_errors=25,
    )
    tool = SimpleNamespace(
        name="query_loki_logs",
        inputSchema={
            "type": "object",
            "properties": {
                "datasourceUid": {"type": "string"},
                "query": {"type": "string"},
                "logql": {"type": "string"},
                "service": {"type": "string"},
                "repository": {"type": "string"},
                "limit": {"type": "integer"},
                "start": {"type": "string"},
                "startRfc3339": {"type": "string"},
                "end": {"type": "string"},
                "endRfc3339": {"type": "string"},
            },
        },
    )
    config.grafana_datasource_uid = "loki-main"

    args = build_tool_arguments(tool, config)

    assert args["datasourceUid"] == "loki-main"
    assert args["query"] == '{service="inventory"} |= "error"'
    assert args["logql"] == '{service="inventory"} |= "error"'
    assert args["service"] == "inventory"
    assert args["repository"] == "test-org/inventory-api"
    assert args["limit"] == 25
    assert "start" in args
    assert "startRfc3339" in args
    assert "end" in args
    assert "endRfc3339" in args


def test_build_tool_arguments_wraps_request_schema(monkeypatch):
    monkeypatch.setenv("GRAFANA_MCP_ARGUMENTS", '{"datasource_uid":"loki-main"}')
    config = GrafanaAgentConfig(
        github_owner="test-org",
        github_repo="billing-api",
        service_name="billing",
    )
    tool = SimpleNamespace(
        name="search_errors",
        inputSchema={
            "type": "object",
            "properties": {
                "req": {"type": "object"},
            },
        },
    )

    args = build_tool_arguments(tool, config)

    assert "req" in args
    assert args["req"]["service"] == "billing"
    assert args["req"]["datasource_uid"] == "loki-main"


def test_mcp_request_headers_uses_static_token():
    config = GrafanaAgentConfig(grafana_mcp_auth_token="static-token")

    assert mcp_request_headers(config) == {"Authorization": "Bearer static-token"}


def test_mcp_request_headers_fetches_dynamic_token(monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return b'{"access_token":"fresh-token","expires_in":1800}'

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = request.data.decode("utf-8")
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("agent.grafana_error_agent.urllib.request.urlopen", fake_urlopen)
    config = GrafanaAgentConfig(
        grafana_mcp_auth_token_url="https://login.example.test/oauth2/token",
        grafana_mcp_auth_client_id="client-id",
        grafana_mcp_auth_client_secret="client-secret",
        grafana_mcp_auth_scope="api://grafana/.default",
        grafana_mcp_auth_audience="grafana-mcp",
        grafana_mcp_auth_extra_params='{"resource":"grafana"}',
        mcp_http_timeout=12,
    )

    assert mcp_request_headers(config) == {"Authorization": "Bearer fresh-token"}
    assert captured["url"] == "https://login.example.test/oauth2/token"
    assert captured["timeout"] == 12
    assert "grant_type=client_credentials" in captured["body"]
    assert "client_id=client-id" in captured["body"]
    assert "client_secret=client-secret" in captured["body"]
    assert "scope=api%3A%2F%2Fgrafana%2F.default" in captured["body"]
    assert "audience=grafana-mcp" in captured["body"]
    assert "resource=grafana" in captured["body"]


def test_collect_dashboard_loki_scan_builds_namespace_query():
    config = GrafanaAgentConfig(
        error_pattern="(?i)(error|fatal)",
    )
    panel_queries = [
        {
            "query": (
                'sum(container_memory_working_set_bytes{k8s_cluster=~"dev-cluster", '
                'namespace=~"stardom-core-dev"}) by (pod)'
            ),
            "datasource": {"uid": "$datasource", "type": "prometheus"},
        },
        {
            "query": '{app="api", k8s_cluster="dev-cluster", namespace="stardom-core-dev"}',
            "datasource": {"uid": "loki", "type": "loki"},
        },
    ]

    scan = collect_dashboard_loki_scan(panel_queries, config)

    assert scan["datasourceUid"] == "loki"
    assert scan["patternSelector"] == '{k8s_cluster="dev-cluster", namespace="stardom-core-dev"}'
    assert scan["logql"] == (
        '{k8s_cluster="dev-cluster", namespace="stardom-core-dev"} |~ "(?i)(error|fatal)"'
    )


# ---------------------------------------------------------------------------
# analyze_error_patterns – Kafka SSL/TLS mismatch detection
# ---------------------------------------------------------------------------

def _make_kafka_ssl_error(index: int = 1) -> GrafanaError:
    return GrafanaError(
        key=f"kafka-ssl-{index}",
        severity="ERROR",
        title="SSL handshake failed",
        message=(
            f"sasl_ssl://broker-{index}.example.net:443/{index}: "
            "SSL handshake failed: Disconnected: connecting to a PLAINTEXT broker "
            "listener? (after 1ms in state SSL_HANDSHAKE)"
        ),
        service="stardom-globaladmin-bgservices",
        source="grafana",
    )


def test_analyze_error_patterns_detects_kafka_ssl_mismatch():
    errors = [_make_kafka_ssl_error(i) for i in range(1, 11)]

    analysis = analyze_error_patterns(errors)

    assert analysis is not None
    assert analysis.pattern_type == "kafka_ssl_plaintext_mismatch"
    assert "sasl_ssl" in analysis.root_cause or "SASL_SSL" in analysis.root_cause
    assert "security.protocol" in analysis.config_keys
    assert "bootstrap.servers" in analysis.config_keys
    assert "10/10" in analysis.summary


def test_analyze_error_patterns_returns_none_for_no_errors():
    assert analyze_error_patterns([]) is None


def test_analyze_error_patterns_single_error_not_classified():
    """A single matching error should not be classified as a recurring pattern."""
    errors = [_make_kafka_ssl_error(1)]

    analysis = analyze_error_patterns(errors)

    assert analysis is None


def test_analyze_error_patterns_returns_none_for_unrecognized_errors():
    errors = [
        GrafanaError(
            key="misc-1",
            severity="ERROR",
            title="Some other error",
            message="IndexOutOfBoundsException at line 42",
            service="svc",
            source="grafana",
        )
    ]

    analysis = analyze_error_patterns(errors)

    assert analysis is None


def test_analyze_error_patterns_majority_threshold():
    """Pattern must match at least half the errors to be returned."""
    errors = [_make_kafka_ssl_error(i) for i in range(1, 6)]
    errors += [
        GrafanaError(
            key=f"misc-{i}",
            severity="ERROR",
            title="Unrelated",
            message="NullPointerException",
            service="svc",
            source="grafana",
        )
        for i in range(6, 11)
    ]

    # 5 SSL out of 10 – exactly half, should still classify
    analysis = analyze_error_patterns(errors)

    assert analysis is not None
    assert analysis.pattern_type == "kafka_ssl_plaintext_mismatch"


def test_analyze_error_patterns_kafka_auth_failure():
    errors = [
        GrafanaError(
            key=f"auth-{i}",
            severity="ERROR",
            title="SASL authentication failed",
            message="SASL authentication failed: mechanism not supported by broker",
            service="svc",
            source="grafana",
        )
        for i in range(3)
    ]

    analysis = analyze_error_patterns(errors)

    assert analysis is not None
    assert analysis.pattern_type == "kafka_auth_failure"
    assert "sasl.mechanism" in analysis.config_keys


def test_analyze_error_patterns_kafka_connectivity():
    errors = [
        GrafanaError(
            key=f"conn-{i}",
            severity="ERROR",
            title="Broker connection refused",
            message="Connection refused: broker transport failure after timeout",
            service="svc",
            source="grafana",
        )
        for i in range(3)
    ]

    analysis = analyze_error_patterns(errors)

    assert analysis is not None
    assert analysis.pattern_type == "kafka_connectivity"
    assert "bootstrap.servers" in analysis.config_keys


# ---------------------------------------------------------------------------
# create_github_issue_for_copilot – root-cause analysis section in issue body
# ---------------------------------------------------------------------------

def test_github_issue_body_includes_kafka_ssl_root_cause_analysis():
    from agent.grafana_error_agent import GrafanaErrorAgent

    config = GrafanaAgentConfig(
        github_owner="test-org",
        github_repo="test-service",
    )
    agent = GrafanaErrorAgent(config)

    errors = [_make_kafka_ssl_error(i) for i in range(1, 11)]
    issue = agent.create_github_issue_for_copilot(errors)

    body = issue["body"]
    assert "Root-Cause Analysis" in body
    assert "kafka_ssl_plaintext_mismatch" in body
    assert "security.protocol" in body
    assert "PLAINTEXT" in body


def test_github_issue_body_no_analysis_section_for_unrecognized_errors():
    from agent.grafana_error_agent import GrafanaErrorAgent

    config = GrafanaAgentConfig(
        github_owner="test-org",
        github_repo="test-service",
    )
    agent = GrafanaErrorAgent(config)

    errors = [
        GrafanaError(
            key="x-1",
            severity="ERROR",
            title="Random error",
            message="Something went wrong",
            service="svc",
            source="grafana",
        )
    ]
    issue = agent.create_github_issue_for_copilot(errors)

    assert "Root-Cause Analysis" not in issue["body"]

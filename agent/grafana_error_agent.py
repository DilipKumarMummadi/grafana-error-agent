"""
Grafana Error Agent

Fetches recent Grafana errors through a configured Grafana MCP server and
creates Copilot-ready output for automated fix issues.

The Grafana MCP server contract can vary by deployment, so this agent supports:
- explicit tool selection with GRAFANA_MCP_TOOL
- explicit JSON arguments with GRAFANA_MCP_ARGUMENTS
- tool discovery and best-effort argument construction from MCP input schemas

Usage:
    python grafana_error_agent.py --output grafana_results.json
"""

import argparse
import asyncio
import json
import logging
import math
import os
import re
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from mcp import ClientSession
from mcp.types import CallToolResult, TextContent

try:
    from mcp.client.streamable_http import streamablehttp_client
except ImportError:  # pragma: no cover - depends on installed mcp version
    streamablehttp_client = None

try:
    from mcp.client.sse import sse_client
except ImportError:  # pragma: no cover - depends on installed mcp version
    sse_client = None


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


SEVERITY_RANK = {
    "CRITICAL": 0,
    "ERROR": 1,
    "HIGH": 1,
    "WARNING": 2,
    "WARN": 2,
    "MEDIUM": 3,
    "INFO": 4,
    "LOW": 5,
    "UNKNOWN": 99,
}


DEFAULT_GRAFANA_MCP_URL = "https://grafana-mcp.westeurope.azure.mop.maersk.io/mcp"


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("%s=%r is not a valid integer; using default %s", name, raw, default)
        return default
    if value <= 0:
        logger.warning("%s=%r must be positive; using default %s", name, raw, default)
        return default
    return value


def _env_float(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("%s=%r is not a valid number; using default %s", name, raw, default)
        return default
    if not math.isfinite(value) or value <= 0:
        logger.warning("%s=%r must be a positive finite number; using default %s", name, raw, default)
        return default
    return value


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass
class GrafanaAgentConfig:
    """Configuration for the Grafana Error Agent."""

    grafana_mcp_server_url: str = field(
        default_factory=lambda: os.getenv("GRAFANA_MCP_SERVER_URL", "").strip()
        or DEFAULT_GRAFANA_MCP_URL
    )
    grafana_mcp_transport: str = field(
        default_factory=lambda: os.getenv("GRAFANA_MCP_TRANSPORT", "auto").strip().lower()
    )
    grafana_mcp_tool: str = field(default_factory=lambda: os.getenv("GRAFANA_MCP_TOOL", "").strip())
    grafana_mcp_arguments: str = field(
        default_factory=lambda: os.getenv("GRAFANA_MCP_ARGUMENTS", "").strip()
    )
    grafana_dashboard_uid: str = field(
        default_factory=lambda: os.getenv("GRAFANA_DASHBOARD_UID", "").strip()
    )
    grafana_datasource_uid: str = field(
        default_factory=lambda: (
            os.getenv("GRAFANA_DATASOURCE_UID", "").strip()
            or os.getenv("GRAFANA_LOKI_DATASOURCE_UID", "").strip()
        )
    )
    grafana_mcp_auth_token: str = field(
        default_factory=lambda: os.getenv("GRAFANA_MCP_AUTH_TOKEN", "").strip()
    )
    grafana_mcp_auth_token_url: str = field(
        default_factory=lambda: os.getenv("GRAFANA_MCP_AUTH_TOKEN_URL", "").strip()
    )
    grafana_mcp_auth_client_id: str = field(
        default_factory=lambda: os.getenv("GRAFANA_MCP_AUTH_CLIENT_ID", "").strip()
    )
    grafana_mcp_auth_client_secret: str = field(
        default_factory=lambda: os.getenv("GRAFANA_MCP_AUTH_CLIENT_SECRET", "").strip()
    )
    grafana_mcp_auth_scope: str = field(
        default_factory=lambda: os.getenv("GRAFANA_MCP_AUTH_SCOPE", "").strip()
    )
    grafana_mcp_auth_audience: str = field(
        default_factory=lambda: os.getenv("GRAFANA_MCP_AUTH_AUDIENCE", "").strip()
    )
    grafana_mcp_auth_grant_type: str = field(
        default_factory=lambda: os.getenv("GRAFANA_MCP_AUTH_GRANT_TYPE", "client_credentials").strip()
    )
    grafana_mcp_auth_token_field: str = field(
        default_factory=lambda: os.getenv("GRAFANA_MCP_AUTH_TOKEN_FIELD", "access_token").strip()
    )
    grafana_mcp_auth_extra_params: str = field(
        default_factory=lambda: os.getenv("GRAFANA_MCP_AUTH_EXTRA_PARAMS", "").strip()
    )
    mcp_http_timeout: float = field(default_factory=lambda: _env_float("MCP_HTTP_TIMEOUT", 60.0))
    mcp_sse_read_timeout: float = field(default_factory=lambda: _env_float("MCP_SSE_READ_TIMEOUT", 600.0))

    github_owner: str = field(default_factory=lambda: os.getenv("GITHUB_OWNER", "").strip())
    github_repo: str = field(default_factory=lambda: os.getenv("GITHUB_REPO", "").strip())
    github_repository: str = field(default_factory=lambda: os.getenv("GITHUB_REPOSITORY", "").strip())

    service_name: str = field(default_factory=lambda: os.getenv("GRAFANA_SERVICE_NAME", "").strip())
    repo_label: str = field(default_factory=lambda: os.getenv("GRAFANA_REPO_LABEL", "repository").strip())
    query: str = field(
        default_factory=lambda: os.getenv("GRAFANA_QUERY", "").strip()
        or '(level="error" OR severity="error" OR "exception" OR "failed")'
    )
    error_pattern: str = field(
        default_factory=lambda: os.getenv(
            "GRAFANA_ERROR_PATTERN",
            "(?i)(error|exception|failed|failure|fatal)",
        ).strip()
    )
    lookback_hours: int = field(default_factory=lambda: _env_int("GRAFANA_LOOKBACK_HOURS", 24))
    max_errors: int = field(default_factory=lambda: _env_int("GRAFANA_MAX_ERRORS", 10))
    include_log_patterns: bool = field(
        default_factory=lambda: _env_bool("GRAFANA_INCLUDE_LOG_PATTERNS", True)
    )
    severity_filter: str = field(default_factory=lambda: os.getenv("GRAFANA_SEVERITY_FILTER", "error").strip())
    use_copilot_workspace: bool = field(
        default_factory=lambda: _env_bool("USE_COPILOT_WORKSPACE", True)
    )

    def repo_slug(self) -> str:
        if self.github_repository:
            return self.github_repository
        if self.github_owner and self.github_repo:
            return f"{self.github_owner}/{self.github_repo}"
        return self.github_repo

    def effective_service_name(self) -> str:
        return self.service_name or self.github_repo or self.repo_slug()


@dataclass
class GrafanaError:
    """Normalized Grafana error finding."""

    key: str
    severity: str
    title: str
    message: str
    service: Optional[str] = None
    source: Optional[str] = None
    timestamp: Optional[str] = None
    count: Optional[int] = None
    labels: dict[str, Any] = field(default_factory=dict)
    url: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict)


def fetch_mcp_auth_token(config: GrafanaAgentConfig) -> str:
    """Fetch a short-lived bearer token for the Grafana MCP server."""
    if not config.grafana_mcp_auth_token_url:
        return ""
    if not config.grafana_mcp_auth_client_id or not config.grafana_mcp_auth_client_secret:
        raise ValueError(
            "GRAFANA_MCP_AUTH_CLIENT_ID and GRAFANA_MCP_AUTH_CLIENT_SECRET are required "
            "when GRAFANA_MCP_AUTH_TOKEN_URL is set"
        )

    params: dict[str, str] = {
        "grant_type": config.grafana_mcp_auth_grant_type or "client_credentials",
        "client_id": config.grafana_mcp_auth_client_id,
        "client_secret": config.grafana_mcp_auth_client_secret,
    }
    if config.grafana_mcp_auth_scope:
        params["scope"] = config.grafana_mcp_auth_scope
    if config.grafana_mcp_auth_audience:
        params["audience"] = config.grafana_mcp_auth_audience

    extra_params = parse_json_env(config.grafana_mcp_auth_extra_params)
    for key, value in extra_params.items():
        if value is not None:
            params[key] = str(value)

    body = urllib.parse.urlencode(params).encode("utf-8")
    request = urllib.request.Request(
        config.grafana_mcp_auth_token_url,
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=config.mcp_http_timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))

    if not isinstance(payload, dict):
        raise ValueError("Grafana MCP token API returned a non-object JSON response")

    token = payload.get(config.grafana_mcp_auth_token_field or "access_token")
    if not token:
        raise ValueError(
            f"Grafana MCP token API response did not include {config.grafana_mcp_auth_token_field!r}"
        )
    return str(token)


def mcp_request_headers(config: GrafanaAgentConfig) -> Optional[dict[str, str]]:
    token = config.grafana_mcp_auth_token or fetch_mcp_auth_token(config)
    if not token:
        return None
    return {"Authorization": f"Bearer {token}"}


def parse_mcp_tool_result(result: CallToolResult) -> dict[str, Any]:
    """Decode JSON returned by an MCP tool, falling back to raw text."""
    texts: list[str] = []
    for block in result.content:
        if isinstance(block, TextContent):
            texts.append(block.text)
    blob = "\n".join(t for t in texts if t).strip()
    if not blob:
        return {"success": False, "error": "empty MCP tool result"}
    if result.isError:
        return {"success": False, "error": blob}
    try:
        return {"success": True, "data": json.loads(blob)}
    except json.JSONDecodeError:
        return {"success": True, "data": blob}


def parse_json_env(raw: str) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"GRAFANA_MCP_ARGUMENTS must be valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("GRAFANA_MCP_ARGUMENTS must be a JSON object")
    return parsed


def normalize_severity(value: Any) -> str:
    raw = str(value or "").strip().upper()
    if not raw:
        return "ERROR"
    return {
        "ERR": "ERROR",
        "FATAL": "CRITICAL",
        "CRIT": "CRITICAL",
        "WARNING": "WARNING",
        "WARN": "WARNING",
    }.get(raw, raw)


def severity_allowed(severity: str, minimum: str) -> bool:
    level = normalize_severity(minimum)
    return SEVERITY_RANK.get(normalize_severity(severity), 99) <= SEVERITY_RANK.get(level, 1)


def pick_first(data: dict[str, Any], names: list[str]) -> Any:
    for name in names:
        if name in data and data[name] not in (None, ""):
            return data[name]
    return None


def collect_candidate_items(data: Any) -> list[Any]:
    """Collect likely alert/log/error items from common Grafana/Loki shapes."""
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return [{"message": str(data)}] if data else []

    direct_keys = [
        "errors",
        "alerts",
        "logs",
        "entries",
        "items",
        "results",
        "findings",
        "incidents",
    ]
    for key in direct_keys:
        value = data.get(key)
        if isinstance(value, list):
            return value

    nested = data.get("data")
    if isinstance(nested, dict):
        result = nested.get("result")
        if isinstance(result, list):
            return result
        for key in direct_keys:
            value = nested.get(key)
            if isinstance(value, list):
                return value
    elif isinstance(nested, list):
        return nested

    if "stream" in data and "values" in data:
        return [data]

    return [data]


def normalize_loki_stream(item: dict[str, Any]) -> list[dict[str, Any]]:
    stream = item.get("stream")
    values = item.get("values")
    if not isinstance(stream, dict) or not isinstance(values, list):
        return [item]

    normalized: list[dict[str, Any]] = []
    for value in values:
        if not isinstance(value, list) or len(value) < 2:
            continue
        normalized.append(
            {
                "timestamp": str(value[0]),
                "message": str(value[1]),
                "labels": stream,
                "service": pick_first(stream, ["service", "app", "application", "container", "job"]),
                "severity": pick_first(stream, ["level", "severity"]) or "ERROR",
            }
        )
    return normalized


def normalize_grafana_errors(data: Any, config: GrafanaAgentConfig) -> list[GrafanaError]:
    """Normalize Grafana MCP responses into Copilot-ready findings."""
    raw_items = collect_candidate_items(data)
    expanded: list[dict[str, Any]] = []
    for item in raw_items:
        if isinstance(item, dict):
            expanded.extend(normalize_loki_stream(item))
        else:
            expanded.append({"message": str(item)})

    errors: list[GrafanaError] = []
    for index, item in enumerate(expanded, 1):
        labels = item.get("labels") if isinstance(item.get("labels"), dict) else {}
        combined = {**labels, **item}
        severity = normalize_severity(
            pick_first(combined, ["severity", "level", "status", "state"]) or "ERROR"
        )
        if not severity_allowed(severity, config.severity_filter):
            continue

        message = str(
            pick_first(
                combined,
                ["message", "msg", "error", "description", "summary", "line", "log", "body"],
            )
            or "Grafana error"
        )
        title = str(pick_first(combined, ["title", "name", "alertname", "rule"]) or message.splitlines()[0])
        title = title[:160]
        service = pick_first(combined, ["service", "app", "application", "container", "job"])
        timestamp = pick_first(combined, ["timestamp", "time", "ts", "startsAt", "starts_at"])
        count = pick_first(combined, ["count", "value", "occurrences"])
        try:
            count_value = int(count) if count is not None else None
        except (TypeError, ValueError):
            count_value = None

        key_seed = pick_first(combined, ["fingerprint", "id", "uid", "key"])
        key = str(key_seed or f"grafana-{index}")

        errors.append(
            GrafanaError(
                key=key,
                severity=severity,
                title=title,
                message=message[:4000],
                service=str(service) if service else config.effective_service_name(),
                source=str(pick_first(combined, ["datasource", "source", "folder"]) or "grafana"),
                timestamp=str(timestamp) if timestamp else None,
                count=count_value,
                labels=labels,
                url=pick_first(combined, ["url", "dashboardUrl", "panelUrl", "generatorURL"]),
                raw=item,
            )
        )

    errors.sort(key=lambda e: SEVERITY_RANK.get(e.severity, 99))
    return errors[: config.max_errors]


_KAFKA_SSL_PATTERN = re.compile(
    r"SSL handshake failed.*connecting to a PLAINTEXT broker",
    re.IGNORECASE,
)

_KAFKA_AUTH_PATTERN = re.compile(
    r"(SASL|authentication|broker.*not.*configured|mechanism.*not.*supported)",
    re.IGNORECASE,
)

_KAFKA_TIMEOUT_PATTERN = re.compile(
    r"(timeout|timed\s+out|connection refused|broker transport failure|leader not available)",
    re.IGNORECASE,
)


@dataclass
class ErrorPatternAnalysis:
    """Result of error pattern analysis for a set of Grafana errors."""

    pattern_type: str
    summary: str
    root_cause: str
    suggested_fix: str
    config_keys: list[str] = field(default_factory=list)


def analyze_error_patterns(errors: list[GrafanaError]) -> Optional[ErrorPatternAnalysis]:
    """Detect recurring error patterns and return a root-cause analysis.

    Returns an :class:`ErrorPatternAnalysis` when all (or a majority of) errors
    share a recognizable pattern, or ``None`` when the errors are too diverse to
    classify automatically.
    """
    if not errors:
        return None

    messages = [e.message for e in errors]

    ssl_matches = sum(1 for m in messages if _KAFKA_SSL_PATTERN.search(m))
    if ssl_matches >= max(2, len(messages) // 2):
        return ErrorPatternAnalysis(
            pattern_type="kafka_ssl_plaintext_mismatch",
            summary=(
                f"{ssl_matches}/{len(messages)} errors indicate a Kafka client/broker "
                "TLS protocol mismatch: the client is configured for `sasl_ssl` but the "
                "broker port accepts PLAINTEXT connections only."
            ),
            root_cause=(
                "The Kafka producer/consumer is connecting with `security.protocol=SASL_SSL` "
                "(or an `sasl_ssl://` bootstrap URL) while the broker listener on the target "
                "port is configured for PLAINTEXT.  The SSL handshake is rejected immediately "
                "because the broker sends a PLAINTEXT frame instead of a TLS ServerHello."
            ),
            suggested_fix=(
                "Choose one of the following fixes and apply it consistently across all "
                "Kafka client configurations in this service:\n\n"
                "**Option A – Switch the client to PLAINTEXT** (if the broker port is "
                "intentionally PLAINTEXT):\n"
                "```\n"
                "security.protocol=PLAINTEXT\n"
                "# Remove or do not set sasl.mechanism / sasl.username / sasl.password\n"
                "```\n\n"
                "**Option B – Use the correct SSL/SASL port** (if the broker also exposes "
                "a TLS listener on a different port, e.g. 9093):\n"
                "```\n"
                "bootstrap.servers=<broker-host>:9093\n"
                "security.protocol=SASL_SSL\n"
                "```\n\n"
                "**Option C – Update the bootstrap URL scheme** in code/config to match "
                "the actual broker protocol:\n"
                "```\n"
                "# Before (wrong)\n"
                "bootstrap.servers=sasl_ssl://<broker>:443\n"
                "# After (plaintext)\n"
                "bootstrap.servers=<broker>:443\n"
                "security.protocol=PLAINTEXT\n"
                "```\n\n"
                "After the change, verify connectivity with `kafka-console-producer` or "
                "the broker's admin API before deploying."
            ),
            config_keys=["security.protocol", "bootstrap.servers", "sasl.mechanism"],
        )

    auth_matches = sum(1 for m in messages if _KAFKA_AUTH_PATTERN.search(m))
    if auth_matches >= max(2, len(messages) // 2):
        return ErrorPatternAnalysis(
            pattern_type="kafka_auth_failure",
            summary=(
                f"{auth_matches}/{len(messages)} errors indicate a Kafka SASL "
                "authentication failure."
            ),
            root_cause=(
                "The Kafka client credentials (username, password, or SASL mechanism) do "
                "not match what the broker expects.  This is often caused by rotating "
                "secrets without updating the application configuration, or by selecting "
                "the wrong SASL mechanism (e.g. PLAIN vs SCRAM-SHA-256)."
            ),
            suggested_fix=(
                "1. Verify `sasl.username` and `sasl.password` match the broker's ACL.\n"
                "2. Confirm `sasl.mechanism` matches the broker listener setting "
                "(PLAIN, SCRAM-SHA-256, or SCRAM-SHA-512).\n"
                "3. Rotate secrets in the deployment environment and restart the service."
            ),
            config_keys=["sasl.mechanism", "sasl.username", "sasl.password"],
        )

    timeout_matches = sum(1 for m in messages if _KAFKA_TIMEOUT_PATTERN.search(m))
    if timeout_matches >= max(2, len(messages) // 2):
        return ErrorPatternAnalysis(
            pattern_type="kafka_connectivity",
            summary=(
                f"{timeout_matches}/{len(messages)} errors indicate Kafka broker "
                "connectivity problems (timeout or connection refused)."
            ),
            root_cause=(
                "The Kafka broker is unreachable from the service.  Common causes: "
                "incorrect `bootstrap.servers`, firewall rules blocking the broker port, "
                "or the broker being down/restarting."
            ),
            suggested_fix=(
                "1. Confirm `bootstrap.servers` points to the correct hostnames and ports.\n"
                "2. Check network policies / firewall rules between the service and broker.\n"
                "3. Verify the Kafka broker pods/VMs are healthy and reachable.\n"
                "4. Review broker logs for split-brain or leader-election events."
            ),
            config_keys=["bootstrap.servers"],
        )

    return None


def _schema_properties(schema: Any) -> dict[str, Any]:
    if not isinstance(schema, dict):
        return {}
    props = schema.get("properties")
    return props if isinstance(props, dict) else {}


def scan_window(config: GrafanaAgentConfig) -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=config.lookback_hours)
    return start.isoformat(), now.isoformat()


def extract_label_match(query: str, label: str) -> Optional[str]:
    match = re.search(rf'\b{re.escape(label)}\s*=~?\s*"([^"]+)"', query)
    return match.group(1) if match else None


def append_log_filter(selector: str, pattern: str) -> str:
    selector = selector.strip()
    if "|=" in selector or "|~" in selector:
        return selector
    return f'{selector} |~ "{pattern}"' if pattern else selector


def collect_dashboard_loki_scan(panel_queries: Any, config: GrafanaAgentConfig) -> dict[str, str]:
    """Build a Loki scan from dashboard panel queries."""
    if isinstance(panel_queries, dict):
        panel_queries = panel_queries.get("data", panel_queries.get("queries", []))
    if not isinstance(panel_queries, list):
        return {}

    datasource_uid = config.grafana_datasource_uid
    loki_selectors: list[str] = []
    cluster = ""
    namespace = ""

    for item in panel_queries:
        if not isinstance(item, dict):
            continue
        query = str(item.get("query") or item.get("processedQuery") or "").strip()
        datasource = item.get("datasource") if isinstance(item.get("datasource"), dict) else {}
        datasource_type = str(datasource.get("type") or "").lower()
        uid = str(datasource.get("uid") or "").strip()

        cluster = cluster or extract_label_match(query, "k8s_cluster")
        namespace = namespace or extract_label_match(query, "namespace")

        if datasource_type == "loki" and query:
            datasource_uid = datasource_uid or uid
            loki_selectors.append(query)

    if not datasource_uid:
        return {}

    if cluster and namespace:
        selector = f'{{k8s_cluster="{cluster}", namespace="{namespace}"}}'
    elif loki_selectors:
        selector = loki_selectors[0]
    else:
        return {}

    return {
        "datasourceUid": datasource_uid,
        "logql": append_log_filter(selector, config.error_pattern),
        "patternSelector": selector,
    }


def select_grafana_tool(tools: list[Any], explicit_name: str) -> Any:
    if explicit_name:
        for tool in tools:
            if getattr(tool, "name", None) == explicit_name:
                return tool
        available = ", ".join(getattr(t, "name", "") for t in tools)
        raise ValueError(f"Grafana MCP tool {explicit_name!r} was not found. Available tools: {available}")

    preferred_terms = [
        "error",
        "errors",
        "log",
        "logs",
        "loki",
        "query",
        "search",
        "alert",
        "alerts",
    ]
    scored: list[tuple[int, Any]] = []
    for tool in tools:
        name = getattr(tool, "name", "").lower()
        desc = str(getattr(tool, "description", "") or "").lower()
        text = f"{name} {desc}"
        score = sum(1 for term in preferred_terms if term in text)
        if score:
            scored.append((score, tool))
    if not scored:
        available = ", ".join(getattr(t, "name", "") for t in tools)
        raise ValueError(
            "Could not discover a Grafana logs/errors tool. Set GRAFANA_MCP_TOOL. "
            f"Available tools: {available}"
        )
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored[0][1]


def build_tool_arguments(tool: Any, config: GrafanaAgentConfig) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=config.lookback_hours)
    repo_slug = config.repo_slug()
    service = config.effective_service_name()
    base_values = {
        "query": config.query,
        "expr": config.query,
        "expression": config.query,
        "logql": config.query,
        "selector": config.query,
        "datasourceUid": config.grafana_datasource_uid,
        "datasource_uid": config.grafana_datasource_uid,
        "service": service,
        "service_name": service,
        "app": service,
        "application": service,
        "repo": repo_slug,
        "repository": repo_slug,
        "repo_label": config.repo_label,
        "limit": config.max_errors,
        "max_results": config.max_errors,
        "page_size": config.max_errors,
        "hours": config.lookback_hours,
        "lookback_hours": config.lookback_hours,
        "from": start.isoformat(),
        "start": start.isoformat(),
        "start_time": start.isoformat(),
        "startRfc3339": start.isoformat(),
        "since": start.isoformat(),
        "to": now.isoformat(),
        "end": now.isoformat(),
        "end_time": now.isoformat(),
        "endRfc3339": now.isoformat(),
    }

    override_args = parse_json_env(config.grafana_mcp_arguments)
    props = _schema_properties(getattr(tool, "inputSchema", None))
    if not props:
        return override_args or {
            "query": config.query,
            "service": service,
            "repository": repo_slug,
            "limit": config.max_errors,
            "start": start.isoformat(),
            "end": now.isoformat(),
        }

    args: dict[str, Any] = {}
    for prop_name in props:
        if prop_name in base_values:
            args[prop_name] = base_values[prop_name]

    args.update(override_args)

    if set(props.keys()) & {"req", "request", "input"}:
        wrapper = next(name for name in ("req", "request", "input") if name in props)
        if wrapper not in args:
            nested = {
                "query": config.query,
                "logql": config.query,
                "datasourceUid": config.grafana_datasource_uid,
                "service": service,
                "repository": repo_slug,
                "limit": config.max_errors,
                "start": start.isoformat(),
                "end": now.isoformat(),
            }
            nested.update(override_args)
            args = {wrapper: nested}

    return args


class GrafanaErrorAgent:
    """Agent that fetches Grafana errors and creates Copilot fix payloads."""

    def __init__(self, config: GrafanaAgentConfig):
        self.config = config
        self.errors: list[GrafanaError] = []
        self._mcp: Optional[ClientSession] = None

    async def _call_mcp_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if self._mcp is None:
            return {"success": False, "error": "MCP session not initialized"}
        logger.info("Calling Grafana MCP tool: %s", name)
        raw = await self._mcp.call_tool(name, arguments)
        return parse_mcp_tool_result(raw)

    async def _run_with_session(self, read: Any, write: Any) -> dict[str, Any]:
        async with ClientSession(read, write) as session:
            await session.initialize()
            self._mcp = session
            try:
                return await self._run_scan()
            finally:
                self._mcp = None

    async def run(self) -> dict[str, Any]:
        url = self.config.grafana_mcp_server_url.strip()
        if not url:
            return {
                "success": False,
                "errors_found": 0,
                "message": "GRAFANA_MCP_SERVER_URL is required",
                "errors": [],
            }

        headers = mcp_request_headers(self.config)
        transport = self.config.grafana_mcp_transport
        if transport == "auto":
            transport = "sse" if url.rstrip("/").endswith("/sse") else "streamable_http"

        if transport == "sse":
            if sse_client is None:
                return {"success": False, "errors_found": 0, "message": "mcp SSE client is unavailable", "errors": []}
            async with sse_client(
                url,
                headers=headers,
                timeout=self.config.mcp_http_timeout,
                sse_read_timeout=self.config.mcp_sse_read_timeout,
            ) as (read, write):
                return await self._run_with_session(read, write)

        if streamablehttp_client is None:
            return {
                "success": False,
                "errors_found": 0,
                "message": "mcp streamable HTTP client is unavailable; install mcp>=1.9.4",
                "errors": [],
            }
        async with streamablehttp_client(
            url,
            headers=headers,
            timeout=self.config.mcp_http_timeout,
            sse_read_timeout=self.config.mcp_sse_read_timeout,
        ) as streams:
            read, write = streams[0], streams[1]
            return await self._run_with_session(read, write)

    async def _run_scan(self) -> dict[str, Any]:
        if self._mcp is None:
            return {"success": False, "errors_found": 0, "message": "MCP session not initialized", "errors": []}

        tools_result = await self._mcp.list_tools()
        tools = list(tools_result.tools)
        if self.config.grafana_dashboard_uid and not self.config.grafana_mcp_tool:
            return await self._run_dashboard_scan(tools)

        tool = select_grafana_tool(tools, self.config.grafana_mcp_tool)
        tool_name = getattr(tool, "name")
        arguments = build_tool_arguments(tool, self.config)
        result = await self._call_mcp_tool(tool_name, arguments)
        if not result.get("success"):
            return {
                "success": False,
                "errors_found": 0,
                "message": result.get("error", "Grafana MCP tool call failed"),
                "tool": tool_name,
                "errors": [],
            }

        errors = normalize_grafana_errors(result.get("data"), self.config)
        self.errors = errors

        if not errors:
            return {
                "success": True,
                "errors_found": 0,
                "message": "No Grafana errors found",
                "tool": tool_name,
                "tool_arguments": arguments,
                "errors": [],
            }

        payload = {
            "success": True,
            "errors_found": len(errors),
            "tool": tool_name,
            "tool_arguments": arguments,
            "errors": [self._error_to_dict(error) for error in errors],
            "github_issue": self.create_github_issue_for_copilot(errors),
        }
        if self.config.use_copilot_workspace:
            payload["copilot_workspace_link"] = self.create_copilot_workspace_link(errors)
        return payload

    async def _run_dashboard_scan(self, tools: list[Any]) -> dict[str, Any]:
        tool_names = {getattr(tool, "name", "") for tool in tools}
        required_tools = {"get_dashboard_panel_queries", "query_loki_logs"}
        missing = sorted(required_tools - tool_names)
        if missing:
            return {
                "success": False,
                "errors_found": 0,
                "message": f"Grafana MCP server is missing required dashboard scan tools: {', '.join(missing)}",
                "errors": [],
            }

        panel_result = await self._call_mcp_tool(
            "get_dashboard_panel_queries",
            {"uid": self.config.grafana_dashboard_uid},
        )
        if not panel_result.get("success"):
            return {
                "success": False,
                "errors_found": 0,
                "message": panel_result.get("error", "Could not read Grafana dashboard panel queries"),
                "tool": "get_dashboard_panel_queries",
                "errors": [],
            }

        scan = collect_dashboard_loki_scan(panel_result.get("data"), self.config)
        if not scan:
            return {
                "success": False,
                "errors_found": 0,
                "message": (
                    "Could not derive a Loki datasource/query from the dashboard. "
                    "Set GRAFANA_DATASOURCE_UID and GRAFANA_QUERY explicitly."
                ),
                "dashboard_uid": self.config.grafana_dashboard_uid,
                "errors": [],
            }

        start, end = scan_window(self.config)
        log_args = {
            "datasourceUid": scan["datasourceUid"],
            "logql": scan["logql"],
            "startRfc3339": start,
            "endRfc3339": end,
            "limit": self.config.max_errors,
        }
        result = await self._call_mcp_tool("query_loki_logs", log_args)
        if not result.get("success"):
            return {
                "success": False,
                "errors_found": 0,
                "message": result.get("error", "Grafana dashboard Loki scan failed"),
                "dashboard_uid": self.config.grafana_dashboard_uid,
                "tool": "query_loki_logs",
                "tool_arguments": log_args,
                "errors": [],
            }

        patterns = await self._fetch_dashboard_patterns(tool_names, scan, start, end)
        errors = normalize_grafana_errors(result.get("data"), self.config)
        self.errors = errors

        payload = {
            "success": True,
            "errors_found": len(errors),
            "dashboard_uid": self.config.grafana_dashboard_uid,
            "tool": "query_loki_logs",
            "tool_arguments": log_args,
            "patterns": patterns,
            "errors": [self._error_to_dict(error) for error in errors],
        }
        if errors:
            payload["github_issue"] = self.create_github_issue_for_copilot(errors, patterns)
            if self.config.use_copilot_workspace:
                payload["copilot_workspace_link"] = self.create_copilot_workspace_link(errors)
        else:
            payload["message"] = "No Grafana errors found"
        return payload

    async def _fetch_dashboard_patterns(
        self,
        tool_names: set[str],
        scan: dict[str, str],
        start: str,
        end: str,
    ) -> list[Any]:
        if not self.config.include_log_patterns or "query_loki_patterns" not in tool_names:
            return []
        pattern_args = {
            "datasourceUid": scan["datasourceUid"],
            "logql": scan["patternSelector"],
            "startRfc3339": start,
            "endRfc3339": end,
        }
        result = await self._call_mcp_tool("query_loki_patterns", pattern_args)
        if not result.get("success"):
            logger.warning("Grafana Loki pattern query failed: %s", result.get("error"))
            return []
        data = result.get("data")
        if isinstance(data, list):
            return data[: self.config.max_errors]
        if isinstance(data, dict):
            for key in ("patterns", "data", "items", "results"):
                value = data.get(key)
                if isinstance(value, list):
                    return value[: self.config.max_errors]
        return [data] if data else []

    def _error_to_dict(self, error: GrafanaError) -> dict[str, Any]:
        return {
            "key": error.key,
            "severity": error.severity,
            "title": error.title,
            "message": error.message,
            "service": error.service,
            "source": error.source,
            "timestamp": error.timestamp,
            "count": error.count,
            "labels": error.labels,
            "url": error.url,
        }

    def create_copilot_workspace_link(self, errors: list[GrafanaError]) -> str:
        repo_slug = self.config.repo_slug()
        task_lines = ["Fix the root cause of these Grafana production errors:"]
        for index, error in enumerate(errors[:5], 1):
            task_lines.append(f"{index}. [{error.severity}] {error.title}")
            if error.service:
                task_lines.append(f"   Service: {error.service}")
        task = urllib.parse.quote("\n".join(task_lines))
        return f"https://copilot-workspace.githubnext.com/{repo_slug}?task={task}"

    def create_github_issue_for_copilot(
        self,
        errors: list[GrafanaError],
        patterns: Optional[list[Any]] = None,
    ) -> dict[str, Any]:
        title = f"[Grafana Agent] Fix {len(errors)} runtime error(s)"
        body = """## Grafana Runtime Errors Detected

The Grafana MCP server reported runtime errors for this repository/service. Fix the likely root cause in one pull request.

"""
        for index, error in enumerate(errors, 1):
            body += f"""### {index}. {error.title}

- **Severity:** {error.severity}
- **Service:** `{error.service or "N/A"}`
- **Source:** `{error.source or "grafana"}`
"""
            if error.timestamp:
                body += f"- **Timestamp:** `{error.timestamp}`\n"
            if error.count is not None:
                body += f"- **Occurrences:** `{error.count}`\n"
            if error.url:
                body += f"- **Grafana URL:** {error.url}\n"
            if error.labels:
                labels = ", ".join(f"{k}={v}" for k, v in sorted(error.labels.items()))
                body += f"- **Labels:** `{labels}`\n"

            body += f"\n**Message:**\n```text\n{error.message}\n```\n\n---\n\n"

        if patterns:
            body += "## Related Loki Patterns\n\n"
            for index, pattern in enumerate(patterns[: self.config.max_errors], 1):
                body += f"{index}. `{str(pattern)[:500]}`\n"
            body += "\n"

        analysis = analyze_error_patterns(errors)
        if analysis:
            body += f"""## Root-Cause Analysis

**Detected pattern:** `{analysis.pattern_type}`

**Summary:** {analysis.summary}

**Root cause:** {analysis.root_cause}

**Suggested fix:**

{analysis.suggested_fix}

"""
            if analysis.config_keys:
                keys_str = ", ".join(f"`{k}`" for k in analysis.config_keys)
                body += f"**Configuration keys to review:** {keys_str}\n\n"

        body += """## Instructions for @copilot

Address all Grafana findings above in one pull request.

1. Search the repository for the service, stack trace, endpoint, class, function, or error message referenced by each finding.
2. Identify the smallest code/configuration change that removes the root cause.
3. Add or update tests where this repository has matching coverage patterns.
4. Avoid unrelated refactors.
5. In the PR description, map each Grafana finding to the file(s) changed and explain how the fix prevents recurrence.

@copilot Implement the fixes and open one PR for human review.
"""

        return {
            "title": title,
            "body": body,
            "labels": ["grafana", "runtime-error", "automated", "copilot"],
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Grafana Error Agent")
    parser.add_argument("--output", type=str, help="Output file for results (JSON)")
    args = parser.parse_args()

    config = GrafanaAgentConfig()
    agent = GrafanaErrorAgent(config)
    result = asyncio.run(agent.run())

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
        logger.info("Results written to %s", args.output)

    if os.getenv("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"errors_found={result.get('errors_found', 0)}\n")
            f.write(f"success={str(result.get('success', False)).lower()}\n")
            if result.get("copilot_workspace_link"):
                f.write(f"copilot_workspace_link={result['copilot_workspace_link']}\n")

    return 0 if result.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Dependency-free MCP adapter over the Helical dashboard's B2C API.

Tool shapes follow the dashboard's `agentcore-mcp` routes — paths, parameter names, and
response fields are taken from them, not invented — but this client targets the B2C port
of those routes (`platform-mcp`, DESIGN.md 2.3). Auth is a Cognito **access** token in
`Authorization: Bearer <token>`.

The one thing to understand before reading:

**Project scope is derived, never transmitted** (DESIGN.md 2.3.1). It is in no tool's
input schema, no path, no query, no header, and no body. The API resolves the caller's
project from the verified subject, which is a pure function of the token, so there is
nothing here for a model to get wrong and nothing for the API to have to distrust.

The cost of that is compatibility with the current `agentcore-mcp` surface, which requires
`conversationId`/`projectId` in the URL: `list_models`, `start_embedding_run` and
`start_finetuning_run` will fail against it until the port lands. Everything that scopes
from the resource or the caller's memberships — datasets, run details, files, S3, UMAPs —
works against either. Set `HELICAL_API_ROOT` to choose the route group.

Also absent, because `agentcore-mcp` has no such route and the port adds them: dataset
upload and registration, presigned URLs, binary artifact download, and anything
billing-related (DESIGN.md 8.2).
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


SERVER_NAME = "helical"
SERVER_VERSION = "0.1.0"
LATEST_PROTOCOL = "2025-06-18"
SUPPORTED_PROTOCOLS = {"2024-11-05", "2025-03-26", LATEST_PROTOCOL}
# Route group. The B2C port is the target; `/api/agentcore-mcp` is the surface that
# exists today and still wants scope in the URL, so some tools will not work against it.
API_ROOT = os.environ.get("HELICAL_API_ROOT", "/api/platform-mcp").rstrip("/")
MAX_RESPONSE_BYTES = 2_000_000
MAX_REQUEST_BYTES = 64_000

UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
RUN_ID = re.compile(r"^[A-Za-z0-9._:+-]{1,200}$")
# The API enforces `/projects/<slug>/data` as the file root; reject traversal locally too
# so an obviously bad path never leaves this process.
PROJECT_PATH = re.compile(r"^/projects/[A-Za-z0-9._-]+/data(/[A-Za-z0-9._\- /]*)?$")

MODALITIES = ("text", "sc", "mrna")
LR_SCHEDULERS = ("constant", "linear", "cosine_with_min_lr")
RUN_STATES = ("succeeded", "failed", "cancelled", "running")


class ToolError(Exception):
    """A safe, caller-facing error. Never carries upstream internals."""


class _NoRedirects(HTTPRedirectHandler):
    """Prevent a redirect from forwarding the bearer token to another host."""

    def redirect_request(self, _req, _fp, _code, _msg, _headers, _newurl):
        return None


def _open(request: Request):
    return build_opener(_NoRedirects).open(request, timeout=30)


def _config() -> tuple[str, str]:
    base_url = os.environ.get("HELICAL_API_BASE_URL", "").rstrip("/")
    token = os.environ.get("HELICAL_API_TOKEN", "")
    if not base_url or not token:
        raise ToolError(
            "The Helical dashboard is not configured. Set HELICAL_API_BASE_URL to the "
            "dashboard origin and HELICAL_API_TOKEN to a Cognito access token, then "
            "restart the client."
        )
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ToolError("HELICAL_API_BASE_URL must be an HTTPS origin without credentials.")
    return base_url, token


def _request(method: str, suffix: str, query: dict[str, str] | None = None,
             body: dict[str, Any] | None = None) -> Any:
    base_url, token = _config()
    url = f"{base_url}{API_ROOT}{suffix}"
    if query:
        url = f"{url}?{urlencode(query)}"

    data = None
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        "User-Agent": f"{SERVER_NAME}/{SERVER_VERSION}",
    }
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        if len(data) > MAX_REQUEST_BYTES:
            raise ToolError("The request payload exceeded the 64 KB safety limit.")
        headers["Content-Type"] = "application/json"

    request = Request(url, data=data, headers=headers, method=method)
    try:
        with _open(request) as response:
            payload = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as error:
        raise _http_error(error) from None
    except (TimeoutError, URLError):
        raise ToolError("The Helical dashboard could not be reached. Try again later.") from None

    if len(payload) > MAX_RESPONSE_BYTES:
        raise ToolError(
            "The response exceeded the 2 MB safety limit. Narrow the request — use a "
            "smaller limit, or read a file in slices with maxBytes."
        )
    if not payload:
        return {}
    try:
        return json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ToolError("The dashboard returned invalid JSON.") from None


def _http_error(error: HTTPError) -> ToolError:
    """Map the API's `{error, detail}` body onto a safe message.

    `error` is a short human string the routes choose deliberately, so it is safe to
    surface. `detail` can carry Zod issues or upstream exception text, so it is dropped.
    """
    summary = ""
    try:
        body = json.loads(error.read(20_000))
        if isinstance(body, dict) and isinstance(body.get("error"), str):
            summary = body["error"]
    except Exception:  # noqa: BLE001 - never let error parsing mask the original failure
        summary = ""

    if error.code == 401:
        return ToolError(
            "Not authenticated. HELICAL_API_TOKEN must be a current Cognito access token."
        )
    if error.code == 403:
        return ToolError(summary or "Not permitted for this account.")
    if error.code == 404:
        return ToolError(summary or "Not found, or not visible to this account.")
    if error.code == 400:
        return ToolError(
            f"The dashboard rejected the request: {summary or 'invalid parameters'}."
        )
    if error.code == 429:
        return ToolError("Rate limit reached. Try again later.")
    if error.code in (500, 502):
        return ToolError("The dashboard or an upstream service failed. Try again later.")
    return ToolError(f"The dashboard returned HTTP {error.code}.")


# --------------------------------------------------------------------------- validation


def _uuid(arguments: dict[str, Any], field: str, *, required: bool = True) -> str | None:
    value = arguments.get(field)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not UUID.fullmatch(value):
        raise ToolError(f"{field} must be a UUID.")
    return value


def _run_id(arguments: dict[str, Any], field: str = "runId") -> str:
    value = arguments.get(field)
    if not isinstance(value, str) or not RUN_ID.fullmatch(value):
        raise ToolError(f"{field} must be a run identifier of at most 200 safe characters.")
    return value


def _string(arguments: dict[str, Any], field: str, *, maximum: int,
            required: bool = True) -> str | None:
    value = arguments.get(field)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= maximum:
        raise ToolError(f"{field} must be a non-empty string of at most {maximum} characters.")
    return value.strip()


def _int(arguments: dict[str, Any], field: str, *, minimum: int, maximum: int,
         required: bool = True) -> int | None:
    value = arguments.get(field)
    if value is None and not required:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ToolError(f"{field} must be an integer from {minimum} to {maximum}.")
    return value


def _number(arguments: dict[str, Any], field: str, *, minimum: float,
            required: bool = True) -> float | None:
    value = arguments.get(field)
    if value is None and not required:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < minimum:
        raise ToolError(f"{field} must be a number of at least {minimum}.")
    return float(value)


def _string_list(arguments: dict[str, Any], field: str, *, maximum_items: int = 20,
                 allowed: tuple[str, ...] | None = None,
                 required: bool = True) -> list[str] | None:
    value = arguments.get(field)
    if value is None and not required:
        return None
    if not isinstance(value, list) or not 1 <= len(value) <= maximum_items:
        raise ToolError(f"{field} must be a list of 1 to {maximum_items} strings.")
    for item in value:
        if not isinstance(item, str) or not 1 <= len(item) <= 200:
            raise ToolError(f"Each entry in {field} must be a string of at most 200 characters.")
        if allowed is not None and item not in allowed:
            raise ToolError(f"Each entry in {field} must be one of: {', '.join(allowed)}.")
    return value


def _enum(arguments: dict[str, Any], field: str, allowed: tuple[str, ...],
          *, required: bool = False) -> str | None:
    value = arguments.get(field)
    if value is None and not required:
        return None
    if value not in allowed:
        raise ToolError(f"{field} must be one of: {', '.join(allowed)}.")
    return value


def _quote_id(arguments: dict[str, Any]) -> str:
    """The quote a run is priced against. Required: it is what fixes the price."""
    value = arguments.get("quote_id")
    if not isinstance(value, str) or not RUN_ID.fullmatch(value):
        raise ToolError(
            "quote_id is required — call the matching estimate_* tool first, show the "
            "user the cost, and pass the quote it returned."
        )
    return value


def _project_path(arguments: dict[str, Any]) -> str:
    value = arguments.get("path")
    if not isinstance(value, str) or ".." in value or not PROJECT_PATH.fullmatch(value):
        raise ToolError(
            "path must be an absolute path under /projects/<project>/data with no '..' segments."
        )
    return value


def _put(target: dict[str, Any], key: str, value: Any) -> None:
    if value is not None:
        target[key] = value


# --------------------------------------------------------------------------- tools


def _list_models(arguments: dict[str, Any]) -> Any:
    query: dict[str, str] = {}
    _put(query, "modelType", _enum(arguments, "modelType", ("sequence", "single-cell")))
    _put(query, "status", _enum(arguments, "status", ("promoted", "unpromoted", "all")))
    return _request("GET", "/models", query)


def _list_datasets(arguments: dict[str, Any]) -> Any:
    query: dict[str, str] = {}
    for field in ("name", "author", "organism", "tissue", "disease"):
        _put(query, field, _string(arguments, field, maximum=200, required=False))
    _put(query, "limit", _int(arguments, "limit", minimum=1, maximum=20, required=False))
    if "limit" in query:
        query["limit"] = str(query["limit"])
    return _request("GET", "/data", query or None)


def _get_dataset(arguments: dict[str, Any]) -> Any:
    return _request("GET", f"/data/{_uuid(arguments, 'id')}")


def _get_dataset_columns(arguments: dict[str, Any]) -> Any:
    return _request("GET", f"/data/{_uuid(arguments, 'id')}/columns")


def _get_dataset_obs_values(arguments: dict[str, Any]) -> Any:
    query: dict[str, str] = {}
    _put(query, "column", _string(arguments, "column", maximum=200, required=False))
    return _request("GET", f"/data/{_uuid(arguments, 'id')}/obs-values", query or None)


def _embedding_body(arguments: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {
        "datasetId": _uuid(arguments, "datasetId"),
        "model": _string(arguments, "model", maximum=200),
        # Required by the route schema despite its description suggesting otherwise.
        "batch_size": _int(arguments, "batch_size", minimum=1, maximum=4096),
    }
    _put(body, "model_version", _int(arguments, "model_version", minimum=1,
                                     maximum=10_000, required=False))
    _put(body, "modalities", _string_list(arguments, "modalities", maximum_items=3,
                                          allowed=MODALITIES, required=False))
    _put(body, "pretrained_embedding_species",
         _string_list(arguments, "pretrained_embedding_species", required=False))
    _put(body, "device", _enum(arguments, "device", ("cuda", "cpu")))
    return body


def _estimate_embedding_run(arguments: dict[str, Any]) -> Any:
    return _request("POST", "/airflow/estimate/embedding", body=_embedding_body(arguments))


def _estimate_finetuning_run(arguments: dict[str, Any]) -> Any:
    return _request("POST", "/airflow/estimate/finetuning", body=_finetuning_body(arguments))


def _start_embedding_run(arguments: dict[str, Any]) -> Any:
    body = _embedding_body(arguments)
    body["quote_id"] = _quote_id(arguments)
    return _request("POST", "/airflow/trigger/embedding", body=body)


def _finetuning_body(arguments: dict[str, Any]) -> dict[str, Any]:
    labels = _string_list(arguments, "labels", maximum_items=10)
    body: dict[str, Any] = {
        "datasetId": _uuid(arguments, "datasetId"),
        "model": _string(arguments, "model", maximum=200),
        "labels": labels,
        "task_type": _string_list(arguments, "task_type", maximum_items=10,
                                  allowed=("prediction", "contrastive")),
        "model_type": _string_list(arguments, "model_type", maximum_items=10,
                                   allowed=("classification", "regression")),
        "learning_rate": _number(arguments, "learning_rate", minimum=1e-12),
        "batch_size": _int(arguments, "batch_size", minimum=1, maximum=4096),
        "epochs": _int(arguments, "epochs", minimum=1, maximum=1000),
        "logging_steps": _int(arguments, "logging_steps", minimum=1, maximum=1_000_000),
        "lr_scheduler": _enum(arguments, "lr_scheduler", LR_SCHEDULERS, required=True),
        "min_lr": _number(arguments, "min_lr", minimum=1e-12),
        "val_steps": _int(arguments, "val_steps", minimum=1, maximum=1_000_000),
        "seed": _int(arguments, "seed", minimum=1, maximum=2**31 - 1),
        "num_trainable_layers": _int(arguments, "num_trainable_layers", minimum=1, maximum=1000),
        "registered_model_name": _string(arguments, "registered_model_name", maximum=200),
    }
    for parallel in ("task_type", "model_type"):
        if len(body[parallel]) != len(labels):
            raise ToolError(f"{parallel} must have exactly one entry per label.")
    _put(body, "model_version", _int(arguments, "model_version", minimum=1,
                                     maximum=10_000, required=False))
    _put(body, "valDatasetId", _uuid(arguments, "valDatasetId", required=False))
    _put(body, "val_split", _number(arguments, "val_split", minimum=0.0, required=False))
    _put(body, "weight_decay", _number(arguments, "weight_decay", minimum=0.0, required=False))
    _put(body, "early_stopping_patience",
         _int(arguments, "early_stopping_patience", minimum=1, maximum=1000, required=False))
    _put(body, "device", _enum(arguments, "device", ("cuda", "cpu")))
    return body


def _start_finetuning_run(arguments: dict[str, Any]) -> Any:
    body = _finetuning_body(arguments)
    body["quote_id"] = _quote_id(arguments)
    return _request("POST", "/airflow/trigger/finetuning", body=body)


def _list_runs(arguments: dict[str, Any]) -> Any:
    query: dict[str, str] = {}
    limit = _int(arguments, "limit", minimum=1, maximum=200, required=False)
    if limit is not None:
        query["limit"] = str(limit)
    state = _string_list(arguments, "state", maximum_items=4, allowed=RUN_STATES, required=False)
    if state:
        query["state"] = ",".join(state)
    dag_ids = _string_list(arguments, "dagIds", maximum_items=10, required=False)
    if dag_ids:
        query["dagIds"] = ",".join(dag_ids)
    return _request("GET", "/airflow/runs", query)


def _get_run_details(arguments: dict[str, Any]) -> Any:
    return _request("GET", f"/airflow/run-details/{quote(_run_id(arguments), safe='')}")


def _list_files(arguments: dict[str, Any]) -> Any:
    query = {"path": _project_path(arguments)}
    limit = _int(arguments, "limit", minimum=1, maximum=500, required=False)
    if limit is not None:
        query["limit"] = str(limit)
    return _request("GET", "/files/list", query)


def _read_file(arguments: dict[str, Any]) -> Any:
    query = {"path": _project_path(arguments)}
    max_bytes = _int(arguments, "maxBytes", minimum=1, maximum=1_048_576, required=False)
    if max_bytes is not None:
        query["maxBytes"] = str(max_bytes)
    return _request("GET", "/files/read", query)


def _list_s3_files(arguments: dict[str, Any]) -> Any:
    query: dict[str, str] = {}
    _put(query, "path", _string(arguments, "path", maximum=500, required=False))
    return _request("GET", "/s3", query or None)


def _list_umaps(arguments: dict[str, Any]) -> Any:
    query: dict[str, str] = {}
    dataset = _uuid(arguments, "datasetId", required=False)
    if dataset:
        query["datasetId"] = dataset
    limit = _int(arguments, "limit", minimum=1, maximum=200, required=False)
    if limit is not None:
        query["limit"] = str(limit)
    return _request("GET", "/airflow/umaps", query or None)


def _get_umap(arguments: dict[str, Any]) -> Any:
    return _request("GET", f"/airflow/umaps/{quote(_run_id(arguments), safe='')}")


# --------------------------------------------------------------------------- tool table

READ_ONLY = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}
LAUNCH = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,  # each call starts a run and consumes compute
    "openWorldHint": True,
}

_UUID_FIELD = {"type": "string", "pattern": UUID.pattern}

def _tool(name: str, description: str, properties: dict[str, Any],
          required: list[str], annotations: dict[str, bool]) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
        "annotations": annotations,
    }


TOOLS = [
    _tool(
        "list_models",
        "List models available in the caller's project. Promoted models by default; pass "
        "status='all' to include unpromoted candidates. Returns model_id, name, version, "
        "base_model and is_promoted — pass `name` as the `model` for a run.",
        {
            "modelType": {"type": "string", "enum": ["sequence", "single-cell"]},
            "status": {"type": "string", "enum": ["promoted", "unpromoted", "all"],
                       "default": "promoted"},
        },
        [],
        READ_ONLY,
    ),
    _tool(
        "list_datasets",
        "Search the single-cell catalogue. name/author match as case-insensitive "
        "substrings; organism/tissue/disease match an exact array element. Returns rows "
        "with id, cellCount and geneCount, plus the total across all pages.",
        {
            "name": {"type": "string", "maxLength": 200},
            "author": {"type": "string", "maxLength": 200},
            "organism": {"type": "string", "maxLength": 200},
            "tissue": {"type": "string", "maxLength": 200},
            "disease": {"type": "string", "maxLength": 200},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10},
        },
        [],
        READ_ONLY,
    ),
    _tool(
        "get_dataset",
        "Full metadata for one dataset, including cellCount, geneCount and the label "
        "columns already identified (celltypeColumn, diseaseColumn, donorColumn).",
        {"id": _UUID_FIELD},
        ["id"],
        READ_ONLY,
    ),
    _tool(
        "get_dataset_columns",
        "List the dataset's .obs column names. Use this to choose the label column for "
        "fine-tuning rather than guessing at a plausible name.",
        {"id": _UUID_FIELD},
        ["id"],
        READ_ONLY,
    ),
    _tool(
        "get_dataset_obs_values",
        "The distinct values of the dataset's categorical .obs columns, or of one column "
        "if `column` is given. Use it to confirm a label column has the classes expected.",
        {"id": _UUID_FIELD, "column": {"type": "string", "maxLength": 200}},
        ["id"],
        READ_ONLY,
    ),
    _tool(
        "estimate_embedding_run",
        "Price an embedding run before starting it. Free, starts nothing, and safe to "
        "call as often as you like. Takes the same arguments as start_embedding_run and "
        "returns the token count, the price, the assumptions behind it, and a `quote_id` "
        "valid for a limited window. SHOW THE USER the tokens and price and get their "
        "explicit approval, then pass the quote_id to start_embedding_run — the quote is "
        "what fixes the price. Changing any argument invalidates it; estimate again.",
        {
            "datasetId": _UUID_FIELD,
            "model": {"type": "string", "minLength": 1, "maxLength": 200},
            "batch_size": {"type": "integer", "minimum": 1, "maximum": 4096},
            "model_version": {"type": "integer", "minimum": 1, "maximum": 10000},
            "modalities": {"type": "array", "items": {"type": "string", "enum": list(MODALITIES)},
                           "minItems": 1, "maxItems": 3},
            "pretrained_embedding_species": {"type": "array", "items": {"type": "string"}},
            "device": {"type": "string", "enum": ["cuda", "cpu"], "default": "cuda"},
        },
        ["datasetId", "model", "batch_size"],
        READ_ONLY,
    ),
    _tool(
        "estimate_finetuning_run",
        "Price a fine-tuning run before starting it. Free and starts nothing. Training "
        "tokens scale with `epochs`, so this is usually far larger than an embedding "
        "quote on the same dataset — quote the epoch count you intend to run, and "
        "re-estimate if it changes. Returns a `quote_id` for start_finetuning_run.",
        {
            "datasetId": _UUID_FIELD,
            "model": {"type": "string", "minLength": 1, "maxLength": 200},
            "labels": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 10},
            "task_type": {"type": "array", "minItems": 1, "maxItems": 10,
                          "items": {"type": "string", "enum": ["prediction", "contrastive"]}},
            "model_type": {"type": "array", "minItems": 1, "maxItems": 10,
                           "items": {"type": "string", "enum": ["classification", "regression"]}},
            "learning_rate": {"type": "number", "exclusiveMinimum": 0},
            "batch_size": {"type": "integer", "minimum": 1, "maximum": 4096},
            "epochs": {"type": "integer", "minimum": 1, "maximum": 1000},
            "logging_steps": {"type": "integer", "minimum": 1},
            "lr_scheduler": {"type": "string", "enum": list(LR_SCHEDULERS)},
            "min_lr": {"type": "number", "exclusiveMinimum": 0},
            "val_steps": {"type": "integer", "minimum": 1},
            "seed": {"type": "integer", "minimum": 1},
            "num_trainable_layers": {"type": "integer", "minimum": 1, "maximum": 1000},
            "registered_model_name": {"type": "string", "minLength": 1, "maxLength": 200},
            "model_version": {"type": "integer", "minimum": 1, "maximum": 10000},
            "valDatasetId": _UUID_FIELD,
            "val_split": {"type": "number", "minimum": 0.01, "maximum": 0.5},
            "weight_decay": {"type": "number", "minimum": 0},
            "early_stopping_patience": {"type": "integer", "minimum": 1},
            "device": {"type": "string", "enum": ["cuda", "cpu"], "default": "cuda"},
        },
        [
            "datasetId", "model", "labels", "task_type", "model_type", "learning_rate",
            "batch_size", "epochs", "logging_steps", "lr_scheduler", "min_lr",
            "val_steps", "seed", "num_trainable_layers", "registered_model_name",
        ],
        READ_ONLY,
    ),
    _tool(
        "start_embedding_run",
        "Start an embedding run. Requires the `quote_id` from estimate_embedding_run, "
        "which fixes the price. Present the quoted cost and get the user's explicit "
        "approval BEFORE calling this — nothing else will ask them. "
        "`batch_size` is required by the API. `modalities` must include 'sc' for "
        "TranscriptFormer models, which fail at run time without it.",
        {
            "datasetId": _UUID_FIELD,
            "model": {"type": "string", "minLength": 1, "maxLength": 200},
            "batch_size": {"type": "integer", "minimum": 1, "maximum": 4096},
            "model_version": {"type": "integer", "minimum": 1, "maximum": 10000},
            "modalities": {"type": "array", "items": {"type": "string", "enum": list(MODALITIES)},
                           "minItems": 1, "maxItems": 3},
            "pretrained_embedding_species": {"type": "array", "items": {"type": "string"}},
            "device": {"type": "string", "enum": ["cuda", "cpu"], "default": "cuda"},
            "quote_id": {"type": "string", "minLength": 1, "maxLength": 200},
        },
        ["datasetId", "model", "batch_size", "quote_id"],
        LAUNCH,
    ),
    _tool(
        "start_finetuning_run",
        "Start a fine-tuning run — the most expensive operation here. Requires the "
        "`quote_id` from estimate_finetuning_run. Present the quoted cost and get the "
        "user's explicit approval BEFORE calling this. `labels`, "
        "`task_type` and `model_type` are parallel arrays with one entry per task. The "
        "API requires every field listed as required here; it has no defaults for them.",
        {
            "datasetId": _UUID_FIELD,
            "model": {"type": "string", "minLength": 1, "maxLength": 200},
            "labels": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 10},
            "task_type": {"type": "array", "minItems": 1, "maxItems": 10,
                          "items": {"type": "string", "enum": ["prediction", "contrastive"]}},
            "model_type": {"type": "array", "minItems": 1, "maxItems": 10,
                           "items": {"type": "string", "enum": ["classification", "regression"]}},
            "learning_rate": {"type": "number", "exclusiveMinimum": 0},
            "batch_size": {"type": "integer", "minimum": 1, "maximum": 4096},
            "epochs": {"type": "integer", "minimum": 1, "maximum": 1000},
            "logging_steps": {"type": "integer", "minimum": 1},
            "lr_scheduler": {"type": "string", "enum": list(LR_SCHEDULERS)},
            "min_lr": {"type": "number", "exclusiveMinimum": 0},
            "val_steps": {"type": "integer", "minimum": 1},
            "seed": {"type": "integer", "minimum": 1},
            "num_trainable_layers": {"type": "integer", "minimum": 1, "maximum": 1000},
            "registered_model_name": {"type": "string", "minLength": 1, "maxLength": 200},
            "model_version": {"type": "integer", "minimum": 1, "maximum": 10000},
            "valDatasetId": _UUID_FIELD,
            "val_split": {"type": "number", "minimum": 0.01, "maximum": 0.5},
            "weight_decay": {"type": "number", "minimum": 0},
            "early_stopping_patience": {"type": "integer", "minimum": 1},
            "device": {"type": "string", "enum": ["cuda", "cpu"], "default": "cuda"},
            "quote_id": {"type": "string", "minLength": 1, "maxLength": 200},
        },
        [
            "datasetId", "model", "labels", "task_type", "model_type",
            "learning_rate", "batch_size", "epochs", "logging_steps", "lr_scheduler",
            "min_lr", "val_steps", "seed", "num_trainable_layers",
            "registered_model_name", "quote_id",
        ],
        LAUNCH,
    ),
    _tool(
        "list_runs",
        "List runs in a project, newest first, with their state and any child runs. "
        "`state` and `dagIds` accept several values.",
        {
            "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
            "state": {"type": "array", "items": {"type": "string", "enum": list(RUN_STATES)},
                      "minItems": 1, "maxItems": 4},
            "dagIds": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 10},
        },
        [],
        READ_ONLY,
    ),
    _tool(
        "get_run_details",
        "One run's state plus the dataset it used and the artifacts it produced. The "
        "artifacts carry s3_key values to read with list_files / read_file.",
        {"runId": {"type": "string", "minLength": 1, "maxLength": 200}},
        ["runId"],
        READ_ONLY,
    ),
    _tool(
        "list_files",
        "List a directory under /projects/<project>/data — typically a run's output "
        "directory taken from get_run_details.",
        {
            "path": {"type": "string", "minLength": 1, "maxLength": 500},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
        },
        ["path"],
        READ_ONLY,
    ),
    _tool(
        "read_file",
        "Read a UTF-8 text file under /projects/<project>/data, up to 1 MiB. Binary "
        "outputs such as .npy embedding matrices cannot be read through this tool; "
        "report their path to the user instead.",
        {
            "path": {"type": "string", "minLength": 1, "maxLength": 500},
            "maxBytes": {"type": "integer", "minimum": 1, "maximum": 1048576, "default": 65536},
        },
        ["path"],
        READ_ONLY,
    ),
    _tool(
        "list_s3_files",
        "List an object-storage prefix for the caller's projects, non-recursively.",
        {"path": {"type": "string", "maxLength": 500}},
        [],
        READ_ONLY,
    ),
    _tool(
        "list_umaps",
        "List UMAP artifacts, optionally for one dataset. Each row carries the dagRunId "
        "that produced it.",
        {
            "datasetId": _UUID_FIELD,
            "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
        },
        [],
        READ_ONLY,
    ),
    _tool(
        "get_umap",
        "Fetch the parsed UMAP coordinates and labels for a run. The payload can be "
        "large; prefer summarising it over echoing it.",
        {"runId": {"type": "string", "minLength": 1, "maxLength": 200}},
        ["runId"],
        READ_ONLY,
    ),
]

HANDLERS = {
    "list_models": _list_models,
    "estimate_embedding_run": _estimate_embedding_run,
    "estimate_finetuning_run": _estimate_finetuning_run,
    "list_datasets": _list_datasets,
    "get_dataset": _get_dataset,
    "get_dataset_columns": _get_dataset_columns,
    "get_dataset_obs_values": _get_dataset_obs_values,
    "start_embedding_run": _start_embedding_run,
    "start_finetuning_run": _start_finetuning_run,
    "list_runs": _list_runs,
    "get_run_details": _get_run_details,
    "list_files": _list_files,
    "read_file": _read_file,
    "list_s3_files": _list_s3_files,
    "list_umaps": _list_umaps,
    "get_umap": _get_umap,
}

INSTRUCTIONS = (
    "Tools over the Helical platform for embedding datasets with foundation models and "
    "fine-tuning them.\n\n"
    "Work is billed per token at a price that varies by model. Before every run: call the "
    "matching estimate_* tool (free, starts nothing), show the user the token count and "
    "the price, get an explicit yes, and pass the returned quote_id to the start_* tool. "
    "Nothing downstream will ask them to confirm — you are the only checkpoint. Credit is "
    "debited when the run starts. Then follow it with list_runs and get_run_details.\n\n"
    "You cannot choose a project — scope comes from the authenticated user. Pass dataset "
    "and model identifiers, never filesystem paths. Read outputs only under "
    "/projects/<project>/data. Never print credentials or raw upstream errors."
)


def _tool_result(value: Any) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}],
        "structuredContent": value if isinstance(value, dict) else {"result": value},
    }


def _handle(message: dict[str, Any]) -> dict[str, Any] | None:
    request_id = message.get("id")
    method = message.get("method")
    if request_id is None:
        return None

    if method == "initialize":
        requested = message.get("params", {}).get("protocolVersion")
        protocol = requested if requested in SUPPORTED_PROTOCOLS else LATEST_PROTOCOL
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": protocol,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": INSTRUCTIONS,
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = message.get("params", {})
        name = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(arguments, dict):
            result = {
                "content": [{"type": "text", "text": "Tool arguments must be an object."}],
                "isError": True,
            }
        else:
            try:
                handler = HANDLERS.get(name)
                if handler is None:
                    raise ToolError("Unknown tool.")
                result = _tool_result(handler(arguments))
            except ToolError as error:
                result = {"content": [{"type": "text", "text": str(error)}], "isError": True}
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": "Method not found"},
    }


def main() -> None:
    for line in sys.stdin:
        try:
            message = json.loads(line)
            response = _handle(message)
        except (json.JSONDecodeError, TypeError, AttributeError):
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            }
        if response is not None:
            sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()

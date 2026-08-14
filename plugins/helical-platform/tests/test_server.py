import importlib.util
import io
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError


SERVER_PATH = Path(__file__).parents[1] / "mcp" / "server.py"
SPEC = importlib.util.spec_from_file_location("helical_plugin_mcp", SERVER_PATH)
server = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(server)

ENV = {"HELICAL_API_BASE_URL": "https://dash.example.test", "HELICAL_API_TOKEN": "secret"}
UUID_A = "11111111-2222-3333-4444-555555555555"
UUID_B = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
# Everything that is not a pure read. Note `start*` is in here but does NOT launch:
# it prices and queues a confirmation. Only resolveConfirmation spends credit.
NOT_READ_ONLY = {
    "startEmbeddingRun", "startFinetuningRun",
    "initiateDatasetUpload", "completeDatasetUpload", "abortDatasetUpload", "registerDataset",
    "resolveConfirmation",
}


class FakeResponse:
    def __init__(self, payload=b"{}"):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit):
        return self.payload


def call(handler, arguments, payload=b"{}"):
    """Invoke a handler with the network mocked; return (result, urllib Request)."""
    with patch.dict(os.environ, ENV, clear=True), patch.object(
        server, "_open", return_value=FakeResponse(payload)
    ) as mocked:
        result = handler(arguments)
    return result, mocked.call_args.args[0]


class ProtocolTests(unittest.TestCase):
    def test_tools_and_handlers_agree(self):
        self.assertEqual({tool["name"] for tool in server.TOOLS}, set(server.HANDLERS))

    def test_triggers_are_not_read_only_and_nothing_is_destructive(self):
        for tool in server.TOOLS:
            expected_read_only = tool["name"] not in NOT_READ_ONLY
            self.assertEqual(tool["annotations"]["readOnlyHint"], expected_read_only, tool["name"])
            self.assertFalse(tool["annotations"]["destructiveHint"], tool["name"])

    def test_schemas_reject_unknown_arguments(self):
        for tool in server.TOOLS:
            self.assertFalse(tool["inputSchema"]["additionalProperties"], tool["name"])

    def test_instructions_put_the_confirmation_duty_on_the_agent(self):
        with patch.object(server, "PRO_FORMA_MODE", False):
            response = server._handle(
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
            )
        instructions = response["result"]["instructions"]
        self.assertIn("explicit yes", instructions)
        self.assertIn("cannot choose a project", instructions)

    def test_non_preview_tool_calls_still_reach_the_reference_adapter(self):
        with patch.object(server, "PRO_FORMA_MODE", False), patch.object(
            server, "_list_models", return_value={"models": []}
        ) as handler, patch.dict(
            server.HANDLERS, {"list_models": server._list_models}
        ):
            response = server._handle(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "list_models", "arguments": {}},
                }
            )
        handler.assert_called_once_with({})
        self.assertEqual(response["result"]["structuredContent"], {"models": []})
        self.assertNotIn("isError", response["result"])


class ProFormaTests(unittest.TestCase):
    def _handle(self, message):
        with patch.object(server, "PRO_FORMA_MODE", True), patch.dict(
            os.environ, {}, clear=True
        ), patch.object(server, "_open") as opened:
            response = server._handle(message)
        opened.assert_not_called()
        return response

    def test_discovery_remains_available_and_is_honest(self):
        initialized = self._handle(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        )
        self.assertIn("pro forma", initialized["result"]["instructions"])
        self.assertIn("no network request", initialized["result"]["instructions"])

        listed = self._handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools = listed["result"]["tools"]
        self.assertEqual({tool["name"] for tool in tools}, set(server.HANDLERS))
        for tool in tools:
            self.assertTrue(tool["description"].startswith("Preview only:"), tool["name"])

    def test_preview_is_the_safe_default(self):
        self.assertTrue(server.PRO_FORMA_MODE)

    def test_every_declared_tool_short_circuits_before_validation_or_network(self):
        for tool in server.TOOLS:
            with self.subTest(tool=tool["name"]):
                response = self._handle(
                    {
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "tools/call",
                        "params": {"name": tool["name"], "arguments": {}},
                    }
                )
                result = response["result"]
                self.assertTrue(result["isError"])
                self.assertEqual(
                    result["content"],
                    [{"type": "text", "text": server.PRO_FORMA_MESSAGE}],
                )
                self.assertNotIn("structuredContent", result)

    def test_an_undeclared_tool_is_a_protocol_error(self):
        response = self._handle(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "future_tool", "arguments": {}},
            }
        )
        self.assertEqual(response["error"]["code"], -32602)
        self.assertEqual(response["error"]["message"], "Unknown tool: future_tool")

    def test_structurally_invalid_arguments_are_a_protocol_error(self):
        response = self._handle(
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "list_models", "arguments": "not-an-object"},
            }
        )
        self.assertEqual(response["error"]["code"], -32602)
        self.assertEqual(response["error"]["message"], "Tool arguments must be an object.")


class ConfigurationTests(unittest.TestCase):
    def test_missing_configuration_fails_closed(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(server.ToolError, "not configured"):
                server._list_datasets({})

    def test_plaintext_upstream_is_refused(self):
        with patch.dict(os.environ, dict(ENV, HELICAL_API_BASE_URL="http://d.test"), clear=True):
            with self.assertRaisesRegex(server.ToolError, "HTTPS origin"):
                server._list_datasets({})


class RoutingTests(unittest.TestCase):
    """Paths follow the dashboard's route shapes, under the configured route group."""

    def test_list_datasets_hits_the_data_route_with_bearer(self):
        _, request = call(server._list_datasets, {"organism": "human", "limit": 5})
        self.assertTrue(request.full_url.startswith(
            f"https://dash.example.test{server.API_ROOT}/data?"))
        self.assertIn("organism=human", request.full_url)
        self.assertIn("limit=5", request.full_url)
        self.assertEqual(request.get_header("Authorization"), "Bearer secret")

    def test_list_models_sends_only_its_own_filters(self):
        _, request = call(server._list_models, {"status": "all"})
        self.assertIn(f"{server.API_ROOT}/models?", request.full_url)
        self.assertIn("status=all", request.full_url)

    def test_embedding_trigger_posts_to_an_unscoped_path(self):
        _, request = call(
            server._start_embedding_run,
            {"datasetId": UUID_B, "model": "scgpt", "batch_size": 8},
        )
        self.assertEqual(request.method, "POST")
        self.assertEqual(
            request.full_url,
            f"https://dash.example.test{server.API_ROOT}/airflow/trigger/embedding",
        )
        body = json.loads(request.data)
        self.assertEqual(
            body,
            {"datasetId": UUID_B, "model": "scgpt", "batch_size": 8},
        )

    def test_run_details_uses_its_real_path(self):
        _, details = call(server._get_run_details, {"runId": "dag-run-1"})
        self.assertTrue(details.full_url.endswith("/airflow/run-details/dag-run-1"))

    def test_list_runs_joins_multi_value_filters_with_commas(self):
        _, request = call(
            server._list_runs,
            {"state": ["running", "succeeded"], "dagIds": ["embedding"]},
        )
        self.assertIn("state=running%2Csucceeded", request.full_url)
        self.assertIn("dagIds=embedding", request.full_url)


class ValidationTests(unittest.TestCase):
    def test_uuid_fields_reject_traversal_and_junk(self):
        for bad in ("../../secrets", "not-a-uuid", ""):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(server.ToolError, "must be a UUID"):
                    server._get_dataset({"id": bad})

    def test_file_paths_must_be_inside_a_project_data_root(self):
        for bad in ("/etc/passwd", "/projects/../etc", "relative/path",
                    "/projects/p/data/../../../etc"):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(server.ToolError, "must be an absolute path"):
                    server._read_file({"path": bad})

    def test_a_legitimate_project_path_is_accepted(self):
        _, request = call(server._read_file, {"path": "/projects/demo/data/airflow/embedding/x.json"})
        self.assertIn("path=%2Fprojects%2Fdemo%2Fdata", request.full_url)

    def test_embedding_requires_batch_size_because_the_api_does(self):
        with self.assertRaisesRegex(server.ToolError, "batch_size must be"):
            server._start_embedding_run({"datasetId": UUID_B, "model": "scgpt"})

    def test_modalities_are_restricted_to_the_api_enum(self):
        with self.assertRaisesRegex(server.ToolError, "modalities must be"):
            server._start_embedding_run(
                {"datasetId": UUID_B, "model": "scgpt", "batch_size": 8,
                 "modalities": ["protein"]}
            )

    def test_finetuning_parallel_arrays_must_line_up_with_labels(self):
        base = {
            "datasetId": UUID_B, "model": "scgpt",
            "labels": ["cell_type", "disease"], "task_type": ["prediction"],
            "model_type": ["classification"], "learning_rate": 1e-4, "batch_size": 16,
            "epochs": 1, "logging_steps": 10, "lr_scheduler": "constant", "min_lr": 1e-6,
            "val_steps": 500, "seed": 42, "num_trainable_layers": 2,
            "registered_model_name": "scgpt_custom",
        }
        with self.assertRaisesRegex(server.ToolError, "one entry per label"):
            server._start_finetuning_run(base)

    def test_finetuning_accepts_a_complete_request(self):
        body_in = {
            "datasetId": UUID_B, "model": "scgpt",
            "labels": ["cell_type"], "task_type": ["prediction"],
            "model_type": ["classification"], "learning_rate": 1e-4, "batch_size": 16,
            "epochs": 1, "logging_steps": 10, "lr_scheduler": "constant", "min_lr": 1e-6,
            "val_steps": 500, "seed": 42, "num_trainable_layers": 2,
            "registered_model_name": "scgpt_custom", "quote_id": "q-1",
        }
        _, request = call(server._start_finetuning_run, body_in)
        sent = json.loads(request.data)
        self.assertEqual(sent["registered_model_name"], "scgpt_custom")
        self.assertEqual(sent["labels"], ["cell_type"])
        self.assertNotIn("conversationId", sent)  # it belongs in the path, not the body


class ScopingTests(unittest.TestCase):
    """Scope is derived from the token and never transmitted (DESIGN.md 2.3.1)."""

    FORBIDDEN = {"projectId", "conversationId", "project", "owner", "userId", "slug",
                 "workspace", "tenant"}

    def test_no_tool_lets_the_model_choose_a_project(self):
        for tool in server.TOOLS:
            leaked = set(tool["inputSchema"]["properties"]) & self.FORBIDDEN
            self.assertEqual(leaked, set(), f"{tool['name']} exposes scope: {leaked}")

    def test_no_request_carries_scope_in_url_or_headers(self):
        probes = [
            (server._list_models, {}),
            (server._list_datasets, {}),
            (server._list_runs, {}),
            (server._start_embedding_run,
             {"datasetId": UUID_B, "model": "m", "batch_size": 8, "quote_id": "q-1"}),
        ]
        for handler, arguments in probes:
            with self.subTest(handler=handler.__name__):
                _, request = call(handler, arguments)
                lowered = request.full_url.lower()
                for term in ("projectid", "conversationid", "x-helical-project"):
                    self.assertNotIn(term, lowered)
                for name in request.headers:
                    self.assertNotIn("project", name.lower())

    def test_the_confirmation_tool_is_not_part_of_this_surface(self):
        # Direct execution (DESIGN.md 6.1) means there is no approval queue to poll.
        self.assertNotIn("get_confirmation_status", server.HANDLERS)


class EstimateTests(unittest.TestCase):
    """Estimate-before-spend, enforced structurally rather than only in prose."""

    EMB = {"datasetId": UUID_B, "model": "scgpt", "batch_size": 8}
    FT = {
        "datasetId": UUID_B, "model": "scgpt", "labels": ["cell_type"],
        "task_type": ["prediction"], "model_type": ["classification"],
        "learning_rate": 1e-4, "batch_size": 16, "epochs": 1, "logging_steps": 10,
        "lr_scheduler": "constant", "min_lr": 1e-6, "val_steps": 500, "seed": 42,
        "num_trainable_layers": 2, "registered_model_name": "scgpt_custom",
    }

    def test_a_run_request_does_not_accept_a_quote(self):
        """The confirmation flow mints the quote server-side and embeds it in the
        confirmation, so there is no caller-supplied quote to mismatch. A schema that
        still accepted one would let an agent pair run A with run B's price."""
        for name in ("startEmbeddingRun", "startFinetuningRun"):
            with self.subTest(tool=name):
                tool = next(x for x in server.TOOLS if x["name"] == name)
                self.assertNotIn("quote_id", tool["inputSchema"]["properties"], name)
                self.assertNotIn("quote_id", tool["inputSchema"]["required"], name)

    def test_a_run_request_starts_nothing_and_only_approval_launches(self):
        """start* is annotated as a write but not as the launch; resolveConfirmation is
        the billable call. Agent hosts read these hints to decide what to auto-approve,
        so 'stages a request' and 'spends money' must not look alike."""
        starts = [x for x in server.TOOLS if x["name"].startswith("start")]
        self.assertTrue(starts)
        for tool in starts:
            self.assertFalse(tool["annotations"]["readOnlyHint"], tool["name"])
        resolve = next(x for x in server.TOOLS if x["name"] == "resolveConfirmation")
        self.assertFalse(resolve["annotations"]["readOnlyHint"])
        self.assertFalse(resolve["annotations"]["idempotentHint"])

    def _error(self, code, body):
        return server._http_error(
            HTTPError("https://dash.example.test", code, "", {}, io.BytesIO(json.dumps(body).encode()))
        )

    def test_the_apis_error_string_is_surfaced_but_detail_is_dropped(self):
        error = self._error(404, {"error": "Dataset not found", "detail": "prisma: internal"})
        self.assertIn("Dataset not found", str(error))
        self.assertNotIn("prisma", str(error))

    def test_401_explains_the_token_rather_than_echoing_upstream(self):
        error = self._error(401, {"error": "Invalid JWT"})
        self.assertIn("Cognito access token", str(error))

    def test_unparseable_error_body_still_yields_a_safe_message(self):
        error = server._http_error(HTTPError("https://d.test", 502, "", {}, None))
        self.assertIn("Try again later", str(error))


if __name__ == "__main__":
    unittest.main()

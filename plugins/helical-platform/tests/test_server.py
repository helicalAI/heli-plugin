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
LAUNCHING = {"start_embedding_run", "start_finetuning_run"}


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
            expected_read_only = tool["name"] not in LAUNCHING
            self.assertEqual(tool["annotations"]["readOnlyHint"], expected_read_only, tool["name"])
            self.assertFalse(tool["annotations"]["destructiveHint"], tool["name"])

    def test_schemas_reject_unknown_arguments(self):
        for tool in server.TOOLS:
            self.assertFalse(tool["inputSchema"]["additionalProperties"], tool["name"])

    def test_instructions_put_the_confirmation_duty_on_the_agent(self):
        response = server._handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        instructions = response["result"]["instructions"]
        self.assertIn("explicit yes", instructions)
        self.assertIn("cannot choose a project", instructions)


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
            {"datasetId": UUID_B, "model": "scgpt", "batch_size": 8, "quote_id": "q-1"},
        )
        self.assertEqual(request.method, "POST")
        self.assertEqual(
            request.full_url,
            f"https://dash.example.test{server.API_ROOT}/airflow/trigger/embedding",
        )
        body = json.loads(request.data)
        self.assertEqual(
            body,
            {"datasetId": UUID_B, "model": "scgpt", "batch_size": 8, "quote_id": "q-1"},
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

    def test_each_operation_has_its_own_estimate_endpoint(self):
        _, emb = call(server._estimate_embedding_run, self.EMB)
        self.assertTrue(emb.full_url.endswith("/airflow/estimate/embedding"))
        _, ft = call(server._estimate_finetuning_run, self.FT)
        self.assertTrue(ft.full_url.endswith("/airflow/estimate/finetuning"))

    def test_an_estimate_prices_exactly_what_the_run_would_do(self):
        """Same body as the trigger, minus the quote — so the quote cannot drift."""
        _, estimate = call(server._estimate_embedding_run, self.EMB)
        _, run = call(server._start_embedding_run, dict(self.EMB, quote_id="q-1"))
        priced, started = json.loads(estimate.data), json.loads(run.data)
        self.assertEqual(started.pop("quote_id"), "q-1")
        self.assertEqual(priced, started)

    def test_estimates_never_carry_a_quote(self):
        _, request = call(server._estimate_finetuning_run, dict(self.FT, quote_id="q-1"))
        self.assertNotIn("quote_id", json.loads(request.data))

    def test_a_run_cannot_start_without_a_quote(self):
        for handler, args in ((server._start_embedding_run, self.EMB),
                              (server._start_finetuning_run, self.FT)):
            with self.subTest(handler=handler.__name__):
                with self.assertRaisesRegex(server.ToolError, "quote_id is required"):
                    handler(args)


class ErrorMappingTests(unittest.TestCase):
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

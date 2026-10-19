"""Cloud import contracts with fake S3 clients and real temporary output files."""

import csv
import io
import json
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

from botocore.exceptions import ClientError, NoCredentialsError

from app.io import aws_import, aws_telemetry


def client_error(code, operation="ListBuckets"):
    return ClientError({"Error": {"Code": code, "Message": "test service response"}}, operation)


class CloudImportTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.client = Mock(name="fake_s3_client")
        # boto3.client is replaced before constructing either importer, so no
        # credentials lookup, instance metadata request, or AWS call can occur.
        self.client_factory = self.stack.enter_context(patch.object(aws_import.boto3, "client", return_value=self.client))
        self.stack.enter_context(patch.object(aws_import, "logger"))
        self.stack.enter_context(patch.object(aws_telemetry, "logger"))
        self.importer = aws_import.AWSS3Importer()
        self.telemetry = aws_telemetry.AWSTelemetryImporter()

    def read_csv(self, path):
        with path.open(newline="", encoding="utf-8") as stream:
            return list(csv.reader(stream))

    def test_constructors_use_default_credential_chain_and_requested_region(self):
        for importer_class in (aws_import.AWSS3Importer, aws_telemetry.AWSTelemetryImporter):
            with self.subTest(importer=importer_class.__name__):
                self.client_factory.reset_mock()
                importer = importer_class(region_name="eu-west-3")
                self.client_factory.assert_called_once_with("s3", region_name="eu-west-3")
                self.assertIs(importer.s3_client, self.client)

    def test_constructors_forward_explicit_credentials(self):
        for importer_class in (aws_import.AWSS3Importer, aws_telemetry.AWSTelemetryImporter):
            with self.subTest(importer=importer_class.__name__):
                self.client_factory.reset_mock()
                importer_class("test-access-key", "test-secret-key", "eu-west-3")
                self.client_factory.assert_called_once_with("s3", aws_access_key_id="test-access-key", aws_secret_access_key="test-secret-key", region_name="eu-west-3")

    def test_missing_optional_sdk_has_an_actionable_error(self):
        for module, importer_class in ((aws_import, aws_import.AWSS3Importer), (aws_telemetry, aws_telemetry.AWSTelemetryImporter)):
            with self.subTest(importer=importer_class.__name__), patch.object(module, "BOTO3_AVAILABLE", False):
                self.client_factory.reset_mock()
                with self.assertRaisesRegex(ImportError, "boto3 not installed"):
                    importer_class()
                self.client_factory.assert_not_called()

    def test_client_initialization_failures_are_not_hidden(self):
        failure = RuntimeError("client unavailable")
        self.client_factory.side_effect = failure
        for importer_class in (aws_import.AWSS3Importer, aws_telemetry.AWSTelemetryImporter):
            with self.subTest(importer=importer_class.__name__), self.assertRaises(RuntimeError) as error:
                importer_class()
            self.assertIs(error.exception, failure)

    def test_bucket_names_are_returned_without_metadata(self):
        self.client.list_buckets.return_value = {"Buckets": [{"Name": "home-data", "CreationDate": "ignored"}, {"Name": "backup"}]}
        self.assertEqual(self.importer.list_buckets(), ["home-data", "backup"])
        self.assertEqual(self.telemetry.list_buckets(), ["home-data", "backup"])

    def test_empty_bucket_response_returns_an_empty_list(self):
        self.client.list_buckets.return_value = {}
        self.assertEqual(self.importer.list_buckets(), [])
        self.assertEqual(self.telemetry.list_buckets(), [])

    def test_bucket_authentication_errors_explain_the_required_action(self):
        cases = (
            (NoCredentialsError(), "No AWS credentials configured"),
            (client_error("AccessDenied"), "s3:ListAllMyBuckets"),
            (client_error("InvalidAccessKeyId"), "Invalid Access Key ID"),
            (client_error("SignatureDoesNotMatch"), "Invalid Secret Key"),
            (client_error("ServiceUnavailable"), "ServiceUnavailable"),
            (RuntimeError("offline"), "Failed to list S3 buckets"),
        )
        for failure, explanation in cases:
            with self.subTest(failure=type(failure).__name__, explanation=explanation):
                self.client.list_buckets.side_effect = failure
                with self.assertRaisesRegex(Exception, explanation):
                    self.importer.list_buckets()

    def test_telemetry_bucket_client_error_returns_empty_result(self):
        self.client.list_buckets.side_effect = client_error("AccessDenied")
        self.assertEqual(self.telemetry.list_buckets(), [])

    def test_scenario_object_listing_consumes_every_page_and_forwards_prefix(self):
        paginator = self.client.get_paginator.return_value
        paginator.paginate.return_value = iter([
            {"Contents": [{"Key": "logs/one.json"}]},
            {},
            {"Contents": [{"Key": "logs/two.json"}, {"Key": "logs/three.csv"}]},
        ])
        self.assertEqual(self.importer.list_objects("home-data", "logs/"), ["logs/one.json", "logs/two.json", "logs/three.csv"])
        self.client.get_paginator.assert_called_once_with("list_objects_v2")
        paginator.paginate.assert_called_once_with(Bucket="home-data", Prefix="logs/")

    def test_telemetry_object_listing_keeps_metadata_from_every_page(self):
        first = {"Key": "logs/one.json", "Size": 100, "LastModified": datetime(2026, 1, 1)}
        second = {"Key": "logs/two.json", "Size": 200}
        paginator = self.client.get_paginator.return_value
        paginator.paginate.return_value = iter([{"Contents": [first]}, {}, {"Contents": [second]}])
        self.assertEqual(self.telemetry.list_objects("home-data", "logs/"), [first, second])
        self.client.get_paginator.assert_called_once_with("list_objects_v2")
        paginator.paginate.assert_called_once_with(Bucket="home-data", Prefix="logs/")

    def test_listing_error_does_not_return_misleading_partial_results(self):
        def failed_pages():
            yield {"Contents": [{"Key": "partial.csv"}]}
            raise client_error("AccessDenied", "ListObjectsV2")

        for importer in (self.importer, self.telemetry):
            with self.subTest(importer=type(importer).__name__):
                self.client.get_paginator.return_value.paginate.return_value = failed_pages()
                self.assertEqual(importer.list_objects("home-data"), [])

    def test_download_preserves_unicode_csv_text(self):
        content = "timestamp,label,value\r\n2026-01-01,entrée 🏠,0\r\n"
        for downloader in (self.importer.download_csv_file, self.telemetry.download_file):
            with self.subTest(downloader=downloader.__name__):
                self.client.get_object.reset_mock()
                self.client.get_object.return_value = {"Body": io.BytesIO(content.encode("utf-8"))}
                self.assertEqual(downloader("home-data", "logs/data.csv"), content)
                self.client.get_object.assert_called_once_with(Bucket="home-data", Key="logs/data.csv")

    def test_downloaded_json_is_decoded(self):
        payload = {"sensor": "entrée", "value": 0}
        self.client.get_object.return_value = {"Body": io.BytesIO(json.dumps(payload).encode("utf-8"))}
        self.assertEqual(self.importer.download_json_file("home-data", "sensor.json"), payload)

    def test_invalid_downloaded_json_returns_none(self):
        self.client.get_object.return_value = {"Body": io.BytesIO(b"not-json")}
        self.assertIsNone(self.importer.download_json_file("home-data", "broken.json"))

    def test_download_client_errors_and_invalid_utf8_return_none(self):
        for downloader in (self.importer.download_csv_file, self.importer.download_json_file, self.telemetry.download_file):
            with self.subTest(downloader=downloader.__name__, failure="client"):
                self.client.get_object.side_effect = client_error("NoSuchKey", "GetObject")
                self.assertIsNone(downloader("home-data", "missing"))
            with self.subTest(downloader=downloader.__name__, failure="encoding"):
                self.client.get_object.side_effect = None
                self.client.get_object.return_value = {"Body": io.BytesIO(b"\xff")}
                self.assertIsNone(downloader("home-data", "bad-encoding"))

    def test_raw_csv_persistence_does_not_damage_existing_crlf_line_endings(self):
        path = self.root / "download.csv"
        content = "timestamp,label,value\r\n2026-01-01,entrée 🏠,0\r\n"
        self.assertTrue(self.importer.save_to_local_csv(content, str(path)))
        self.assertEqual(path.read_bytes(), content.encode("utf-8"))
        self.assertEqual(len(self.read_csv(path)), 2)

    def test_raw_csv_write_failure_returns_false(self):
        with patch("app.io.aws_import.open", side_effect=PermissionError("read-only destination")):
            self.assertFalse(self.importer.save_to_local_csv("content", "unused.csv"))

    def test_detection_recognizes_supported_telemetry_field_names(self):
        for key in ("apower", "power_w", "power", "voltage", "voltage_v", "current", "current_a"):
            with self.subTest(key=key):
                self.assertEqual(self.telemetry.detect_data_type(json.dumps({key: 0})), "smartmeter")
        self.assertEqual(self.telemetry.detect_data_type('{"ts":0,"energy":0}'), "smartmeter")
        for key in ("temperature_c", "humidity_rh", "label"):
            with self.subTest(key=key):
                self.assertEqual(self.telemetry.detect_data_type(json.dumps({key: 0})), "dht")

    def test_detection_skips_invalid_lines_and_unknown_json(self):
        content = '\n'.join(["", "invalid-json", "null", "42", "[]", '{"unrelated":1}', '{"temperature_c":0}'])
        self.assertEqual(self.telemetry.detect_data_type(content), "dht")
        self.assertIsNone(self.telemetry.detect_data_type('{"unrelated":1}'))
        self.assertIsNone(self.telemetry.detect_data_type(""))

    def test_smartmeter_detection_has_priority_over_label(self):
        self.assertEqual(self.telemetry.detect_data_type('{"label":"oven","apower":0}'), "smartmeter")

    def test_dht_parser_preserves_zero_values(self):
        content = '{"timestamp_iso":"2026-01-01T12:00:00","label":"cold","gpio":0,"temperature_c":0,"humidity_rh":0}'
        self.assertEqual(self.telemetry.parse_dht_data(content), [{"timestamp": "2026-01-01T12:00:00", "label": "cold", "gpio": 0, "temperature": 0, "humidity": 0}])

    def test_smartmeter_parser_preserves_zero_values(self):
        content = '{"ts":0,"apower":0,"voltage":0,"current":0,"energy_total":0}'
        self.assertEqual(self.telemetry.parse_smartmeter_data(content), [{"timestamp": 0, "power": 0, "voltage": 0, "current": 0, "energy": 0}])

    def test_parsers_skip_malformed_and_non_object_lines(self):
        for parser, payload in (
            (self.telemetry.parse_dht_data, {"timestamp_iso": "2026-01-01", "temperature_c": 20}),
            (self.telemetry.parse_smartmeter_data, {"ts": 1700000000000, "apower": 20}),
        ):
            with self.subTest(parser=parser.__name__):
                content = '\n'.join(["invalid-json", "null", "42", "[]", '"text"', json.dumps(payload), ""])
                self.assertEqual(len(parser(content)), 1)

    def test_smartmeter_parser_supports_the_aliases_accepted_by_detection(self):
        payload = {"timestamp_iso": "2026-01-01 12:00:00", "power_w": 0, "voltage_v": 230, "current_a": 0, "energy": 10}
        self.assertEqual(self.telemetry.parse_smartmeter_data(json.dumps(payload)), [{"timestamp": "2026-01-01 12:00:00", "power": 0, "voltage": 230, "current": 0, "energy": 10}])

    def test_alias_fallback_treats_none_as_missing_but_keeps_zero(self):
        payload = {"ts": 0, "apower": 0, "power": 99, "voltage": None, "voltage_v": 230, "energy_total": None, "energy": 0}
        record = self.telemetry.parse_smartmeter_data(json.dumps(payload))[0]
        self.assertEqual(record["timestamp"], 0)
        self.assertEqual(record["power"], 0)
        self.assertEqual(record["voltage"], 230)
        self.assertEqual(record["energy"], 0)

    def test_dht_persistence_quotes_labels_and_keeps_empty_optional_values(self):
        path = self.root / "temperature.csv"
        self.assertTrue(self.telemetry.save_dht_to_csv([{"timestamp": "2026-01-01 12:00:00", "label": "kitchen, entrée", "gpio": 4, "temperature": 0, "humidity": 0}, {"timestamp": "2026-01-01 12:00:01"}], str(path)))
        self.assertEqual(self.read_csv(path), [
            ["timestamp_iso", "label", "gpio", "temp_C", "hum_%"],
            ["2026-01-01 12:00:00", "kitchen, entrée", "4", "0", "0"],
            ["2026-01-01 12:00:01", "", "", "", ""],
        ])

    def test_smartmeter_persistence_emits_iso_timestamps_for_unix_records(self):
        path = self.root / "smartmeter.csv"
        seconds = 1700000000
        records = [{"timestamp": seconds * 1000, "power": 0, "voltage": 230, "current": 0}, {"timestamp": str(seconds), "power": 10}]
        self.assertTrue(self.telemetry.save_smartmeter_to_csv(records, str(path), "oven"))
        rows = self.read_csv(path)
        self.assertEqual(rows[0], ["timestamp_iso", "device", "device_id", "ip", "power_W", "voltage_V", "current_A"])
        self.assertEqual(rows[1][1:], ["oven", "oven", "imported", "0", "230", "0"])
        self.assertEqual(datetime.fromisoformat(rows[1][0]), datetime.fromtimestamp(seconds))
        self.assertEqual(rows[1][0], rows[2][0])

    def test_telemetry_persistence_preserves_iso_input_timestamps(self):
        for saver in (self.telemetry.save_dht_to_csv, self.telemetry.save_smartmeter_to_csv):
            with self.subTest(saver=saver.__name__):
                path = self.root / f"{saver.__name__}.csv"
                self.assertTrue(saver([{"timestamp": "2026-01-01T12:00:00+00:00"}], str(path)))
                self.assertEqual(self.read_csv(path)[1][0], "2026-01-01T12:00:00+00:00")

    def test_imported_smartmeter_json_is_readable_by_simulator_graph_loader(self):
        from app.hardware.smartmeter import load_power_df

        path = self.root / "smartmeter_oven.csv"
        records = self.telemetry.parse_smartmeter_data('{"ts":1700000000000,"apower":0,"voltage":230,"current":0}')
        self.assertTrue(self.telemetry.save_smartmeter_to_csv(records, str(path), "oven"))
        frame = load_power_df(str(path), device="oven")
        self.assertFalse(frame.empty)
        self.assertEqual(frame["value"].tolist(), [0])

    def test_imported_dht_json_is_readable_by_simulator_temperature_loader(self):
        from app.hardware.real_sensors import load_temp_by_label_any_csv

        path = self.root / "dht_cold.csv"
        records = self.telemetry.parse_dht_data('{"ts":1700000000000,"label":"cold","gpio":4,"temperature_c":0,"humidity_rh":0}')
        self.assertTrue(self.telemetry.save_dht_to_csv(records, str(path)))
        frame = load_temp_by_label_any_csv("cold", logs_dir=str(self.root))
        self.assertFalse(frame.empty)
        self.assertEqual(frame["value"].tolist(), [0])

    def test_empty_telemetry_records_save_valid_header_only_files(self):
        for saver in (self.telemetry.save_dht_to_csv, self.telemetry.save_smartmeter_to_csv):
            with self.subTest(saver=saver.__name__):
                path = self.root / f"{saver.__name__}.csv"
                self.assertTrue(saver([], str(path)))
                self.assertEqual(len(self.read_csv(path)), 1)

    def test_telemetry_write_errors_return_false(self):
        for saver in (self.telemetry.save_dht_to_csv, self.telemetry.save_smartmeter_to_csv):
            with self.subTest(saver=saver.__name__), patch("app.io.aws_telemetry.open", side_effect=PermissionError("read-only destination")):
                self.assertFalse(saver([{"timestamp": "2026-01-01"}], "unused.csv"))


if __name__ == "__main__":
    unittest.main()

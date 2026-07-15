import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from app.controllers.llm_smartmeter import _load_time_value_csv
from LLM.smartmeter.common.llm_interpreter import (
    call_openai_compatible_chat,
    extract_json_object,
    generate_llm_interpretation,
)


class LlmSmartMeterCsvTests(unittest.TestCase):
    def test_loader_recovers_concatenated_smartmeter_rows(self):
        content = (
            "timestamp_iso,device,device_id,ip,power_W,voltage_V,current_A\n"
            "2026-01-01 00:00:00.000,computer,sm_pc,192.168.1.1,10,230,0.04\n"
            "2026-01-01 00:01:00.000,computer,sm_pc,192.168.1.1,,,"
            "2026-01-01 00:02:00.000,computer,sm_pc,192.168.1.1,20,231,0.08\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "smartmeter_sm_pc.csv"
            path.write_text(content, encoding="utf-8")

            df = _load_time_value_csv(path)

        self.assertEqual(df["value"].tolist(), [10.0, 20.0])


class LlmSmartMeterInterpretationTests(unittest.TestCase):
    def test_interpretation_prompt_is_generated_without_network_call(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            results = base / "results_k3"
            results.mkdir()
            (results / "cluster_summary.csv").write_text(
                "cluster,n_cycles,duration_mean,duration_std,max_power_mean,max_power_std,"
                "mean_power_mean,mean_power_std,energy_mean_kwh,energy_std_kwh,peak_time_mean\n"
                "0,7,277.3,12.0,410.0,30.0,231.0,20.0,1.020,0.2,0.4\n"
                "1,2,90.0,4.0,180.0,10.0,120.0,8.0,0.180,0.02,0.1\n",
                encoding="utf-8",
            )
            (results / "cluster_representatives.csv").write_text(
                "cluster,cycle_id,duration_minutes,max_power,mean_power,energy_kwh\n"
                "0,4,275.0,405.0,230.0,1.000\n"
                "1,9,88.0,170.0,119.0,0.170\n",
                encoding="utf-8",
            )
            selected_case = base / "selected_case_latest_incomplete_day.csv"
            selected_case.write_text(
                "cycle_id,cluster,start_time,duration_minutes,max_power,mean_power,energy_kwh,"
                "distance_to_last_incomplete_day\n"
                "4,0,2026-01-01 12:00:00,275.0,405.0,230.0,1.000,0.42\n",
                encoding="utf-8",
            )

            status = generate_llm_interpretation(
                appliance_label="Computer",
                appliance_key="computer",
                output_dir=base,
                results_dir=results,
                chosen_k=3,
                selected_cluster=0,
                dominant_cluster=0,
                selected_cycle_id=4,
                selected_case_csv=selected_case,
                enable_llm=False,
            )

            self.assertEqual(status["status"], "prompt_ready")
            self.assertTrue((base / "llm_interpretation_prompt.json").exists())
            self.assertTrue((base / "llm_interpretation_status.json").exists())
            self.assertFalse((base / "llm_interpretation.json").exists())

    def test_extract_json_object_from_text_response(self):
        parsed = extract_json_object('text before {"cluster_profiles": [], "limitations": []} text after')
        self.assertEqual(parsed["cluster_profiles"], [])
        self.assertEqual(parsed["limitations"], [])

    def test_openai_compatible_chat_call_parses_json_response(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return (
                    b'{"choices":[{"message":{"content":"{\\"cluster_profiles\\":[],'
                    b'\\"selected_case_explanation\\":\\"ok\\",'
                    b'\\"completion_explanation\\":null,'
                    b'\\"limitations\\":[]}"}}]}'
                )

        with patch("urllib.request.urlopen", return_value=FakeResponse()) as mocked_urlopen:
            parsed = call_openai_compatible_chat(
                messages=[{"role": "user", "content": "test"}],
                api_key="fake-key",
                model="fake-model",
                base_url="https://example.test/v1/chat/completions",
            )

        self.assertEqual(parsed["selected_case_explanation"], "ok")
        self.assertEqual(parsed["_llm_metadata"]["model"], "fake-model")
        mocked_urlopen.assert_called_once()


if __name__ == "__main__":
    unittest.main()

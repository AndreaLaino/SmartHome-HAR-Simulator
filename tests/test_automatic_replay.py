"""Replay/export contracts with real CSVs and no Tk or hardware dependencies."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd

import automatic
import graph


class ReplayFileTests(unittest.TestCase):
    def test_supported_delimiters_are_sorted_and_duplicate_samples_keep_last(self):
        with tempfile.TemporaryDirectory() as directory:
            for delimiter in (",", "\t", ";"):
                with self.subTest(delimiter=delimiter):
                    path = Path(directory) / "sensor.csv"
                    path.write_text(
                        delimiter.join(("time", "state")) + "\n" + "\n".join(
                            delimiter.join(row) for row in [
                                ("2026-07-14 12:02", "1"),
                                ("2026-07-14 12:00", "0"),
                                ("2026-07-14 12:02", "0"),
                            ]
                        ), encoding="utf-8",
                    )
                    times, values = automatic.read_timestamp_state_file(str(path))
                    self.assertEqual(times, ["2026-07-14 12:00:00", "2026-07-14 12:02:00"])
                    self.assertEqual(values, [0, 0])

    def test_duplicate_resolution_is_stable_with_many_equal_timestamps(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "many.csv"
            pd.DataFrame({"time": ["2026-07-14 12:00"] * 80, "state": range(80)}).to_csv(path, index=False)
            self.assertEqual(automatic.read_timestamp_state_file(str(path))[1], [79])

    def test_missing_columns_are_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.csv"
            path.write_text("time,unrelated\n2026-07-14 12:00,1\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                automatic.read_timestamp_state_file(str(path))


def replay_context(rows):
    frame = pd.DataFrame(rows, columns=["timestamp_sim", "event_type", "subject", "name", "value"])
    return SimpleNamespace(automatic_state={
        "interactions_df": frame,
        "sensors_in_csv": [("PIR", "motion"), ("Smart Meter", "power")],
        "sensors_listbox": Mock(curselection=Mock(return_value=(0, 1))),
    }, preferences={})


class InteractionReplayTests(unittest.TestCase):
    def test_minute_aggregation_preserves_last_binary_edge_and_peak_power(self):
        ctx = replay_context([
            ("2026-07-14 12:01:00", "sensor", "PIR", "motion", 0),
            ("2026-07-14 12:00:05", "sensor", "PIR", "motion", 1),
            ("2026-07-14 12:00:45", "sensor", "PIR", "motion", 0),
            ("2026-07-14 12:00:05", "sensor", "Smart Meter", "power", 150),
            ("2026-07-14 12:00:45", "sensor", "Smart Meter", "power", 0),
            ("2026-07-14 12:01:00", "sensor", "Smart Meter", "power", 0),
            ("2026-07-14 12:00:55", "device", "PIR", "motion", 1),
        ])
        original = ctx.automatic_state["interactions_df"].copy(deep=True)
        series, selected = automatic.build_sensor_states_from_interactions(ctx, (0, 1))
        self.assertEqual(selected, ["PIR:motion", "Smart Meter:power"])
        self.assertEqual(series["PIR:motion"]["state"], [0, 0])
        self.assertEqual(series["Smart Meter:power"]["state"], [150, 0])
        self.assertEqual(series["PIR:motion"]["time"], ["2026-07-14 12:00:00", "2026-07-14 12:01:00"])
        pd.testing.assert_frame_equal(ctx.automatic_state["interactions_df"], original)

    def test_bad_dates_non_numeric_and_infinite_values_are_skipped(self):
        ctx = replay_context([
            ("broken", "sensor", "PIR", "motion", 1),
            ("2026-07-14 12:00", "sensor", "PIR", "motion", "bad"),
            ("2026-07-14 12:01", "sensor", "PIR", "motion", float("inf")),
            ("2026-07-14 12:02", "sensor", "PIR", "motion", 0),
        ])
        series, selected = automatic.build_sensor_states_from_interactions(ctx, (0, 1))
        self.assertEqual(selected, ["PIR:motion"])
        self.assertEqual(series["PIR:motion"]["state"], [0])
        self.assertEqual(series["PIR:motion"]["time"], ["2026-07-14 12:02:00"])

    def test_equal_timestamp_edges_use_file_order(self):
        ctx = replay_context([("2026-07-14 12:00", "sensor", "PIR", "motion", value) for value in (0, 1, 0)])
        series, _ = automatic.build_sensor_states_from_interactions(ctx, (0,))
        self.assertEqual(series["PIR:motion"]["state"], [0])

    def test_export_fills_minute_gaps_and_can_be_loaded_in_folder_mode(self):
        ctx = replay_context([
            ("2026-07-14 12:00:30", "sensor", "PIR", "motion", 1),
            ("2026-07-14 12:02:45", "sensor", "PIR", "motion", 0),
        ])
        ctx.automatic_state["sensors_listbox"].curselection.return_value = (0,)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "replay.csv"
            with patch.object(automatic, "ask_save_file", return_value=str(path)), patch.object(automatic, "messagebox") as messages:
                automatic.export_logs_from_csv_ctx(ctx)
            messages.showerror.assert_not_called()
            times, values = automatic.read_timestamp_state_file(str(path))
            self.assertEqual(times, [f"2026-07-14 12:0{minute}:00" for minute in range(3)])
            self.assertEqual(values, [1, 1, 0])
            self.assertEqual(ctx.automatic_state["SAVE_LAST_DIR"], directory)

    def test_export_all_invalid_samples_skips_dialog_and_reports_zero_files(self):
        ctx = replay_context([("invalid", "sensor", "PIR", "motion", "invalid")])
        with patch.object(automatic, "ask_save_file") as save, patch.object(automatic, "messagebox") as messages:
            automatic.export_logs_from_csv_ctx(ctx)
        save.assert_not_called()
        messages.showinfo.assert_called_once_with("Export", "Exported 0 files.")

    def test_cancelled_export_does_not_write_or_change_directory(self):
        ctx = replay_context([("2026-07-14 12:00", "sensor", "PIR", "motion", 0)])
        with patch.object(automatic, "ask_save_file", return_value=""), patch.object(automatic, "messagebox"):
            automatic.export_logs_from_csv_ctx(ctx)
        self.assertNotIn("SAVE_LAST_DIR", ctx.automatic_state)

    def test_invalid_log_does_not_replace_existing_replay_selection(self):
        ctx = replay_context([])
        old = ctx.automatic_state.copy()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.csv"
            path.write_text("wrong,columns\n1,2\n", encoding="utf-8")
            with patch.object(automatic, "ask_open_file", return_value=str(path)), patch.object(automatic, "messagebox") as messages:
                automatic.select_path_csv(ctx)
        self.assertIs(ctx.automatic_state["interactions_df"], old["interactions_df"])
        messages.showerror.assert_called_once()

    def test_csv_load_accepts_mixed_timestamp_precision_before_building_series(self):
        ctx = replay_context([])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mixed.csv"
            path.write_text(
                "timestamp_sim,event_type,subject,name,x,y,value,extra\n"
                "2026-07-14 12:00,sensor,PIR,motion,0,0,0,\n"
                "2026-07-14 12:00:30,sensor,PIR,motion,0,0,1,\n",
                encoding="utf-8",
            )
            with patch.object(automatic, "ask_open_file", return_value=str(path)), patch.object(automatic, "messagebox") as messages:
                automatic.select_path_csv(ctx)
            messages.showerror.assert_not_called()
        series, selected = automatic.build_sensor_states_from_interactions(ctx, (0,))
        self.assertEqual(selected, ["PIR:motion"])
        self.assertEqual(series["PIR:motion"], {"time": ["2026-07-14 12:00:00"], "state": [1.0]})

    def test_graph_generation_reports_empty_selection_without_drawing(self):
        ctx = replay_context([])
        ctx.automatic_state["sensors_listbox"].curselection.return_value = ()
        with patch.object(automatic, "messagebox") as messages, patch.object(automatic, "show_graphs_auto") as draw:
            automatic.generate_graphs_from_csv_ctx(ctx)
        messages.showerror.assert_called_once()
        draw.assert_not_called()


class GraphFrameTests(unittest.TestCase):
    def test_graph_minutes_keep_latest_sample_and_fill_only_internal_gaps(self):
        frame = graph._build_dataframe(
            ["2026-07-14 12:02:20", "2026-07-14 12:00:05", "2026-07-14 12:00:45"],
            [0, 2, 3],
        )
        self.assertEqual(frame["value"].tolist(), [3, 3, 0])
        self.assertEqual(frame.index.tolist(), list(pd.date_range("2026-07-14 12:00", periods=3, freq="1min")))

    def test_graph_discards_invalid_values_and_preserves_zero(self):
        frame = graph._build_dataframe(
            [f"12:0{minute}" for minute in range(5)], ["bad", float("inf"), float("-inf"), None, 0],
        )
        self.assertEqual(frame["value"].tolist(), [0])

    def test_graph_empty_samples_produce_an_empty_frame(self):
        self.assertTrue(graph._build_dataframe([], []).empty)


if __name__ == "__main__":
    unittest.main()

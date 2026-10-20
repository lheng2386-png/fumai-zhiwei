import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from compare_reunion_context import prepare_fold, run, select_context, trailing_context


class ReunionContextTests(unittest.TestCase):
    def test_suffix_keeps_same_endpoint_and_labels_without_reversing_time(self):
        values = np.arange(2 * 14 * 30, dtype=np.float32).reshape(2, 14, 30)
        source = {"x": values, "y": np.asarray([0, 1]),
                  "end_time_ns": np.asarray([100, 200], dtype=np.int64)}
        for length in (5, 15, 30):
            result = trailing_context(source, length)
            np.testing.assert_array_equal(result["x"], values[:, :, 30 - length:])
            np.testing.assert_array_equal(result["y"], source["y"])
            np.testing.assert_array_equal(result["end_time_ns"], source["end_time_ns"])
            np.testing.assert_array_equal(result["x"][:, :, -1], values[:, :, -1])
            self.assertTrue(result["x"].flags.c_contiguous)
        for length in (0, 1, 31):
            with self.assertRaises(ValueError):
                trailing_context(source, length)

    def test_fold_reads_only_training_and_scaling_does_not_use_later_extremes(self):
        with tempfile.TemporaryDirectory() as directory:
            prepared = Path(directory)
            days = ["2022-02-08", "2022-02-11", "2022-02-14"]
            timestamps = np.asarray([
                pd.Timestamp(f"{day} 10:0{index}", tz="Indian/Reunion").value
                for day in days for index in range(2)], dtype=np.int64)
            values = np.asarray([2, 4, 100, 110, 1000, 1100], dtype=np.float32)
            raw = np.broadcast_to(values[:, None, None], (6, 14, 30)).copy()
            np.savez(prepared / "train.npz", x=raw, y=np.asarray([0, 1] * 3),
                     end_time_ns=timestamps)
            np.savez(prepared / "normalization.npz",
                     mean=np.zeros(14, dtype=np.float32),
                     scale=np.ones(14, dtype=np.float32))
            # These files must never be consulted, even for context selection.
            for forbidden in ("validation", "test", "normal_stress"):
                (prepared / f"{forbidden}.npz").write_bytes(b"not an archive")
            load = np.load
            opened = []
            def guarded_load(path, *args, **kwargs):
                opened.append(Path(path).name)
                self.assertIn(Path(path).name, {"train.npz", "normalization.npz"})
                return load(path, *args, **kwargs)
            with patch("numpy.load", side_effect=guarded_load):
                datasets, scaler, dates = prepare_fold(
                    prepared, ("2022-02-08", "2022-02-11", "2022-02-11",
                               "2022-02-14", "2022-02-14"), [5, 15, 30])
            self.assertEqual(set(opened), {"train.npz", "normalization.npz"})
            np.testing.assert_array_equal(scaler["mean"], np.full(14, 3))
            np.testing.assert_array_equal(scaler["scale"], np.ones(14))
            for length, splits in datasets.items():
                for baseline, current in zip(datasets[30], splits):
                    np.testing.assert_array_equal(current["x"], baseline["x"][:, :, -length:])
                    np.testing.assert_array_equal(current["end_time_ns"], baseline["end_time_ns"])
                self.assertGreater(float(splits[2]["x"].min()), 900)
            groups = [set(dates[key]) for key in
                      ("train_days", "tuning_days", "evaluation_days")]
            self.assertFalse(groups[0] & groups[1] or groups[0] & groups[2] or groups[1] & groups[2])

    def test_context_selection_uses_all_inner_seeds_and_keeps_baseline_on_tie(self):
        self.assertEqual(select_context({5: [1.0, 0.1], 15: [0.8, 0.8],
                                         30: [0.7, 0.7]}), 15)
        self.assertEqual(select_context({5: [0.8, 0.8], 30: [0.8, 0.8]}), 30)

    def test_run_freezes_inner_selection_before_any_outer_inference(self):
        # Exercise orchestration only; no model is trained or sent to a GPU.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "manifest.json").write_text(json.dumps({
                "window": 30, "features": [f"feature_{i}" for i in range(14)]}))
            data = {"x": np.zeros((2, 14, 30), dtype=np.float32),
                    "y": np.asarray([0, 1]),
                    "end_time_ns": np.asarray([0, 300 * 10**9], dtype=np.int64)}
            datasets = {length: tuple(trailing_context(data, length) for _ in range(3))
                        for length in (5, 15, 30)}
            args = argparse.Namespace(
                prepared=root, output=root / "result", contexts=[5, 15, 30],
                seeds=[2031, 2032, 2033], epochs=1, patience=1,
                batch_size=2, lr=8e-4, device="cpu")
            trained = []
            def fake_train(name, seed, training, tuning, output, device, controls):
                trained.append((training["x"].shape[-1], seed))
                tag = f"{name}_seed{seed}"
                score = np.asarray([0.1, 0.9], dtype=np.float32)
                np.save(output / f"{tag}_validation.npy", score)
                torch.save({"state_dict": {}}, output / f"{tag}.pt")
                return {"tag": tag, "validation_ap": 1.0,
                        "best_epoch": 1, "epochs_run": 1,
                        "elapsed_seconds": 0.001, "parameters": 0, "history": []}
            def fake_predict(model, x, device, batch_size):
                self.assertEqual(len(trained), 9)
                selection = json.loads(
                    (args.output / "fold" / "inner_selection.json").read_text())
                self.assertEqual(selection["selected_context_minutes"], 30)
                self.assertEqual(selection["outer_status"], "not scored")
                return np.asarray([0.1, 0.9], dtype=np.float32)
            with (patch("compare_reunion_context.FOLDS", [("fold",)]),
                  patch("compare_reunion_context.prepare_fold",
                        return_value=(datasets, {"mean": np.zeros(14), "scale": np.ones(14)}, {})),
                  patch("compare_reunion_context.train_one", side_effect=fake_train),
                  patch("compare_reunion_context.make_model"),
                  patch("compare_reunion_context.predict", side_effect=fake_predict),
                  patch("builtins.print")):
                result = run(args)
            self.assertEqual(result["status"], "complete")
            self.assertEqual(set(trained), {(length, seed) for length in (5, 15, 30)
                                           for seed in args.seeds})
            self.assertEqual(result["folds"]["fold"]["selected_context_minutes"], 30)


if __name__ == "__main__":
    unittest.main()

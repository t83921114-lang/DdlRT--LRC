import argparse
import importlib.util
from pathlib import Path
import tempfile
import unittest

MODULE_PATH = Path(__file__).resolve().parents[1] / "run_baseline.py"
SPEC = importlib.util.spec_from_file_location("run_baseline", MODULE_PATH)
baseline = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(baseline)


class BaselineMatrixTests(unittest.TestCase):
    def test_default_plan_has_both_tests_algorithms_and_four_encodings(self):
        runs = baseline.build_runs(baseline.load_manifest(), repetitions=5)
        self.assertEqual(80, len(runs))
        self.assertEqual({"normal-rw", "recovery"}, {run["test"] for run in runs})
        self.assertEqual({"SRS", "ERS"}, {run["algorithm"] for run in runs})
        self.assertTrue(all(run["stripes"] == 1 for run in runs))
        self.assertTrue(all(run["block_size_bytes"] == 1048576 for run in runs))
        self.assertTrue(all((run["intra_gbps"], run["inter_gbps"]) == (10, 1)
                            for run in runs))

    def test_test_filter_and_failed_block(self):
        runs = baseline.build_runs(baseline.load_manifest(), ["recovery"], 1, 0,
                                   ["SRS"])
        self.assertEqual(4, len(runs))
        self.assertTrue(all(run["failed_block_id"] == 0 for run in runs))
        self.assertTrue(all(run["test"] == "recovery" for run in runs))

    def test_test_type_is_part_of_run_identity(self):
        normal = baseline.build_runs(baseline.load_manifest(), ["normal-rw"], 1,
                                     algorithms=["SRS"])[0]
        recovery = baseline.build_runs(baseline.load_manifest(), ["recovery"], 1,
                                       algorithms=["SRS"])[0]
        self.assertNotEqual(normal["run_id"], recovery["run_id"])

    def test_all_data_blocks_plan_covers_each_algorithm_and_block(self):
        runs = baseline.build_runs(
            baseline.load_manifest(), ["recovery"], repetitions=1,
            algorithms=["SRS", "ERS"], all_data_blocks=True)
        self.assertEqual(72, len(runs))
        for algorithm in ("SRS", "ERS"):
            for encoding in baseline.load_manifest()["encoding_parameters"]:
                block_ids = {run["failed_block_id"] for run in runs
                             if run["algorithm"] == algorithm and
                             run["encoding"] == encoding["name"]}
                self.assertEqual(set(range(encoding["k"])), block_ids)


class BaselineIdentityTests(unittest.TestCase):
    def test_algorithm_is_part_of_run_identity(self):
        srs = baseline.build_runs(baseline.load_manifest(), ["normal-rw"], 1,
                                  algorithms=["SRS"])[0]
        ers = baseline.build_runs(baseline.load_manifest(), ["normal-rw"], 1,
                                  algorithms=["ERS"])[0]
        self.assertNotEqual(srs["run_id"], ers["run_id"])


class BaselineParsingTests(unittest.TestCase):
    def test_parse_normal_rw_metrics(self):
        parsed = baseline.parse_client_log(
            "write time: 2 seconds\nwrite throughput: 3.5 MiB/s\n"
            "read time: 1 seconds\nread throughput: 7 MiB/s\n")
        self.assertEqual(3.5, parsed["write_throughput_mib_s"])
        self.assertEqual(7.0, parsed["read_throughput_mib_s"])
        self.assertIsNone(parsed["recovery_time_seconds"])

    def test_parse_recovery_metrics(self):
        parsed = baseline.parse_client_log(
            "write time: 2 seconds\nwrite throughput: 3 MiB/s\n"
            "recovery time: 0.25 seconds\nrecovery throughput: 4 MiB/s\n")
        self.assertEqual(0.25, parsed["recovery_time_seconds"])
        self.assertEqual(4.0, parsed["recovery_throughput_mib_s"])


class BaselineSummaryTests(unittest.TestCase):
    def test_summary_separates_algorithms(self):
        with tempfile.TemporaryDirectory() as directory:
            results = Path(directory) / "results.csv"
            summary = Path(directory) / "recovery_summary.csv"
            rows = []
            for algorithm, recovery_time in (("SRS", 0.02), ("ERS", 0.04)):
                row = {field: "" for field in baseline.CSV_FIELDS}
                row.update({
                    "run_id": algorithm, "status": "success",
                    "test": "recovery", "algorithm": algorithm,
                    "encoding": "sample", "k": 1, "l": 1, "g": 1,
                    "failed_block_id": 0, "repetition": 1,
                    "recovery_time_seconds": recovery_time,
                    "recovery_throughput_mib_s": 1.0 / recovery_time,
                })
                rows.append(row)
            with results.open("w", newline="", encoding="utf-8") as handle:
                writer = baseline.csv.DictWriter(handle, fieldnames=baseline.CSV_FIELDS)
                writer.writeheader()
                writer.writerows(rows)
            baseline.write_recovery_summary(results, summary)
            with summary.open(newline="", encoding="utf-8") as handle:
                records = list(baseline.csv.DictReader(handle))
            self.assertEqual({"SRS", "ERS"}, {row["algorithm"] for row in records})
            self.assertEqual({"1"}, {row["covered_data_blocks"] for row in records})


class BaselineXmlTests(unittest.TestCase):
    SAMPLE = b'''<?xml version="1.0" encoding="UTF-8"?>
<Configuration><BlockSize>4</BlockSize><CodeType>RS</CodeType><k>2</k><r>1</r><z>0</z><Other>x</Other></Configuration>
'''

    def test_render_enforces_initial_srs_ers_configuration(self):
        run = baseline.build_runs(baseline.load_manifest(), ["normal-rw"], 1,
                                  algorithms=["ERS"])[0]
        root = baseline.ET.fromstring(baseline.render_xml(self.SAMPLE, run))
        self.assertEqual("ERS", root.findtext("CodeType"))
        self.assertEqual("1048576", root.findtext("BlockSize"))
        self.assertEqual(str(run["k"]), root.findtext("k"))
        self.assertEqual(str(run["g"]), root.findtext("r"))
        self.assertEqual(str(run["l"]), root.findtext("z"))
        self.assertEqual("x", root.findtext("Other"))

    def test_atomic_restore_preserves_exact_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.xml"
            path.write_bytes(self.SAMPLE)
            original = path.read_bytes()
            run = baseline.build_runs(baseline.load_manifest(), ["recovery"], 1,
                                      algorithms=["SRS"])[0]
            baseline.atomic_write(path, baseline.render_xml(original, run))
            baseline.atomic_write(path, original)
            self.assertEqual(self.SAMPLE, path.read_bytes())


if __name__ == "__main__":
    unittest.main()

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
    def test_default_plan_has_both_tests_and_four_encodings(self):
        runs = baseline.build_runs(baseline.load_manifest(), repetitions=5)
        self.assertEqual(40, len(runs))
        self.assertEqual({"normal-rw", "recovery"}, {run["test"] for run in runs})
        self.assertTrue(all(run["stripes"] == 1 for run in runs))
        self.assertTrue(all(run["block_size_bytes"] == 1048576 for run in runs))
        self.assertTrue(all((run["intra_gbps"], run["inter_gbps"]) == (10, 1)
                            for run in runs))

    def test_test_filter_and_failed_block(self):
        runs = baseline.build_runs(baseline.load_manifest(), ["recovery"], 1, 0)
        self.assertEqual(4, len(runs))
        self.assertTrue(all(run["failed_block_id"] == 0 for run in runs))
        self.assertTrue(all(run["test"] == "recovery" for run in runs))

    def test_test_type_is_part_of_run_identity(self):
        normal = baseline.build_runs(baseline.load_manifest(), ["normal-rw"], 1)[0]
        recovery = baseline.build_runs(baseline.load_manifest(), ["recovery"], 1)[0]
        self.assertNotEqual(normal["run_id"], recovery["run_id"])


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


class BaselineXmlTests(unittest.TestCase):
    SAMPLE = b'''<?xml version="1.0" encoding="UTF-8"?>
<Configuration><BlockSize>4</BlockSize><CodeType>RS</CodeType><k>2</k><r>1</r><z>0</z><Other>x</Other></Configuration>
'''

    def test_render_enforces_initial_ddlrt_configuration(self):
        run = baseline.build_runs(baseline.load_manifest(), ["normal-rw"], 1)[0]
        root = baseline.ET.fromstring(baseline.render_xml(self.SAMPLE, run))
        self.assertEqual("DdlRT_LRC", root.findtext("CodeType"))
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
            run = baseline.build_runs(baseline.load_manifest(), ["recovery"], 1)[0]
            baseline.atomic_write(path, baseline.render_xml(original, run))
            baseline.atomic_write(path, original)
            self.assertEqual(self.SAMPLE, path.read_bytes())


if __name__ == "__main__":
    unittest.main()

import argparse
import importlib.util
import os
import subprocess
from pathlib import Path
import tempfile
import unittest

MODULE_PATH = Path(__file__).resolve().parents[1] / "run_overnight.py"
SPEC = importlib.util.spec_from_file_location("run_overnight", MODULE_PATH)
runner = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(runner)


class MatrixTests(unittest.TestCase):
    def test_full_matrix_and_runs(self):
        manifest = runner.load_manifest()
        configs = runner.build_configurations(manifest)
        runs = runner.build_runs(configs, 5)
        self.assertEqual(96, len(configs))
        self.assertEqual(96, len({item["config_id"] for item in configs}))
        self.assertEqual(480, len(runs))
        self.assertEqual({"SRS", "ERS"}, {item["code_type"] for item in configs})
        baseline = [item for item in configs if item["encoding"] == "k6_l2_g2"
                    and item["code_type"] == "SRS"
                    and item["block_size_bytes"] == 1048576
                    and item["stripes"] == 1000 and item["inter_gbps"] == 1]
        self.assertEqual(1, len(baseline))
        self.assertEqual(["bandwidth", "block_size", "encoding", "stripes"],
                         baseline[0]["experiment_labels"])

    def test_algorithm_filter_and_stripe_override(self):
        manifest = runner.load_manifest()
        configs = runner.build_configurations(manifest, {"bandwidth"}, 7, ["ERS"])
        self.assertEqual(20, len(configs))
        self.assertTrue(all(item["stripes"] == 7 for item in configs))
        self.assertTrue(all(item["code_type"] == "ERS" for item in configs))
        collapsed = runner.build_configurations(manifest, {"stripes"}, 7, ["SRS"])
        self.assertEqual(4, len(collapsed))

    def test_algorithm_is_part_of_configuration_identity(self):
        config = {
            "code_type": "SRS", "encoding": "k6_l2_g2",
            "k": 6, "l": 2, "g": 2, "block_size_bytes": 1048576,
            "stripes": 1000, "intra_gbps": 10, "inter_gbps": 1,
        }
        srs_key = runner._canonical_key(config)
        config["code_type"] = "ERS"
        self.assertNotEqual(srs_key, runner._canonical_key(config))

    def test_default_output_is_ignored_results_directory(self):
        self.assertEqual(runner.REPO_ROOT / "experiments/results",
                         runner.DEFAULT_OUTPUT)


class LogParsingTests(unittest.TestCase):
    def test_parse_two_merge_rounds(self):
        text = """write time: 12.5 seconds
write throughput: 320.25 MiB/s
start[ 1 time]merge now? (Y/N)
[Client] will merge 4 pairs (round 1, concurrency 4)
[Client] merge succeeded -> new stripe 0 (0 + 1)
[Client] merge succeeded -> new stripe 2 (2 + 3)
[Client] merge succeeded -> new stripe 4 (4 + 5)
[Client] merge succeeded -> new stripe 6 (6 + 7)
[merge1time] end-to-end time: 8.1 s
 estimated parallel critical path: 7.2 s
 coordinator data migration sum: 4.3 s
 coordinator parity update sum: 5.4 s
start[ 2 time]merge now? (Y/N)
[Client] will merge 2 pairs (round 2, concurrency 2)
[Client] merge succeeded -> new stripe 0 (0 + 2)
[Client] merge succeeded -> new stripe 4 (4 + 6)
[merge2time] end-to-end time: 6.1 s
 estimated parallel critical path: 5.2 s
 coordinator data migration sum: 3.3 s
 coordinator parity update sum: 4.4 s
"""
        parsed = runner.parse_client_log(text)
        self.assertEqual(12.5, parsed["write_time_seconds"])
        self.assertEqual(320.25, parsed["write_throughput_mib_s"])
        self.assertEqual(2, len(parsed["rounds"]))
        self.assertEqual((4, 4), (parsed["rounds"][0]["pairs_completed"],
                                  parsed["rounds"][0]["pairs_total"]))
        self.assertEqual(7.2, parsed["rounds"][0]["critical_path_seconds"])
        self.assertEqual(6.1, parsed["rounds"][1]["end_to_end_seconds"])


class ClientInteractionTests(unittest.TestCase):
    def test_client_failure_output_terminates_waiting_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = root / "failing_client.py"
            client.write_text(r"""#!/usr/bin/env python3
import sys
import time

sys.stdout.write("[SET402] upload data fai")
sys.stdout.flush()
time.sleep(0.05)
sys.stdout.write("led! gRPC code=14 message=proxy failed\n")
sys.stdout.flush()
time.sleep(30)
""")
            client.chmod(0o755)
            args = argparse.Namespace(
                output=root / "output", client=client, coordinator="test:55555",
                client_timeout=5.0, command_timeout=5.0,
            )
            instance = runner.Runner(args, [])
            started = runner.time.monotonic()
            with self.assertRaisesRegex(runner.ClientOutputError, "upload data failed"):
                instance._run_client(
                    {"stripes": 1000, "inter_gbps": 1}, root / "client.log")
            self.assertLess(runner.time.monotonic() - started, 2.0)
            self.assertIsNone(instance.current_process)

    def test_multiline_output_prompt_is_answered_before_process_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = root / "fake_client.py"
            client.write_text(r"""#!/usr/bin/env python3
import sys

sys.stdout.write("write time: 1 seconds\nwrite throughput: 2 MiB/s\n"
                 "start[ 1 time]merge now? (Y/N)\n")
sys.stdout.flush()
if sys.stdin.readline().strip() != "Y":
    raise SystemExit(11)
sys.stdout.write("round one complete\nstart[ 2 time]merge now? (Y/N)\n")
sys.stdout.flush()
if sys.stdin.readline().strip() != "Y":
    raise SystemExit(12)
sys.stdout.write("round two complete\nstart[ 3 time]merge now? (Y/N)\n")
sys.stdout.flush()
if sys.stdin.readline().strip() != "N":
    raise SystemExit(13)
""")
            client.chmod(0o755)
            args = argparse.Namespace(
                output=root / "output", client=client, coordinator="test:55555",
                client_timeout=5.0, command_timeout=5.0,
            )
            instance = runner.Runner(args, [])
            bandwidth_calls = []

            def fake_command(command, timeout, check=True):
                bandwidth_calls.append(command)
                return subprocess.CompletedProcess(command, 0, stdout="limited\n")

            instance._command = fake_command
            return_code, transcript = instance._run_client(
                {"stripes": 1000, "inter_gbps": 1}, root / "client.log")
            self.assertEqual(0, return_code)
            self.assertIn("round one complete", transcript)
            self.assertIn("round two complete", transcript)
            self.assertEqual(1, len(bandwidth_calls))
            log = (root / "client.log").read_text()
            self.assertIn("sent Y for merge round 1", log)
            self.assertIn("sent Y for merge round 2", log)


class XmlTests(unittest.TestCase):
    SAMPLE = b'''<?xml version="1.0" encoding="UTF-8"?>
<Configuration><BlockSize>1</BlockSize><k>2</k><r>3</r><z>4</z><CodeType>SRS</CodeType><BaselineSeed>7</BaselineSeed><Other>x</Other></Configuration>
'''

    def test_render_xml_maps_k_l_g_and_code_type(self):
        rendered = runner.render_xml(self.SAMPLE, 1048576, 10, 3, 2, "ERS")
        root = runner.ET.fromstring(rendered)
        self.assertEqual("1048576", root.findtext("BlockSize"))
        self.assertEqual("10", root.findtext("k"))
        self.assertEqual("3", root.findtext("r"))
        self.assertEqual("2", root.findtext("z"))
        self.assertEqual("ERS", root.findtext("CodeType"))
        self.assertEqual("7", root.findtext("BaselineSeed"))
        self.assertEqual("x", root.findtext("Other"))

    def test_update_and_exact_restore_in_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.xml"
            path.write_bytes(self.SAMPLE)
            original = runner.update_xml_file(path, 8, 6, 2, 2, "ERS")
            self.assertNotEqual(original, path.read_bytes())
            runner.atomic_write(path, original)
            self.assertEqual(self.SAMPLE, path.read_bytes())

    def test_rejects_missing_required_node(self):
        with self.assertRaises(ValueError):
            runner.render_xml(b"<Configuration><k>1</k></Configuration>", 1, 1, 1, 1)


if __name__ == "__main__":
    unittest.main()

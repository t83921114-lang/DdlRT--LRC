#!/usr/bin/env python3
"""Run DdlRT-LRC initial-layout normal I/O and single-block recovery tests."""

from __future__ import annotations

import argparse
import csv
import codecs
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = Path(__file__).with_name("manifest.json")
DEFAULT_XML = REPO_ROOT / "project/config/parameterConfiguration.xml"
DEFAULT_OUTPUT = Path(__file__).with_name("results") / "baseline"
TESTS = ("normal-rw", "recovery")
CSV_FIELDS = [
    "run_id", "status", "failure_reason", "attempts", "test", "encoding",
    "k", "l", "g", "block_size_bytes", "stripes", "intra_gbps",
    "inter_gbps", "failed_block_id", "repetition", "write_time_seconds",
    "write_throughput_mib_s", "read_time_seconds", "read_throughput_mib_s",
    "recovery_time_seconds", "recovery_throughput_mib_s", "started_at",
    "finished_at", "log_path",
]
ROUND_SUMMARY_FIELDS = [
    "encoding", "k", "l", "g", "repetition", "expected_data_blocks",
    "successful_blocks", "failed_blocks", "covered_data_blocks",
    "mean_recovery_time_seconds", "median_recovery_time_seconds",
    "min_recovery_time_seconds", "max_recovery_time_seconds",
    "throughput_from_mean_time_mib_s", "mean_block_throughput_mib_s",
]
OVERALL_SUMMARY_FIELDS = [
    "encoding", "k", "l", "g", "completed_repetitions",
    "expected_repetitions", "mean_round_recovery_time_seconds",
    "median_round_recovery_time_seconds", "min_round_recovery_time_seconds",
    "max_round_recovery_time_seconds", "throughput_from_mean_round_time_mib_s",
]
FLOAT = r"([0-9]+(?:\.[0-9]+)?)"
FAILURE_RE = re.compile(
    r"(?:upload data failed|\bset failed|normal read failed|"
    r"get blocks failed|single-block recovery failed|recovery failed)", re.I)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def load_manifest(path: Path = DEFAULT_MANIFEST) -> Dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def build_runs(manifest: Dict[str, Any], tests: Optional[Sequence[str]] = None,
               repetitions: int = 5, failed_block_id: int = 0,
               all_data_blocks: bool = False) -> List[Dict[str, Any]]:
    if repetitions <= 0:
        raise ValueError("repetitions must be positive")
    selected = list(tests) if tests else list(TESTS)
    unknown = set(selected) - set(TESTS)
    if unknown:
        raise ValueError("unknown test(s): " + ", ".join(sorted(unknown)))
    runs: List[Dict[str, Any]] = []
    for test in selected:
        for encoding in manifest["encoding_parameters"]:
            failed_blocks = (list(range(encoding["k"])) if
                             test == "recovery" and all_data_blocks else
                             [failed_block_id if test == "recovery" else None])
            # One repetition of an all-data-block run is one complete D0..D(k-1)
            # scan. Keep repetition outside the block loop so execution and
            # summaries reflect complete experiment rounds.
            for repetition in range(1, repetitions + 1):
                for block_id in failed_blocks:
                    identity = json.dumps(
                        ("DdlRT_LRC", test, encoding["name"], encoding["k"],
                         encoding["l"], encoding["g"], 1048576, 1, 10, 1,
                         block_id),
                        separators=(",", ":"),
                    )
                    config_id = hashlib.sha256(identity.encode()).hexdigest()[:16]
                    runs.append({
                        "run_id": "%s-r%02d" % (config_id, repetition),
                        "test": test,
                        "encoding": encoding["name"],
                        "k": encoding["k"], "l": encoding["l"], "g": encoding["g"],
                        "block_size_bytes": 1048576, "stripes": 1,
                        "intra_gbps": 10, "inter_gbps": 1,
                        "failed_block_id": block_id,
                        "repetition": repetition,
                    })
    return runs


def render_xml(source: bytes, run: Dict[str, Any]) -> bytes:
    root = ET.fromstring(source)
    replacements = {
        "BlockSize": run["block_size_bytes"], "k": run["k"],
        "r": run["g"], "z": run["l"], "CodeType": "DdlRT_LRC",
    }
    for tag, value in replacements.items():
        nodes = root.findall(tag)
        if len(nodes) != 1:
            raise ValueError("expected exactly one <%s>, found %d" % (tag, len(nodes)))
        nodes[0].text = str(value)
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(
        root, encoding="utf-8") + b"\n"


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode if path.exists() else 0o644
    with tempfile.NamedTemporaryFile(dir=str(path.parent), prefix=path.name + ".",
                                     delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, mode)
    os.replace(str(temporary), str(path))


def parse_client_log(text: str) -> Dict[str, Optional[float]]:
    patterns = {
        "write_time_seconds": r"write time:\s*" + FLOAT,
        "write_throughput_mib_s": r"write throughput:\s*" + FLOAT,
        "read_time_seconds": r"read time:\s*" + FLOAT,
        "read_throughput_mib_s": r"read throughput:\s*" + FLOAT,
        "recovery_time_seconds": r"recovery time:\s*" + FLOAT,
        "recovery_throughput_mib_s": r"recovery throughput:\s*" + FLOAT,
    }
    result: Dict[str, Optional[float]] = {}
    for key, pattern in patterns.items():
        match = re.search(pattern, text, re.I)
        result[key] = float(match.group(1)) if match else None
    return result


def atomic_json(path: Path, value: Any) -> None:
    atomic_write(path, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode())


def append_results(jsonl_path: Path, csv_path: Path, record: Dict[str, Any]) -> None:
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    with jsonl_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    exists = csv_path.exists() and csv_path.stat().st_size > 0
    with csv_path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        writer.writerow(record)
        handle.flush()
        os.fsync(handle.fileno())


def write_recovery_summaries(results_csv: Path, round_summary_csv: Path,
                                overall_summary_csv: Path,
                                expected_repetitions: int) -> None:
    import statistics

    groups: Dict[tuple[str, int, int, int, int], List[Dict[str, str]]] = {}
    if results_csv.exists() and results_csv.stat().st_size:
        with results_csv.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                if row.get("test") != "recovery":
                    continue
                key = (row["encoding"], int(row["k"]), int(row["l"]),
                       int(row["g"]), int(row["repetition"]))
                groups.setdefault(key, []).append(row)

    round_records: List[Dict[str, Any]] = []
    completed_round_times: Dict[tuple[str, int, int, int], List[float]] = {}
    for (encoding, k, l, g, repetition), rows in sorted(groups.items()):
        successful = [row for row in rows if row.get("status") == "success" and
                      row.get("recovery_time_seconds")]
        times = [float(row["recovery_time_seconds"]) for row in successful]
        throughputs = [float(row["recovery_throughput_mib_s"])
                       for row in successful if row.get("recovery_throughput_mib_s")]
        covered = len({int(row["failed_block_id"]) for row in successful})
        mean_time = statistics.fmean(times) if times else None
        complete = covered == k and len(successful) == k
        if complete and mean_time is not None:
            completed_round_times.setdefault((encoding, k, l, g), []).append(mean_time)
        round_records.append({
            "encoding": encoding, "k": k, "l": l, "g": g,
            "repetition": repetition, "expected_data_blocks": k,
            "successful_blocks": len(successful),
            "failed_blocks": len(rows) - len(successful),
            "covered_data_blocks": covered,
            "mean_recovery_time_seconds": mean_time,
            "median_recovery_time_seconds": statistics.median(times) if times else None,
            "min_recovery_time_seconds": min(times) if times else None,
            "max_recovery_time_seconds": max(times) if times else None,
            "throughput_from_mean_time_mib_s": (1.0 / mean_time if mean_time else None),
            "mean_block_throughput_mib_s": (statistics.fmean(throughputs)
                                               if throughputs else None),
        })

    overall_records: List[Dict[str, Any]] = []
    for (encoding, k, l, g), round_times in sorted(completed_round_times.items()):
        mean_round_time = statistics.fmean(round_times)
        overall_records.append({
            "encoding": encoding, "k": k, "l": l, "g": g,
            "completed_repetitions": len(round_times),
            "expected_repetitions": expected_repetitions,
            "mean_round_recovery_time_seconds": mean_round_time,
            "median_round_recovery_time_seconds": statistics.median(round_times),
            "min_round_recovery_time_seconds": min(round_times),
            "max_round_recovery_time_seconds": max(round_times),
            "throughput_from_mean_round_time_mib_s": 1.0 / mean_round_time,
        })

    def atomic_csv(path: Path, fields: Sequence[str], records: Sequence[Dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", dir=str(path.parent),
                                         prefix=path.name + ".", delete=False,
                                         newline="", encoding="utf-8") as handle:
            temporary = Path(handle.name)
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(records)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temporary), str(path))

    atomic_csv(round_summary_csv, ROUND_SUMMARY_FIELDS, round_records)
    atomic_csv(overall_summary_csv, OVERALL_SUMMARY_FIELDS, overall_records)


class Runner:
    def __init__(self, args: argparse.Namespace, runs: Sequence[Dict[str, Any]]):
        self.args = args
        self.runs = runs
        self.output = args.output.resolve()
        self.logs = self.output / "logs"
        self.state_path = self.output / "state.json"
        self.results_jsonl = self.output / "results.jsonl"
        self.results_csv = self.output / "results.csv"
        self.recovery_summary_csv = self.output / "recovery_summary.csv"
        self.recovery_overall_summary_csv = (
            self.output / "recovery_overall_summary.csv")
        self.state: Dict[str, Any] = {"runs": {}}
        self.original_xml: Optional[bytes] = None
        self.current_process: Optional[subprocess.Popen[str]] = None
        self.stopping = False

    def _command(self, command: Sequence[str], timeout: float,
                 check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(command, cwd=str(REPO_ROOT), text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=timeout, check=check)

    def preflight(self) -> None:
        missing_commands = [name for name in ("bash", "pdsh", "rsync", "ssh", "sudo")
                            if shutil.which(name) is None]
        required = [
            self.args.xml, self.args.client, REPO_ROOT / "hosts", REPO_ROOT / "proxy_hosts",
            *[REPO_ROOT / name for name in (
                "update_all.sh", "kill_all_nodes.sh", "start_proxy.sh",
                "start_coordinator.sh", "limit_bandwidth.sh", "unlimit_all.sh",
                "limit_client_proxy_bandwidth.sh",
                "unlimit_client_proxy_bandwidth.sh")],
        ]
        missing_files = [str(path) for path in required if not path.is_file()]
        if missing_commands or missing_files:
            raise RuntimeError("preflight failed: missing commands=%s; missing files=%s" %
                               (missing_commands, missing_files))
        if not os.access(self.args.client, os.X_OK):
            raise RuntimeError("client is not executable: %s" % self.args.client)
        self._command(["sudo", "-n", "true"], self.args.command_timeout)
        for hosts in (REPO_ROOT / "hosts", REPO_ROOT / "proxy_hosts"):
            self._command(["pdsh", "-S", "-R", "ssh", "-w", "^" + str(hosts),
                           "-l", "root", "-f", "50", "true"], self.args.command_timeout)

    def cleanup_cluster(self) -> None:
        for script in ("unlimit_client_proxy_bandwidth.sh", "unlimit_all.sh",
                       "kill_all_nodes.sh"):
            try:
                self._command(["bash", str(REPO_ROOT / script)],
                              self.args.command_timeout)
            except Exception as error:
                print("cleanup warning (%s): %s" % (script, error), file=sys.stderr)

    def restore(self) -> None:
        if self.current_process and self.current_process.poll() is None:
            self.current_process.terminate()
            try:
                self.current_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.current_process.kill()
        self.cleanup_cluster()
        if self.original_xml is not None:
            atomic_write(self.args.xml, self.original_xml)
            self.original_xml = None
            try:
                self._command(["bash", str(REPO_ROOT / "update_all.sh")],
                              self.args.command_timeout)
            except Exception as error:
                print("warning: restored local XML but sync failed: %s" % error,
                      file=sys.stderr)

    def stop(self, signum: int, _frame: Any) -> None:
        self.stopping = True
        print("received signal %d; stopping after cleanup" % signum, file=sys.stderr)
        if self.current_process and self.current_process.poll() is None:
            self.current_process.terminate()

    def _run_client(self, run: Dict[str, Any], log_path: Path) -> tuple[int, str]:
        command = [str(self.args.client), "--coordinator", self.args.coordinator,
                   "--stripes", "1", "--test-mode", run["test"]]
        if run["test"] == "recovery":
            command.extend(["--failed-block", str(run["failed_block_id"])])
        process = subprocess.Popen(command, cwd=str(REPO_ROOT),
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   bufsize=0)
        self.current_process = process
        output: List[str] = []
        failure_buffer = ""
        started = time.monotonic()
        selector = selectors.DefaultSelector()
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        assert process.stdout is not None
        selector.register(process.stdout, selectors.EVENT_READ)
        with log_path.open("w", encoding="utf-8", buffering=1) as log:
            def consume(chunk: str) -> None:
                nonlocal failure_buffer
                if not chunk:
                    return
                output.append(chunk)
                log.write(chunk)
                sys.stdout.write(chunk)
                sys.stdout.flush()
                failure_buffer += chunk
                failure = FAILURE_RE.search(failure_buffer)
                if failure:
                    line_start = failure_buffer.rfind("\n", 0, failure.start()) + 1
                    line_end = failure_buffer.find("\n", failure.end())
                    if line_end < 0:
                        line_end = len(failure_buffer)
                    raise RuntimeError(failure_buffer[line_start:line_end].strip())
                failure_buffer = failure_buffer[-512:]

            try:
                stdout_fd = process.stdout.fileno()
                reached_eof = False
                while not reached_eof:
                    if self.stopping and process.poll() is None:
                        process.terminate()
                    if time.monotonic() - started > self.args.client_timeout:
                        process.kill()
                        process.wait()
                        raise TimeoutError("client exceeded %.1f seconds" %
                                           self.args.client_timeout)
                    for _, _ in selector.select(timeout=1.0):
                        chunk = os.read(stdout_fd, 65536)
                        if chunk:
                            consume(decoder.decode(chunk))
                        else:
                            reached_eof = True
                            selector.unregister(process.stdout)
                            break
                consume(decoder.decode(b"", final=True))
                return process.wait(), "".join(output)
            finally:
                selector.close()
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                process.stdout.close()
                self.current_process = None

    def _attempt(self, run: Dict[str, Any], attempt: int) -> Dict[str, Any]:
        log_path = self.logs / (run["run_id"] + "-attempt%02d.log" % attempt)
        record = dict(run)
        record.update({"status": "failed", "failure_reason": "", "attempts": attempt,
                       "started_at": utc_now(), "finished_at": None,
                       "log_path": str(log_path.resolve())})
        try:
            self.cleanup_cluster()
            atomic_write(self.args.xml, render_xml(self.original_xml or
                                                   self.args.xml.read_bytes(), run))
            self._command(["bash", str(REPO_ROOT / "update_all.sh")],
                          self.args.command_timeout)
            self._command(["bash", str(REPO_ROOT / "start_proxy.sh")],
                          self.args.command_timeout)
            self._command(["bash", str(REPO_ROOT / "start_coordinator.sh")],
                          self.args.command_timeout)
            time.sleep(self.args.startup_wait)
            # Both experiment 5 and 6 require rack bandwidth 10:1. Only
            # normal read/write additionally shapes the client/proxy path;
            # recovery data stays on the proxy/datanode and proxy/proxy paths.
            self._command(["bash", str(REPO_ROOT / "limit_bandwidth.sh"), "1"],
                          self.args.command_timeout)
            if run["test"] == "normal-rw":
                self._command(
                    ["bash", str(REPO_ROOT / "limit_client_proxy_bandwidth.sh")],
                    self.args.command_timeout)
            return_code, text = self._run_client(run, log_path)
            record.update(parse_client_log(text))
            if return_code != 0:
                raise RuntimeError("client exited with status %d" % return_code)
            required = ["write_time_seconds", "write_throughput_mib_s"]
            required += (["read_time_seconds", "read_throughput_mib_s"]
                         if run["test"] == "normal-rw" else
                         ["recovery_time_seconds", "recovery_throughput_mib_s"])
            missing = [key for key in required if record.get(key) is None]
            if missing:
                raise RuntimeError("missing client metrics: %s" % missing)
            record["status"] = "success"
        except Exception as error:
            record["failure_reason"] = "%s: %s" % (type(error).__name__, error)
        finally:
            record["finished_at"] = utc_now()
            self.cleanup_cluster()
        return record

    def execute(self) -> int:
        if not self.args.skip_preflight:
            self.preflight()
        self.output.mkdir(parents=True, exist_ok=True)
        self.logs.mkdir(parents=True, exist_ok=True)
        if self.state_path.exists() and not self.args.restart:
            self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
        if self.args.restart:
            self.state = {"runs": {}}
        self.original_xml = self.args.xml.read_bytes()
        atomic_write(self.output / "parameterConfiguration.xml.backup", self.original_xml)
        signal.signal(signal.SIGINT, self.stop)
        signal.signal(signal.SIGTERM, self.stop)
        try:
            for index, run in enumerate(self.runs, 1):
                if self.stopping:
                    break
                previous = self.state["runs"].get(run["run_id"], {})
                if self.args.resume and previous.get("status") == "success":
                    print("[%d/%d] skip successful %s" %
                          (index, len(self.runs), run["run_id"]))
                    continue
                print("[%d/%d] run %s test=%s encoding=%s" %
                      (index, len(self.runs), run["run_id"], run["test"],
                       run["encoding"]))
                final: Optional[Dict[str, Any]] = None
                for attempt in range(1, self.args.retries + 2):
                    final = self._attempt(run, attempt)
                    if final["status"] == "success" or self.stopping:
                        break
                    print("attempt failed: " + final["failure_reason"], file=sys.stderr)
                assert final is not None
                append_results(self.results_jsonl, self.results_csv, final)
                write_recovery_summaries(
                    self.results_csv, self.recovery_summary_csv,
                    self.recovery_overall_summary_csv, self.args.repetitions)
                self.state["runs"][run["run_id"]] = {
                    "status": final["status"], "finished_at": final["finished_at"],
                    "failure_reason": final["failure_reason"],
                    "attempts": final["attempts"],
                }
                atomic_json(self.state_path, self.state)
            return 130 if self.stopping else 0
        finally:
            self.restore()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    result.add_argument("--execute", action="store_true")
    result.add_argument("--dry-run", action="store_true")
    result.add_argument("--test", action="append", choices=list(TESTS),
                        help="select experiment 5 (normal-rw) and/or 6 (recovery)")
    result.add_argument(
        "--repetitions", type=int, default=4,
        help="number of complete experiment rounds; with --all-data-blocks, "
             "each round scans D0..D(k-1)")
    result.add_argument("--failed-block", type=int, default=0)
    result.add_argument("--all-data-blocks", action="store_true",
                        help="run recovery independently for every data block D0..D(k-1)")
    result.add_argument("--resume", action="store_true")
    result.add_argument("--restart", action="store_true")
    result.add_argument("--coordinator", default="10.10.1.2:55555")
    result.add_argument("--client", type=Path,
                        default=REPO_ROOT / "project/cmake/build/main_client")
    result.add_argument("--xml", type=Path, default=DEFAULT_XML)
    result.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    result.add_argument("--command-timeout", type=float, default=300.0)
    result.add_argument("--client-timeout", type=float, default=1800.0)
    result.add_argument("--startup-wait", type=float, default=10.0)
    result.add_argument("--retries", type=int, default=1)
    result.add_argument("--skip-preflight", action="store_true")
    return result


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parser().parse_args(argv)
    if args.execute and args.dry_run:
        raise SystemExit("--execute and --dry-run are mutually exclusive")
    if args.resume and args.restart:
        raise SystemExit("--resume and --restart are mutually exclusive")
    if args.repetitions <= 0 or args.failed_block < 0 or args.retries < 0:
        raise SystemExit("repetitions must be positive; failed block/retries non-negative")
    if args.all_data_blocks and args.test and "recovery" not in args.test:
        raise SystemExit("--all-data-blocks requires the recovery test")
    manifest = load_manifest(args.manifest)
    runs = build_runs(manifest, args.test, args.repetitions, args.failed_block,
                      args.all_data_blocks)
    print("planned runs: %d" % len(runs))
    for run in runs:
        print("{run_id} test={test} encoding={encoding} k/l/g={k}/{l}/{g} "
              "block={block_size_bytes} stripes={stripes} intra/inter={intra_gbps}/{inter_gbps} "
              "failed_block={failed_block_id} repetition={repetition}".format(**run))
    if not args.execute:
        print("plan only; pass --execute to operate the cluster")
        return 0
    if not args.client.is_file() or not os.access(args.client, os.X_OK):
        raise SystemExit("client is missing or not executable: %s" % args.client)
    return Runner(args, runs).execute()


if __name__ == "__main__":
    raise SystemExit(main())

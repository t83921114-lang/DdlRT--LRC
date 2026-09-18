#!/usr/bin/env python3
"""Plan and run the SRS/ERS overnight experiment matrix."""

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
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = Path(__file__).with_name("manifest.json")
DEFAULT_XML = REPO_ROOT / "project/config/parameterConfiguration.xml"
DEFAULT_OUTPUT = Path(__file__).with_name("results")
ALGORITHMS = ("SRS", "ERS")
CSV_FIELDS = [
    "run_id", "code_type", "status", "failure_reason", "attempts", "experiment_labels",
    "encoding", "k", "l", "g", "block_size_bytes", "stripes",
    "intra_gbps", "inter_gbps", "repetition", "write_time_seconds",
    "write_throughput_mib_s", "round", "pairs_completed", "pairs_total",
    "migration_sum_seconds", "parity_sum_seconds", "critical_path_seconds",
    "end_to_end_seconds", "started_at", "finished_at", "log_path",
]
FLOAT = r"([0-9]+(?:\.[0-9]+)?)"
PROMPT_RE = re.compile(r"start\[\s*(\d+)\s*time\]merge now\?", re.I)
CLIENT_FAILURE_RE = re.compile(
    r"(?:upload data failed|\bset failed|mergeStripes RPC failed|"
    r"merge returned failure|merge stopped early)", re.I)


class ClientOutputError(RuntimeError):
    pass


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def load_manifest(path: Path = DEFAULT_MANIFEST) -> Dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _canonical_key(config: Dict[str, Any]) -> Tuple[Any, ...]:
    return (config["code_type"], config["encoding"], config["k"], config["l"], config["g"],
            config["block_size_bytes"], config["stripes"],
            config["intra_gbps"], config["inter_gbps"])


def build_configurations(manifest: Dict[str, Any], experiments: Optional[Set[str]] = None,
                         stripes_override: Optional[int] = None,
                         algorithms: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
    """Build the de-duplicated matrix while retaining every matching label."""
    defaults = manifest["defaults"]
    selected = experiments or {item["name"] for item in manifest["experiments"]}
    known = {item["name"] for item in manifest["experiments"]}
    unknown = selected - known
    if unknown:
        raise ValueError("unknown experiment(s): " + ", ".join(sorted(unknown)))
    selected_algorithms = list(algorithms) if algorithms is not None else list(
        defaults.get("algorithms", ALGORITHMS))
    unknown_algorithms = set(selected_algorithms) - set(ALGORITHMS)
    if unknown_algorithms:
        raise ValueError("unknown algorithm(s): " + ", ".join(sorted(unknown_algorithms)))
    if not selected_algorithms:
        raise ValueError("at least one algorithm is required")
    unique: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
    for code_type in selected_algorithms:
        for encoding in manifest["encoding_parameters"]:
            base = {
                "code_type": code_type, "encoding": encoding["name"], "k": encoding["k"],
                "l": encoding["l"], "g": encoding["g"],
                "block_size_bytes": defaults["block_size_bytes"],
                "stripes": defaults["stripes"], "intra_gbps": defaults["intra_gbps"],
                "inter_gbps": defaults["inter_gbps"],
            }
            for experiment in manifest["experiments"]:
                if experiment["name"] not in selected:
                    continue
                dimension = experiment["dimension"]
                values = [None] if dimension == "encoding" else experiment["values"]
                for value in values:
                    config = dict(base)
                    if dimension != "encoding":
                        config[dimension] = value
                    if stripes_override is not None:
                        config["stripes"] = stripes_override
                    key = _canonical_key(config)
                    if key not in unique:
                        config["experiment_labels"] = []
                        unique[key] = config
                    unique[key]["experiment_labels"].append(experiment["name"])
    result = list(unique.values())
    for config in result:
        config["experiment_labels"].sort()
        identity = json.dumps(_canonical_key(config), separators=(",", ":"))
        config["config_id"] = hashlib.sha256(identity.encode()).hexdigest()[:16]
    return result


def build_runs(configurations: Sequence[Dict[str, Any]], repetitions: int) -> List[Dict[str, Any]]:
    if repetitions <= 0:
        raise ValueError("repetitions must be positive")
    runs = []
    for config in configurations:
        for repetition in range(1, repetitions + 1):
            run = dict(config)
            run["repetition"] = repetition
            run["run_id"] = "%s-r%02d" % (config["config_id"], repetition)
            runs.append(run)
    return runs


def render_xml(source: bytes, block_size: int, k: int, r: int, z: int,
               code_type: Optional[str] = None) -> bytes:
    root = ET.fromstring(source)
    replacements: Dict[str, Any] = {"BlockSize": block_size, "k": k, "r": r, "z": z}
    if code_type is not None:
        replacements["CodeType"] = code_type
    for tag, value in replacements.items():
        nodes = root.findall(tag)
        if len(nodes) != 1:
            raise ValueError("expected exactly one <%s>, found %d" % (tag, len(nodes)))
        nodes[0].text = str(value)
    body = ET.tostring(root, encoding="utf-8")
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + body + b"\n"


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


def update_xml_file(path: Path, block_size: int, k: int, r: int, z: int,
                   code_type: Optional[str] = None) -> bytes:
    original = path.read_bytes()
    atomic_write(path, render_xml(original, block_size, k, r, z, code_type))
    return original


def parse_client_log(text: str) -> Dict[str, Any]:
    """Parse write metrics and either DdlRT_LRC or ClusterRT_LRC round metrics."""
    result: Dict[str, Any] = {"write_time_seconds": None,
                              "write_throughput_mib_s": None, "rounds": []}
    match = re.search(r"write time:\s*" + FLOAT, text, re.I)
    if match:
        result["write_time_seconds"] = float(match.group(1))
    match = re.search(r"write throughput:\s*" + FLOAT, text, re.I)
    if match:
        result["write_throughput_mib_s"] = float(match.group(1))

    totals: Dict[int, int] = {}
    for match in re.finditer(r"will merge\s+(\d+)\s+pairs\s*\(round\s+(\d+)(?:\s*,[^)]*)?\)", text, re.I):
        totals[int(match.group(2))] = int(match.group(1))
    successes: Dict[int, int] = {}
    current_prompt_round = 0
    for line in text.splitlines():
        prompt = PROMPT_RE.search(line)
        if prompt:
            current_prompt_round = int(prompt.group(1))
        if "merge succeeded" in line and current_prompt_round:
            successes[current_prompt_round] = successes.get(current_prompt_round, 0) + 1

    round_starts = list(re.finditer(r"\[merge\s*(\d+)\s*time\]\s*end-to-end time:\s*" + FLOAT, text, re.I))
    rounds: Dict[int, Dict[str, Any]] = {}
    for index, match in enumerate(round_starts):
        number = int(match.group(1))
        end = round_starts[index + 1].start() if index + 1 < len(round_starts) else len(text)
        section = text[match.start():end]
        item: Dict[str, Any] = {"round": number, "end_to_end_seconds": float(match.group(2))}
        patterns = {
            "critical_path_seconds": r"estimated parallel critical path:\s*" + FLOAT,
            "migration_sum_seconds": r"coordinator data migration sum:\s*" + FLOAT,
            "parity_sum_seconds": r"coordinator parity update sum:\s*" + FLOAT,
        }
        for key, pattern in patterns.items():
            found = re.search(pattern, section, re.I)
            item[key] = float(found.group(1)) if found else None
        item["pairs_total"] = totals.get(number)
        item["pairs_completed"] = successes.get(number, 0)
        rounds[number] = item

    # ClusterRT variants use underscore or human-readable metric names, often on one line.
    cluster_round = re.compile(r"(?:ClusterRT[^\n]*?)?(?:round|merge)\s*[=: ]\s*(\d+)([^\n]*)", re.I)
    aliases = {
        "migration_sum_seconds": r"(?:migration_sum|data migration sum)\s*[=:]\s*" + FLOAT,
        "parity_sum_seconds": r"(?:parity_sum|parity update sum)\s*[=:]\s*" + FLOAT,
        "critical_path_seconds": r"(?:critical_path|critical path)\s*[=:]\s*" + FLOAT,
        "end_to_end_seconds": r"(?:end_to_end|end-to-end time)\s*[=:]\s*" + FLOAT,
    }
    for match in cluster_round.finditer(text):
        number, section = int(match.group(1)), match.group(2)
        if not any(re.search(pattern, section, re.I) for pattern in aliases.values()):
            continue
        item = rounds.setdefault(number, {"round": number})
        pair = re.search(r"pairs(?:\s+completed)?\s*[=:]\s*(\d+)\s*/\s*(\d+)", section, re.I)
        if pair:
            item["pairs_completed"], item["pairs_total"] = int(pair.group(1)), int(pair.group(2))
        for key, pattern in aliases.items():
            found = re.search(pattern, section, re.I)
            if found:
                item[key] = float(found.group(1))
    for number, item in rounds.items():
        item.setdefault("pairs_total", totals.get(number))
        item.setdefault("pairs_completed", successes.get(number, 0))
        for key in aliases:
            item.setdefault(key, None)
    result["rounds"] = [rounds[key] for key in sorted(rounds)]
    return result


def atomic_json(path: Path, value: Any) -> None:
    atomic_write(path, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode())


def append_jsonl(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def append_csv(path: Path, record: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists() and path.stat().st_size > 0
    rows = record.get("rounds") or [{}]
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        for round_data in rows:
            row = dict(record)
            row.update(round_data)
            row["experiment_labels"] = ";".join(record["experiment_labels"])
            row.pop("rounds", None)
            writer.writerow(row)
        handle.flush()
        os.fsync(handle.fileno())


class Runner:
    def __init__(self, args: argparse.Namespace, runs: Sequence[Dict[str, Any]]):
        self.args = args
        self.runs = runs
        self.output = args.output.resolve()
        self.state_path = self.output / "state.json"
        self.results_jsonl = self.output / "results.jsonl"
        self.results_csv = self.output / "results.csv"
        self.logs = self.output / "logs"
        self.original_xml: Optional[bytes] = None
        self.current_process: Optional[subprocess.Popen[str]] = None
        self.stopping = False
        self.state: Dict[str, Any] = {"runs": {}}

    def _command(self, command: Sequence[str], timeout: float, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(command, cwd=str(REPO_ROOT), text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=timeout, check=check)

    def preflight(self) -> None:
        required_commands = ("bash", "pdsh", "rsync", "ssh", "sudo")
        missing_commands = [name for name in required_commands if shutil.which(name) is None]
        required_files = [
            self.args.xml, self.args.client, REPO_ROOT / "hosts", REPO_ROOT / "proxy_hosts",
            *[REPO_ROOT / name for name in (
                "update_all.sh", "kill_all_nodes.sh", "start_proxy.sh",
                "start_coordinator.sh", "limit_bandwidth.sh", "unlimit_all.sh")],
        ]
        missing_files = [str(path) for path in required_files if not path.is_file()]
        if missing_commands or missing_files:
            details = []
            if missing_commands:
                details.append("missing commands: " + ", ".join(missing_commands))
            if missing_files:
                details.append("missing files: " + ", ".join(missing_files))
            raise RuntimeError("preflight failed: " + "; ".join(details))
        if not os.access(self.args.client, os.X_OK):
            raise RuntimeError("preflight failed: client is not executable: %s" % self.args.client)
        self._command(["sudo", "-n", "true"], self.args.command_timeout)
        for hosts_file in (REPO_ROOT / "hosts", REPO_ROOT / "proxy_hosts"):
            self._command(["pdsh", "-S", "-R", "ssh", "-w", "^" + str(hosts_file),
                           "-l", "root", "-f", "50", "true"], self.args.command_timeout)

    def cleanup_cluster(self) -> None:
        errors = []
        for script in ("unlimit_all.sh", "kill_all_nodes.sh"):
            try:
                self._command(["bash", str(REPO_ROOT / script)], self.args.command_timeout)
            except Exception as error:
                errors.append("%s: %s" % (script, error))
        if errors:
            print("cleanup warnings: " + "; ".join(errors), file=sys.stderr)

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
                print("warning: restored local XML but could not sync it: %s" % error,
                      file=sys.stderr)

    def stop(self, signum: int, _frame: Any) -> None:
        self.stopping = True
        print("received signal %d; stopping after cleanup" % signum, file=sys.stderr)
        if self.current_process and self.current_process.poll() is None:
            self.current_process.terminate()

    def _run_client(self, run: Dict[str, Any], log_path: Path) -> Tuple[int, str]:
        command = [str(self.args.client), "--coordinator", self.args.coordinator,
                   "--stripes", str(run["stripes"])]
        started = time.monotonic()
        transcript: List[str] = []
        prompt_buffer = ""
        failure_buffer = ""
        limited = False
        selector = selectors.DefaultSelector()
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        with log_path.open("w", encoding="utf-8", buffering=1) as log:
            process = subprocess.Popen(command, cwd=str(REPO_ROOT), stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       bufsize=0)
            self.current_process = process
            assert process.stdout is not None and process.stdin is not None
            selector.register(process.stdout, selectors.EVENT_READ)

            def consume_output(output: str) -> None:
                nonlocal failure_buffer, limited, prompt_buffer
                if not output:
                    return
                transcript.append(output)
                log.write(output)
                sys.stdout.write(output)
                sys.stdout.flush()
                failure_buffer += output
                failure = CLIENT_FAILURE_RE.search(failure_buffer)
                if failure:
                    line_start = failure_buffer.rfind("\n", 0, failure.start()) + 1
                    line_end = failure_buffer.find("\n", failure.end())
                    if line_end < 0:
                        line_end = len(failure_buffer)
                    raise ClientOutputError(failure_buffer[line_start:line_end].strip())
                failure_buffer = failure_buffer[-512:]
                prompt_buffer += output
                while True:
                    prompt = PROMPT_RE.search(prompt_buffer)
                    if not prompt:
                        # A prompt is short; retain only enough trailing text for a split chunk.
                        prompt_buffer = prompt_buffer[-256:]
                        return
                    merge_round = int(prompt.group(1))
                    prompt_buffer = prompt_buffer[prompt.end():]
                    if merge_round == 1 and not limited:
                        completed = self._command(
                            ["bash", str(REPO_ROOT / "limit_bandwidth.sh"),
                             str(run["inter_gbps"])], self.args.command_timeout)
                        log.write("[runner] bandwidth command output:\n" + completed.stdout)
                        limited = True
                    answer = "Y" if merge_round <= 2 else "N"
                    # SRS/ERS receives the merge round as a function argument,
                    # so its interactive protocol consumes only the Y/N answer.
                    payload = answer + "\n"
                    process.stdin.write(payload.encode("utf-8"))
                    process.stdin.flush()
                    log.write("[runner] sent %s for merge round %d\n" % (answer, merge_round))

            try:
                stdout_fd = process.stdout.fileno()
                reached_eof = False
                while not reached_eof:
                    if self.stopping and process.poll() is None:
                        process.terminate()
                    if time.monotonic() - started > self.args.client_timeout:
                        process.kill()
                        process.wait()
                        raise TimeoutError("client exceeded %.1f seconds" % self.args.client_timeout)
                    events = selector.select(timeout=1.0)
                    for _, _ in events:
                        chunk = os.read(stdout_fd, 65536)
                        if chunk:
                            consume_output(decoder.decode(chunk))
                        else:
                            reached_eof = True
                            selector.unregister(process.stdout)
                            break
                consume_output(decoder.decode(b"", final=True))
                return process.wait(), "".join(transcript)
            finally:
                selector.close()
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                process.stdin.close()
                process.stdout.close()
                self.current_process = None

    def _attempt(self, run: Dict[str, Any], attempt: int) -> Dict[str, Any]:
        started_at = utc_now()
        log_path = self.logs / (run["run_id"] + "-attempt%02d.log" % attempt)
        record = dict(run)
        record.update({"status": "failed", "failure_reason": "", "attempts": attempt,
                       "started_at": started_at, "finished_at": None,
                       "log_path": str(log_path.resolve()), "rounds": []})
        try:
            self.cleanup_cluster()
            atomic_write(self.args.xml, render_xml(self.original_xml or self.args.xml.read_bytes(),
                                                   run["block_size_bytes"], run["k"], run["g"], run["l"],
                                                   run["code_type"]))
            self._command(["bash", str(REPO_ROOT / "update_all.sh")], self.args.command_timeout)
            self._command(["bash", str(REPO_ROOT / "start_proxy.sh")], self.args.command_timeout)
            self._command(["bash", str(REPO_ROOT / "start_coordinator.sh")], self.args.command_timeout)
            time.sleep(self.args.startup_wait)
            return_code, text = self._run_client(run, log_path)
            parsed = parse_client_log(text)
            record.update(parsed)
            if return_code != 0:
                raise RuntimeError("client exited with status %d" % return_code)
            if parsed["write_time_seconds"] is None or parsed["write_throughput_mib_s"] is None:
                raise RuntimeError("write metrics were not found in client output")
            if len(parsed["rounds"]) != 2:
                raise RuntimeError("expected 2 parsed merge rounds, found %d" % len(parsed["rounds"]))
            required_metrics = ("pairs_completed", "pairs_total", "migration_sum_seconds",
                                "parity_sum_seconds", "critical_path_seconds",
                                "end_to_end_seconds")
            missing = [(item["round"], key) for item in parsed["rounds"]
                       for key in required_metrics if item.get(key) is None]
            if missing:
                raise RuntimeError("missing merge metrics: %s" % missing)
            incomplete = [item for item in parsed["rounds"]
                          if item.get("pairs_total") is not None and
                          item.get("pairs_completed") != item.get("pairs_total")]
            if incomplete:
                raise RuntimeError("one or more merge rounds did not complete all pairs")
            record["status"] = "success"
        except Exception as error:
            record["failure_reason"] = "%s: %s" % (type(error).__name__, error)
        finally:
            record["finished_at"] = utc_now()
            # Every attempt leaves a clean baseline. This runs before a retry and
            # after the final failed attempt, so bandwidth shaping and cluster
            # processes cannot leak into the next experiment.
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
        backup = self.output / "parameterConfiguration.xml.backup"
        atomic_write(backup, self.original_xml)
        signal.signal(signal.SIGINT, self.stop)
        signal.signal(signal.SIGTERM, self.stop)
        try:
            for index, run in enumerate(self.runs, 1):
                if self.stopping:
                    break
                previous = self.state["runs"].get(run["run_id"], {})
                if self.args.resume and previous.get("status") == "success":
                    print("[%d/%d] skip successful %s" % (index, len(self.runs), run["run_id"]))
                    continue
                print("[%d/%d] run %s labels=%s" %
                      (index, len(self.runs), run["run_id"], ",".join(run["experiment_labels"])))
                final = None
                for attempt in range(1, self.args.retries + 2):
                    final = self._attempt(run, attempt)
                    if final["status"] == "success" or self.stopping:
                        break
                    print("attempt failed: " + final["failure_reason"], file=sys.stderr)
                assert final is not None
                append_jsonl(self.results_jsonl, final)
                append_csv(self.results_csv, final)
                self.state["runs"][run["run_id"]] = {
                    "status": final["status"], "finished_at": final["finished_at"],
                    "failure_reason": final["failure_reason"], "attempts": final["attempts"],
                }
                atomic_json(self.state_path, self.state)
            return 130 if self.stopping else 0
        finally:
            self.restore()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    result.add_argument("--execute", action="store_true", help="actually operate the cluster")
    result.add_argument("--dry-run", action="store_true", help="list only; never mutate or invoke commands")
    result.add_argument("--experiment", action="append", choices=["encoding", "bandwidth", "stripes", "block_size"])
    result.add_argument("--algorithm", action="append", choices=list(ALGORITHMS),
                        help="run SRS and/or ERS; default is both")
    result.add_argument("--resume", action="store_true", help="skip successful run IDs from state.json")
    result.add_argument("--restart", action="store_true", help="ignore prior state and run selected items again")
    result.add_argument("--repetitions", type=int)
    result.add_argument("--stripes", type=int, help="override stripes for every selected configuration")
    result.add_argument("--coordinator", default="10.10.1.2:55555")
    result.add_argument("--client", type=Path, default=REPO_ROOT / "project/cmake/build/main_client")
    result.add_argument("--xml", type=Path, default=DEFAULT_XML)
    result.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    result.add_argument("--command-timeout", type=float, default=300.0)
    result.add_argument("--client-timeout", type=float, default=7200.0)
    result.add_argument("--startup-wait", type=float, default=10.0)
    result.add_argument("--retries", type=int, default=1, help="retries after the first attempt")
    result.add_argument("--skip-preflight", action="store_true",
                        help="skip command, sudo, and cluster SSH checks")
    return result


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parser().parse_args(argv)
    if args.execute and args.dry_run:
        raise SystemExit("--execute and --dry-run are mutually exclusive")
    if args.resume and args.restart:
        raise SystemExit("--resume and --restart are mutually exclusive")
    if args.repetitions is not None and args.repetitions <= 0:
        raise SystemExit("--repetitions must be positive")
    if args.stripes is not None and args.stripes <= 0:
        raise SystemExit("--stripes must be positive")
    if args.retries < 0 or min(args.command_timeout, args.client_timeout, args.startup_wait) < 0:
        raise SystemExit("timeouts, waits, and retries must be non-negative")
    manifest = load_manifest(args.manifest)
    selected = set(args.experiment) if args.experiment else None
    algorithms = args.algorithm or None
    configurations = build_configurations(manifest, selected, args.stripes, algorithms)
    repetitions = args.repetitions or manifest["defaults"]["repetitions"]
    runs = build_runs(configurations, repetitions)
    print("unique configurations: %d" % len(configurations))
    print("planned runs: %d" % len(runs))
    for config in configurations:
        print("{config_id} code_type={code_type} labels={labels} encoding={encoding} k/l/g={k}/{l}/{g} "
              "block={block_size_bytes} stripes={stripes} intra/inter={intra_gbps}/{inter_gbps}".format(
                  labels=",".join(config["experiment_labels"]), **config))
    if not args.execute:
        print("plan only; pass --execute to operate the cluster")
        return 0
    if not args.client.is_file() or not os.access(args.client, os.X_OK):
        raise SystemExit("client is missing or not executable: %s" % args.client)
    return Runner(args, runs).execute()


if __name__ == "__main__":
    raise SystemExit(main())

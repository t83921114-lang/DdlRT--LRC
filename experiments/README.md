# Overnight experiments

`run_overnight.py` builds a de-duplicated experiment plan from `manifest.json` and, only when explicitly requested, runs it against the cluster. It uses Python 3's standard library only.

## Matrix

The four encoding tuples `(k,l,g)` map to XML as `k=k`, `z=l`, and `r=g`. The matrix crosses every encoding with:

- encoding baseline: 1 MiB, 1000 stripes, intra/inter 10/1 Gbit/s;
- inter bandwidth: 0.5, 1, 2, 5, and 10 Gbit/s, with intra fixed at 10 Gbit/s;
- stripe count: 500, 1000, 1500, and 2000;
- block size: 256 KiB, 512 KiB, 1 MiB, 2 MiB, and 4 MiB.

Overlapping baseline points are stored once while retaining all matching `experiment_labels`. The complete plan has 48 unique configurations and 240 runs at five repetitions each. Every run performs two merge rounds.

## Safe planning and execution

The default is plan-only and does not edit XML or invoke any cluster script:

```bash
bash experiments/run_experiments.sh
bash experiments/run_experiments.sh --dry-run
```

Real execution always requires `--execute`. Before changing XML, the runner checks required commands and files, passwordless `sudo`, and root SSH reachability for both host lists:

Before the first real run, rebuild `main_client`; the experiment runner relies on its
`--coordinator` and `--stripes` options:

```bash
cd project && bash compile.sh && cd ..
bash experiments/run_experiments.sh --execute --resume
```

Useful controls:

```bash
# Small smoke run (still operates the real cluster because --execute is present)
bash experiments/run_experiments.sh --execute --experiment encoding --stripes 4 --repetitions 1

# Run only bandwidth configurations and retry failures twice after the first attempt
bash experiments/run_experiments.sh --execute --experiment bandwidth --resume --retries 2

# Ignore prior state for selected runs
bash experiments/run_experiments.sh --execute --experiment stripes --restart
```

`--experiment` may be repeated. Other operational controls include `--coordinator`, `--client`, `--output`, `--command-timeout`, `--client-timeout`, and `--startup-wait`. `--skip-preflight` is available only for environments where equivalent checks are handled externally. `--resume` skips successful run IDs in the atomic state file; failed runs remain eligible. `--restart` ignores state but does not erase append-only historical result files.

## Merge-bandwidth handshake

The runner deliberately does not pass `--merge-rounds`. It launches `main_client` with `--coordinator` and `--stripes`, reads merged stdout/stderr line by line, and waits for:

```text
start[ 1 time]merge now? (Y/N)
```

Only after that line—which occurs after all stripe writes—does it call `limit_bandwidth.sh RATE`. It then writes `Y` followed by the corresponding numeric round (`1` or `2`) to stdin, satisfying both prompts consumed by the current client. It would answer `N` to any later Y/N prompt. This avoids limiting write traffic and closes the race that would exist if shaping were applied merely after process launch. The client's prompt uses `std::endl`, so it flushes even though stdout is piped. Client and runner output is merged into one raw per-attempt log.

## Lifecycle and output

For each attempt the runner:

1. calls `unlimit_all.sh` and `kill_all_nodes.sh`;
2. updates `BlockSize`, `k`, `r`, and `z` using an XML parser and an atomic file replacement;
3. calls `update_all.sh`, `start_proxy.sh`, and `start_coordinator.sh`, then waits for startup;
4. runs the interactive client handshake above;
5. removes bandwidth limits, records the result, and continues after bounded retries.

On normal exit, SIGINT, or SIGTERM it makes a best effort to terminate the client, remove limits, kill nodes, atomically restore the exact original XML bytes, and synchronize that restored configuration back to the cluster. A copy is also retained as `parameterConfiguration.xml.backup` in the output directory.

By default, output is written to the sibling directory `../DdlRT--LRC-experiment-results`, outside the repository. Keeping results outside the repository prevents `update_all.sh` from repeatedly copying accumulated logs to every cluster node. Use `--output` to select another location.

The output directory contains:

- `results.jsonl`: one append-only record per final run outcome, including both rounds;
- `results.csv`: one row per merge round (or one failure row if no round parsed);
- `state.json`: atomically replaced resume state;
- `logs/`: an independent raw client log for each run attempt.

The parser supports the current DdlRT_LRC multiline output (`will merge`, `merge succeeded`, and `[mergeNtime] ...`) and ClusterRT_LRC summary lines containing pairs, `migration_sum`, `parity_sum`, `critical_path`, and `end_to_end`.

## Local tests

Tests use only temporary local files and never invoke cluster scripts:

```bash
python3 -m unittest discover -s experiments/tests -v
```

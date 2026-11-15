#!/usr/bin/env python3

import csv
import os
import signal
import statistics
import subprocess
import sys
from itertools import product
from pathlib import Path
from time import perf_counter

SCRIPTS = [
    # "scripts/blant-clusters-v1.sh",
    "scripts/blant-clusters-v2-core.sh",
    # "scripts/blant-clusters-v3-jeremy-core.sh",
]

SCRIPT_OPTS: dict[str, list[str]] = {
    "scripts/blant-cluster-v2-core.sh": ["-X", "10"],
    "scripts/blant-cluster-v3-jeremy-core.sh": ["-X", "10"],
}

NETWORKS = [
    # "networks/AThaliana.el",
    "networks/CElegans.el",
    # "networks/DMelanogaster.el",
    # "networks/HI-union.el",
    # "networks/HSapiens.el",
    # "networks/yeast.el",
]

# (Ks, EDs)
PARAMS = [
    ("4", "0.6"),
    ("4", "0.8"),
    ("4", "1.0"),
    ("6", "0.6"),
    ("6", "0.8"),
    ("6", "1.0"),
]

RANDOM_SEED = 99
ITERATIONS = 10  # per cmd with random seed
TIMEOUT_S: int | None = None  # per run

OUTPUT = Path("benchmark_results1")
OUTPUT_RESULTS = OUTPUT / "results"
OUTPUT_LOGS = OUTPUT / "logs"
OUTPUT_SUMMARY = OUTPUT / "summary.tsv"

FIELDS = [
    "script",
    "network",
    "k",
    "ed",
    "wall_mean_xbar_s",
    "wall_se_s",
    "wall_min_s",
    "wall_max_s",
    "clusters_mean_xbar",
    "clusters_se",
    "clusters_min",
    "clusters_max",
    "options",
    "runs_ok",
    "runs_total",
    "success_rate_%",
    "statuses",
]


def check_setup() -> None:
    missing = [p for p in [*SCRIPTS, *NETWORKS, "./blant"] if not Path(p).exists()]
    if missing:
        sys.exit(f"Missing files (are you in the BLANT repo root?): {missing}")
    for k in {k for k, _ in PARAMS}:
        if not Path(f"canon_maps/canon_list{k}.txt").exists():
            sys.exit(f"canon_maps/canon_list{k}.txt missing")


def run_once(
    script: str, network: str, k: str, ed: str, seed: int, filename: str
) -> dict[str, float | str]:
    out_f = OUTPUT_RESULTS / f"{filename}.out"
    log_f = OUTPUT_LOGS / f"{filename}.log"

    cmd = [
        "bash",
        script,
        "-r",
        str(seed),
        *(SCRIPT_OPTS.get(script, [])),
        "./blant",
        k,
        ed,
        network,
    ]

    t0 = perf_counter()
    with out_f.open("wb") as fo, log_f.open("wb") as fe:
        # own process group so a timeout kills BLANT + the awk pipeline, not just bash
        proc = subprocess.Popen(cmd, stdout=fo, stderr=fe, start_new_session=True)
        try:
            status: int | str = proc.wait(timeout=TIMEOUT_S)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            status = "timeout"
    wall = perf_counter() - t0

    with out_f.open("rb") as f:
        num_clusters = sum(1 for _ in f)
    return {
        "status": status,
        "wall": wall,
        "clusters": num_clusters,
    }


def summarize(
    script: str, network: str, k: str, ed: str, runs: list[dict[str, float | str]]
) -> dict[str, float | str]:
    ok = [r for r in runs if r["status"] == 0]
    walls: list[float] = [r["wall"] for r in ok]  # type: ignore
    clusters: list[int] = [r["clusters"] for r in ok]  # type: ignore
    row: dict[str, float | str] = {
        "script": script,
        "network": network,
        "k": k,
        "ed": ed,
        "wall_mean_xbar_s": "",
        "wall_se_s": "",
        "wall_min_s": "",
        "wall_max_s": "",
        "clusters_mean_xbar": "",
        "clusters_se": "",
        "clusters_min": "",
        "clusters_max": "",
        "options": " ".join(SCRIPT_OPTS.get(script, [])),
        "runs_ok": len(ok),
        "runs_total": len(runs),
        "success_rate_%": round(100 * len(ok) / len(runs), 2),
        "statuses": ",".join(str(r["status"]) for r in runs),
    }
    if walls:
        row.update(
            wall_mean_xbar_s=round(statistics.mean(walls), 4),
            wall_se_s=round(statistics.stdev(walls), 4) if len(walls) > 1 else 0.0,
            wall_min_s=round(min(walls), 4),
            wall_max_s=round(max(walls), 4),
        )
    if clusters:
        row.update(
            clusters_mean_xbar=round(statistics.mean(clusters), 4),
            clusters_se=round(statistics.stdev(clusters), 4) if len(walls) > 1 else 0.0,
            clusters_min=round(min(clusters), 4),
            clusters_max=round(max(clusters), 4),
        )
    return row


def benchmark() -> None:
    check_setup()
    OUTPUT_RESULTS.mkdir(parents=True, exist_ok=True)
    OUTPUT_LOGS.mkdir(parents=True, exist_ok=True)

    print("Starting benchmark...")

    with OUTPUT_SUMMARY.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, delimiter="\t")
        writer.writeheader()

        for network, (k, ed) in product(NETWORKS, PARAMS):
            scripts_runs: dict[str, list[dict[str, float | str]]] = {s: [] for s in SCRIPTS}
            print(f"===== {network} k={k} ED={ed} =====")
            for i in range(ITERATIONS):
                # rotate the script order each iteration so no version always runs first (cache/thermal bias)
                r = i % len(SCRIPTS)
                print(f"{i:02d}| ")
                for script in SCRIPTS[r:] + SCRIPTS[:r]:
                    seed = RANDOM_SEED + i
                    filename = (
                        f"{Path(script).stem}_{Path(network).stem}_k{k}_ed{ed}_r{seed}"
                    )
                    print(
                        f"   {script} {network} k={k} ED={ed} seed={seed}",
                        flush=True,
                    )
                    res = run_once(script, network, k, ed, seed, filename)
                    if res["status"] != 0:
                        print(
                            f"\t[!] run {i + 1} failed ({res['status']}); see {OUTPUT_LOGS / (filename + '.log')}"
                        )
                    else:
                        print(f"\t{res['wall']:.2f}s wall, {res['clusters']} clusters")
                    scripts_runs[script].append(res)

            rows = [summarize(s, network, k, ed, scripts_runs[s]) for s in SCRIPTS]
            writer.writerows(rows)
            f.flush()
    print("Benchmark ended.\nSee summary:", OUTPUT_SUMMARY)


if __name__ == "__main__":
    benchmark()

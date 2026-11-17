#!/usr/bin/env python3

import csv
import os
import signal
import statistics
import subprocess
import sys
from itertools import chain, product, repeat
from pathlib import Path
from time import perf_counter
from typing import Literal

# required args
SCRIPTS: dict[str, set[str]] = {
    "scripts/blant-clusters-v1.sh": set(),
    # "scripts/blant-clusters-v2-core.sh": {"-X"},
    # "scripts/blant-clusters-v3-jeremy-core.sh": {"-X"},
}
SCRIPTS_NAMES = list(SCRIPTS.keys())

NETWORKS = [
    # "networks/AThaliana.el",
    # "networks/CElegans.el",
    "networks/DMelanogaster.el",
    # "networks/HI-union.el",
    # "networks/HSapiens.el",
    # "networks/yeast.el",
]
K_GRAPHLETS = [
    "4",
    "6",
]
EDGE_DENSITIES = [
    # "0.6",
    # "0.8",
    "1.0",
]

# optional args
# boolean flag: "" (True), None (False)
OPTIONS: dict[str, tuple[str, ...] | tuple[Literal[""], None]] = {
    "-X": (
        "0",
        # "5",
        # "10",
    ),
}

RANDOM_SEED = 99
ITERATIONS = 10  # per cmd with random seed
TIMEOUT_S: int | None = None  # per run

OUTPUT = Path("benchmark_results")
OUTPUT_TMP = OUTPUT / "tmp"
OUTPUT_CACHE = OUTPUT / "cache"
OUTPUT_RESULTS = OUTPUT / "results"
OUTPUT_LOGS = OUTPUT / "logs"
OUTPUT_SUMMARY = OUTPUT / "summary.tsv"

FIELDS = [
    "script",
    "network",
    "k",
    "ed",
    "x",
    "wall_mean_xbar_s",
    "wall_sd_s",
    "wall_min_s",
    "wall_max_s",
    "clusters_mean_xbar",
    "clusters_sd",
    "clusters_min",
    "clusters_max",
    "runs_ok",
    "runs_total",
    "success_rate_%",
    "statuses",
    "options",
]


def check_setup() -> None:
    missing = [p for p in [*SCRIPTS, *NETWORKS, "./blant"] if not Path(p).exists()]
    if missing:
        sys.exit(f"Missing files (are you in the BLANT repo root?): {missing}")
    for k in K_GRAPHLETS:
        if not Path(f"canon_maps/canon_list{k}.txt").exists():
            sys.exit(f"canon_maps/canon_list{k}.txt missing")


def cache_blant(network: str, k: str, seed: int) -> Path:
    d = OUTPUT_CACHE / f"{Path(network).stem}" / f"r{seed}"
    out = d / f"blant{k}.out"
    if not out.exists():
        d.mkdir(parents=True, exist_ok=True)
        with out.open("wb") as f:
            # default args
            subprocess.run(
                [
                    "./blant",
                    "-p0.9L",
                    "-q",
                    "-r",
                    str(seed),
                    f"-k{k}",
                    "-sEBE",
                    "-mcg",
                    network,
                ],
                stdout=f,
                check=True,
            )
    return d


def run_once(
    script: str,
    network: str,
    k: str,
    ed: str,
    seed: int,
    options: list[str],
    filename: str,
) -> dict[str, float | str]:
    str_cache = str(cache_blant(network, k, seed))
    return {
        "status": 0,
        "wall": 1.0,
        "clusters": 1,
        "options": "",
    }

    str_network_name = Path(network).stem
    str_seed = str(seed)

    out_f = OUTPUT_RESULTS / str_network_name / f"r{str_seed}" / f"{filename}.out"
    log_f = OUTPUT_LOGS / str_network_name / f"r{str_seed}" / f"{filename}.log"
    out_f.parent.mkdir(parents=True, exist_ok=True)
    log_f.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "bash",
        script,
        "-r",
        str_seed,
        "-D",
        str_cache,
        "-T",
        str(OUTPUT_TMP),
        *options,
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
        "options": " ".join(options),
    }


def get_option_value(
    key: str, script: str, options: list[tuple[str, str]], default: str
) -> str:
    return next((v for k, v in options if k == key and key in SCRIPTS[script]), default)


def summarize(
    script: str,
    network: str,
    k: str,
    ed: str,
    x: str,
    options: str,
    runs: list[dict[str, float | str]],
) -> dict[str, float | str]:
    ok = [r for r in runs if r["status"] == 0]
    walls: list[float] = [r["wall"] for r in ok]  # type: ignore
    clusters: list[int] = [r["clusters"] for r in ok]  # type: ignore
    row: dict[str, float | str] = {
        "script": script,
        "network": network,
        "k": k,
        "ed": ed,
        "x": x,
        "wall_mean_xbar_s": "",
        "wall_sd_s": "",
        "wall_min_s": "",
        "wall_max_s": "",
        "clusters_mean_xbar": "",
        "clusters_sd": "",
        "clusters_min": "",
        "clusters_max": "",
        "runs_ok": len(ok),
        "runs_total": len(runs),
        "success_rate_%": round(100 * len(ok) / len(runs), 2),
        "statuses": ",".join(str(r["status"]) for r in runs),
        "options": options,
    }
    if walls:
        row.update(
            wall_mean_xbar_s=round(statistics.mean(walls), 4),
            wall_sd_s=round(statistics.stdev(walls), 4) if len(walls) > 1 else 0.0,
            wall_min_s=round(min(walls), 4),
            wall_max_s=round(max(walls), 4),
        )
    if clusters:
        row.update(
            clusters_mean_xbar=round(statistics.mean(clusters), 4),
            clusters_sd=round(statistics.stdev(clusters), 4)
            if len(clusters) > 1
            else 0.0,
            clusters_min=round(min(clusters), 4),
            clusters_max=round(max(clusters), 4),
        )
    return row


def benchmark() -> None:
    check_setup()

    OUTPUT_CACHE.mkdir(parents=True, exist_ok=True)
    OUTPUT_TMP.mkdir(parents=True, exist_ok=True)
    OUTPUT_RESULTS.mkdir(parents=True, exist_ok=True)
    OUTPUT_LOGS.mkdir(parents=True, exist_ok=True)

    required_args_combinations = product(NETWORKS, K_GRAPHLETS, EDGE_DENSITIES)
    options_combinations = list(
        product(*(zip(repeat(k), vals) for k, vals in OPTIONS.items()))
    )

    tested_cmds: set[str] = set()

    print("Starting benchmark...")

    with OUTPUT_SUMMARY.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, delimiter="\t")
        if f.tell() == 0:
            writer.writeheader()
        else:
            f.write("\n")

        for network, k, ed in required_args_combinations:
            print(f"========== {network} k={k} ED={ed} ==========")

            for options in options_combinations:
                scripts_runs: dict[str, list[dict[str, float | str]]] = {
                    s: [] for s in SCRIPTS
                }
                scripts_options: dict[str, str] = {
                    s: "" for s in SCRIPTS
                }

                filtered_options: list[tuple[str, str]] = [
                    opt for opt in options if opt[1] is not None
                ]  # type: ignore
                print(
                    f"===== {' '.join(f'{opt[0]}={opt[1]}' if opt[1] != '' else opt[0] for opt in filtered_options)} ====="
                )
                for i in range(ITERATIONS):
                    # rotate the script order each iteration so no version always runs first (cache/thermal bias)
                    r = i % len(SCRIPTS_NAMES)
                    print(f"{i:02d}| ")
                    for script in SCRIPTS_NAMES[r:] + SCRIPTS_NAMES[:r]:
                        valid_options = [
                            opt for opt in filtered_options if opt[0] in SCRIPTS[script]
                        ]
                        seed = RANDOM_SEED + i
                        filename = f"{Path(script).stem}_{Path(network).stem}_k{k}_ed{ed}_r{seed}_{'_'.join(opt[0][1:] + opt[1] for opt in valid_options)}"

                        # avoid testing the same cmds
                        if filename not in tested_cmds:
                            tested_cmds.add(filename)
                        else:
                            continue

                        print(
                            f"   {script} {network} k={k} ED={ed} seed={seed}",
                            flush=True,
                        )
                        cmd_options = [v for v in chain.from_iterable(valid_options) if v != ""]
                        res = run_once(
                            script,
                            network,
                            k,
                            ed,
                            seed,
                            cmd_options,
                            filename,
                        )
                        if res["status"] != 0:
                            print(
                                f"\t[!] run {i + 1} failed ({res['status']}); see {OUTPUT_LOGS / (filename + '.log')}"
                            )
                        else:
                            print(
                                f"\t{res['wall']:.2f}s wall, {res['clusters']} clusters"
                            )
                        scripts_runs[script].append(res)
                        scripts_options[script] = " ".join(cmd_options)
                rows = [
                    summarize(
                        s,
                        network,
                        k,
                        ed,
                        get_option_value("-X", s, filtered_options, "0"),
                        scripts_options[s],
                        scripts_runs[s],
                    )
                    for s in SCRIPTS
                    if scripts_runs[s]
                ]
                writer.writerows(rows)
                f.flush()
    print("Benchmark ended.\nSee summary:", OUTPUT_SUMMARY)


if __name__ == "__main__":
    benchmark()

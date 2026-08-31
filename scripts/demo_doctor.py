#!/usr/bin/env python3
"""Pre-flight check: prints a green/red checklist and exits non-zero if
anything is broken. The last check runs the live question's gold traversal
in pure Gremlin with no LLM, confirming the graph is intact and reachable.

    python scripts/demo_doctor.py                # full check
    python scripts/demo_doctor.py --offline      # fallback assets only
    python scripts/demo_doctor.py --skip-llm     # everything except quota probe
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data"
CORPUS = DATA / "corpus"

GREEN, RED, YELLOW, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[0m"
results: list[tuple[str, bool, str]] = []

GREMLIN_URL = "ws://gremlin:8182/gremlin"


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    mark = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
    print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))
    return ok


def warn(name: str, detail: str = "") -> None:
    print(f"  [{YELLOW}WARN{RESET}] {name}" + (f" — {detail}" if detail else ""))


def compose(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", *args], cwd=REPO, capture_output=True,
        text=True, timeout=timeout,
    )


def worker(script: str, timeout: int = 120, **env: str) -> subprocess.CompletedProcess:
    """Run a snippet inside the Airflow worker, where the dependencies live.

    Talks to service clients directly rather than via Airflow Hooks, since
    BaseHook.get_connection needs task context outside a running task.
    """
    args: list[str] = []
    for key, value in env.items():
        args += ["-e", f"{key}={value}"]
    return compose("exec", "-T", *args, "airflow-worker", "python", "-c", script,
                   timeout=timeout)


def conn_extra(conn_id: str) -> dict:
    """Read a connection's extra via the CLI, which reads the metadata DB."""
    proc = compose("exec", "-T", "airflow-worker", "airflow", "connections",
                   "get", conn_id, "-o", "json")
    if proc.returncode != 0:
        return {}
    start = proc.stdout.find("[")
    if start < 0:
        return {}
    try:
        return json.loads(proc.stdout[start:])[0].get("extra_dejson") or {}
    except (json.JSONDecodeError, IndexError, KeyError):
        return {}


GREMLIN_SNIPPET = """
import os
from gremlin_python.driver.client import Client
c = Client(os.environ["URL"], "g")
try:
    print(c.submit(os.environ["Q"]).all().result())
finally:
    c.close()
"""

PGVECTOR_SNIPPET = """
import psycopg2
c = psycopg2.connect(host="vectordb", user="vector", password="vector", dbname="vectordb")
cur = c.cursor()
cur.execute("SELECT to_regclass('crm_chunks')")
if cur.fetchone()[0] is None:
    print("MISSING_TABLE")
else:
    cur.execute("SELECT count(*), min(vector_dims(embedding)) FROM crm_chunks")
    rows, dim = cur.fetchone()
    print(f"{rows or 0} {dim or 0}")
"""

GEMINI_SNIPPET = """
import json, os, urllib.error, urllib.request
model = os.environ["MODEL"].split(":", 1)[-1]
url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
body = {"contents": [{"parts": [{"text": "Reply with the single word: ready"}]}]}
req = urllib.request.Request(
    url, method="POST", data=json.dumps(body).encode(),
    headers={"x-goog-api-key": os.environ["KEY"], "Content-Type": "application/json"},
)
try:
    print(urllib.request.urlopen(req, timeout=30).status)
except urllib.error.HTTPError as e:
    print("HTTP", e.code, e.read()[:200].decode("utf8", "replace"))
"""


def check_services() -> None:
    print("\nServices")
    proc = compose("ps", "--format", "json")
    if not check("docker compose reachable", proc.returncode == 0,
                 (proc.stderr or "").strip().splitlines()[-1] if proc.returncode else ""):
        return
    states = {}
    for line in proc.stdout.splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        states[row.get("Service", "?")] = row.get("State", "?")
    for name in ("postgres", "redis", "vectordb", "gremlin", "airflow-apiserver",
                 "airflow-scheduler", "airflow-worker", "airflow-dag-processor"):
        check(f"service {name}", states.get(name) == "running",
              states.get(name, "missing"))


def check_airflow() -> None:
    print("\nAirflow")
    proc = compose("exec", "-T", "airflow-worker", "airflow", "version")
    check("airflow responds", proc.returncode == 0, proc.stdout.strip())

    proc = compose("exec", "-T", "airflow-worker", "airflow", "connections", "list",
                   "--output", "json")
    conn_ids = set()
    if proc.returncode == 0:
        try:
            conn_ids = {c["conn_id"] for c in json.loads(proc.stdout)}
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
    for conn in ("gremlin", "gemini_default", "vectordb"):
        check(f"connection {conn}", conn in conn_ids)

    proc = compose("exec", "-T", "airflow-worker", "airflow", "pools", "list",
                   "--output", "json")
    pools = {}
    if proc.returncode == 0:
        try:
            pools = {p["pool"]: p for p in json.loads(proc.stdout)}
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
    check("pool gemini_free exists", "gemini_free" in pools,
          f"slots={pools.get('gemini_free', {}).get('slots', '?')}")

    proc = compose("exec", "-T", "airflow-worker", "airflow", "dags", "list",
                   "--output", "json")
    dag_ids = set()
    if proc.returncode == 0:
        try:
            dag_ids = {d["dag_id"] for d in json.loads(proc.stdout)}
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
    for dag_id in ("crm_corpus_generate", "kg_build", "vector_build",
                   "rag_benchmark", "rag_showdown_live", "crm_reachability"):
        check(f"dag {dag_id} parses", dag_id in dag_ids)

    proc = compose("exec", "-T", "airflow-worker", "airflow", "dags",
                   "list-import-errors", "--output", "json")
    has_errors = proc.returncode == 0 and proc.stdout.strip() not in ("", "[]")
    check("no DAG import errors", not has_errors,
          proc.stdout.strip()[:200] if has_errors else "")


def check_gremlin() -> None:
    print("\nGraph")
    proc = worker(GREMLIN_SNIPPET, URL=GREMLIN_URL,
                  Q="g.V().groupCount().by(label())")
    ok = proc.returncode == 0 and "Customer" in proc.stdout
    check("gremlin round-trip", ok,
          proc.stdout.strip()[:120] if ok else (proc.stderr or "").strip()[-200:])
    if not ok:
        warn("recover with", "./scripts/reload_kg.sh")
        return

    proc = worker(GREMLIN_SNIPPET, URL=GREMLIN_URL,
                  Q="g.V().hasLabel('Customer').count()")
    count = proc.stdout.strip().strip("[]") if proc.returncode == 0 else "?"
    check("70 Customer vertices", count == "70", f"found {count}")


def check_vectors() -> None:
    print("\nVector store")
    proc = worker(PGVECTOR_SNIPPET)
    if proc.returncode != 0:
        check("pgvector reachable", False, (proc.stderr or "").strip()[-200:])
        return
    check("pgvector reachable", True)

    out = proc.stdout.strip()
    if "MISSING_TABLE" in out:
        check("pgvector populated", False, "crm_chunks does not exist — run vector_build")
        return
    rows, dim = (out.split() + ["0", "0"])[:2]
    check("pgvector populated", int(rows) > 0, f"{rows} chunks")
    check("embedding dim 768", dim == "768", dim)


def check_llm() -> None:
    print("\nGemini")
    extra = conn_extra("gemini_default")
    key, model = extra.get("api_key"), extra.get("model", "")
    if not check("gemini_default carries an api_key", bool(key),
                 "" if key else "re-run ./setup-demo.sh with GEMINI_API_KEY set in .env"):
        return
    # pydantic-ai 2.x: use "google:" prefix, not the older "google-gla:".
    ok_prefix = model.startswith("google:")
    check("model prefix is current", ok_prefix,
          model if ok_prefix else f"{model!r} — expected google:<model>")

    proc = worker(GEMINI_SNIPPET, timeout=90, KEY=key,
                  MODEL=model or "google:gemini-2.5-flash")
    out = (proc.stdout or "").strip()
    ok = proc.returncode == 0 and out.startswith("200")
    detail = "" if ok else out[:160]
    if "429" in out:
        detail = "RATE LIMITED (429) — swap to the backup key before presenting"
    check("LLM responds", ok, detail)


def check_gold_traversal(question_id: str) -> None:
    """Answer the live question with Gremlin only, no LLM."""
    print(f"\nLive question ({question_id})")
    questions_path = CORPUS / "questions.json"
    if not check("questions.json present", questions_path.exists()):
        return
    questions = json.loads(questions_path.read_text())
    q = next((x for x in questions if x["id"] == question_id), None)
    if not check(f"{question_id} exists", q is not None):
        return
    if not q["gremlin"]:
        warn(f"{question_id} has no traversal", "B5 questions are prose-only")
        return

    proc = worker(GREMLIN_SNIPPET, URL=GREMLIN_URL, Q=q["gremlin"])
    if not check("gold traversal runs", proc.returncode == 0,
                 (proc.stderr or "").strip()[-200:]):
        return
    got = proc.stdout.strip()
    expected = q["gold_entity_ids"] or [str(q["gold_scalar"])]
    hit = all(str(e).strip("'\"") in got for e in expected)
    check("traversal returns the gold answer", hit,
          f"{len(expected)} entities" if hit else f"expected {expected} | got {got[:120]}")


def check_offline_assets() -> None:
    print("\nOffline fallback")
    seed = DATA / "seed"
    check("seed kg_edges.json", (seed / "kg_edges.json").exists(),
          "run kg_build with use_cached_extraction=false, then copy it — "
          "see data/seed/README.md")
    check("seed vector dump", (seed / "initdb" / "02_chunks.sql").exists(),
          "run scripts/dump_vectors.sh")
    check("scorecard.json", (seed / "scorecard.json").exists()
          or (DATA / "results" / "scorecard.json").exists(),
          "run rag_benchmark")
    check("scorecard.png", (seed / "scorecard.png").exists()
          or (DATA / "results" / "scorecard.png").exists())
    check("corpus present", (CORPUS / "docs.json").exists())
    fallback = REPO / "fallback"
    if not (fallback.exists() and any(fallback.iterdir())):
        warn("no fallback/ recording", "record rag_showdown_live succeeding (60-90s)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offline", action="store_true",
                    help="Only check the committed fallback assets.")
    ap.add_argument("--skip-llm", action="store_true",
                    help="Skip the Gemini call (saves free-tier quota).")
    ap.add_argument("--question", default="B3-01",
                    help="Question the live demo will run.")
    args = ap.parse_args()

    print("=" * 64)
    print("  DEMO DOCTOR")
    print("=" * 64)

    if args.offline:
        check_offline_assets()
    else:
        check_services()
        check_airflow()
        check_gremlin()
        check_vectors()
        if not args.skip_llm:
            check_llm()
        check_gold_traversal(args.question)
        check_offline_assets()

    failed = [name for name, ok, _ in results if not ok]
    print("\n" + "=" * 64)
    if failed:
        print(f"  {RED}NOT READY{RESET} — {len(failed)} of {len(results)} checks failed")
        for name in failed:
            print(f"    - {name}")
        print("=" * 64)
        return 1
    print(f"  {GREEN}SAFE TO PRESENT{RESET} — {len(results)} checks passed")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

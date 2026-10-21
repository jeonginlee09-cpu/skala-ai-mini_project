"""Fresh assignment-vs-Reviewer comparison; preserve every success and refusal."""
import argparse
import asyncio
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
from datetime import datetime

import httpx
import benchmark_three_versions as common

ROOT = common.ROOT
OUT = ROOT / "evidence/reviewer_comparison_20261021"
VARIANTS = ["original", "latest"]
PORTS = {"original": 18250, "latest": 18251}


def snapshot(name, ref):
    if name == "original":
        return common.snapshot_source(name, ref)
    dest = OUT / "stages" / name
    dest.mkdir(parents=True, exist_ok=False)
    archive = subprocess.check_output(["git", "archive", ref], cwd=ROOT)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(dest, filter="data")
    shutil.copytree(ROOT / ".runbook_index", dest / ".runbook_index")
    files = {str(p.relative_to(dest)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in dest.rglob("*") if p.is_file() and ".runbook_index" not in p.parts}
    return dest, files


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--latest-ref", default="HEAD")
    args = parser.parse_args()
    if OUT.exists():
        raise RuntimeError("Previous measurement must be preserved; choose a fresh output path")
    OUT.mkdir(parents=True)
    (OUT / "telemetry").mkdir()
    common.OUT, common.PORTS = OUT, PORTS
    ref = subprocess.check_output(["git", "rev-parse", args.latest_ref], cwd=ROOT, text=True).strip()
    cases = json.loads((ROOT / "evidence/test_cases.json").read_text())
    manifest = {"started_at": datetime.now().astimezone().isoformat(), "rounds": 3,
        "python": sys.version, "ports": PORTS, "latest_commit": ref, "sources": {},
        "conditions": [
            "Same 11 inputs three times per version, 33 requests each; 24 incident and 9 chat inputs.",
            "Real OpenAI, FastAPI/SSE and MCP stdio; fixed class sample, jitter=0, timestamp=2026-10-21T09:00:00+09:00.",
            "Artificial first-call failure disabled equally; isolated source copies and conversation databases.",
            "Latest starts with an independent warm RAG cache. Index/cache construction costs excluded.",
            "Order alternates per input and round; all outcomes including ReviewerFailed remain in denominators.",
            "Conditional Reviewer enabled for latest; original has no Reviewer. Provider usage includes Reviewer.",
            "Q08 keeps existing auth-service fallback; target-policy match is not billing-service diagnosis accuracy.",
            "No new streaming-specific tests or first-token metrics; total request latency only.",
            "Old experiments are preserved and are not substituted into this comparison.",
            "No actual operational action executes; independent human answer grading and actual invoice remain unmeasured."]}
    processes, logs = [], []
    try:
        for name in VARIANTS:
            source, files = snapshot(name, ref)
            manifest["sources"][name] = files
            log = (OUT / (name + "_server.log")).open("w")
            logs.append(log)
            processes.append(subprocess.Popen([sys.executable, "-u", str(ROOT / "tools/serve_comparison_variant.py"),
                "--source", str(source), "--variant", name, "--port", str(PORTS[name]),
                "--out", str(OUT / "telemetry"), "--trace-project-prefix", "capstone-reviewer-comparison"],
                cwd=source, stdout=log, stderr=subprocess.STDOUT,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "REVIEWER_MODE": "conditional"}))
        async with httpx.AsyncClient(trust_env=False, timeout=180) as client:
            for name in VARIANTS:
                for _ in range(100):
                    try:
                        response = await client.get(f"http://127.0.0.1:{PORTS[name]}/health", timeout=2)
                        response.raise_for_status()
                        manifest.setdefault("health", {})[name] = response.json()
                        break
                    except Exception:
                        await asyncio.sleep(.25)
                else:
                    raise RuntimeError("Startup failed: " + name)
            (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
            results = []
            for repeat in range(1, 4):
                for index, case in enumerate(cases):
                    order = VARIANTS if (index + repeat) % 2 else list(reversed(VARIANTS))
                    for name in order:
                        row = await common.collect(client, name, case, repeat)
                        events = [item["event"] for item in row["events"]]
                        failures = [e for e in events if e["type"] in ("ReviewerFailed", "ValidationFailed", "RequestFailed")]
                        if failures:
                            row["failure"] = failures[-1]
                        row["reviewer_events"] = [e for e in events if e["type"].startswith("Reviewer")]
                        # The canonical enriched record includes refusals, not only FinalAnswer outcomes.
                        (OUT / "runs" / row["request_id"] / "response.json").write_text(
                            json.dumps(row, ensure_ascii=False, indent=2))
                        results.append(row)
            (OUT / "all_results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for log in logs:
            log.close()


if __name__ == "__main__":
    asyncio.run(main())

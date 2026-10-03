#!/usr/bin/env python3
"""Measure what BuildBuddy adds to SWSS Common's existing build cache."""

import argparse
from collections import Counter
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import time
from urllib.parse import urlsplit
import uuid


COMMON_TARGETS = [
    "//:libswsscommon",
    "//:libswsscommon_shared",
    "//:libswsscommon_consolidated.so",
    "//:swssloglevel",
    "//crates/swss-common:bindings_dir",
    "//dist:libswsscommon_pkg",
    "//dist:libswsscommon_pkg.debug_symbols",
    "//dist:sonic-db-cli_pkg",
    "//pyext:swsscommon_pkg",
    "//goext:swsscommon",
    "//goext:swsscommon_runtime_test",
    "//tests:status_code_util_test",
    "//tests:saiaclschema_ut",
    "//tests:notification_queue_ut",
    "//tests:interface_ut",
    "//tests:vrf_ut",
    "//tests:shared_library_runtime_test",
    "//dist:libswsscommon_package_test",
    "//pyext:swsscommon_package_test",
]

PACKAGE_OUTPUTS = [
    "dist/libswsscommon_pkg.tar",
    "dist/libswsscommon_pkg.debug_symbols.tar",
    "dist/sonic-db-cli_pkg.tar",
    "pyext/swsscommon_pkg.tar.gz",
]


def targets(yang):
    result = list(COMMON_TARGETS)
    if yang:
        result.insert(0, "//common:cfg_schema_generated")
        result.insert(-2, "//tests:defaultvalueprovider_ut")
    return result


def child_environment(source):
    """Bazel records its client environment in BEP; do not pass CI credentials."""
    credential_name = re.compile(
        r"(?:^|_)(?:TOKEN|PASSWORD|SECRET|API_KEY|ACCESS_KEY|PRIVATE_KEY|CREDENTIALS?)(?:_|$)",
        re.IGNORECASE)
    environment, credentials = {}, []
    for name, value in source.items():
        if credential_name.search(name):
            if value:
                credentials.append(value)
        else:
            environment[name] = value
    return environment, credentials


def sanitize(data, secrets):
    """Remove literal and JSON-escaped credentials from retained text artifacts."""
    if isinstance(secrets, str):
        secrets = [secrets]
    for secret in sorted(set(filter(None, secrets)), key=len, reverse=True):
        for value in (secret, json.dumps(secret)[1:-1]):
            data = data.replace(value.encode(), b"[REDACTED]")
    return data


def omit_client_environment(value):
    """Drop every client_env option, including variables with innocuous names."""
    if isinstance(value, list):
        result, skip_next = [], False
        for item in value:
            if skip_next:
                skip_next = False
                continue
            if isinstance(item, str) and item == "--client_env":
                skip_next = True
                continue
            if isinstance(item, str) and item.startswith("--client_env="):
                continue
            if isinstance(item, dict) and item.get("optionName") == "client_env":
                continue
            result.append(omit_client_environment(item))
        return result
    if isinstance(value, dict):
        return {key: omit_client_environment(item) for key, item in value.items()}
    return value


def publish_artifact(source, destination, secret):
    if not source.exists():
        return False
    data = source.read_bytes()
    if data.startswith(b"\x1f\x8b"):
        data = gzip.decompress(data)
    if source.name.endswith(".bep.jsonl"):
        try:
            data = ("\n".join(json.dumps(omit_client_environment(json.loads(line)))
                              for line in data.decode().splitlines()) + "\n").encode()
        except (ValueError, UnicodeError):
            # Incomplete BEP cannot be safely filtered. Keep the sanitized log only.
            return False
    destination.write_bytes(sanitize(data, secret))
    return True


def bep_metrics(path):
    """Read Bazel 8's exact runner count; absent/incomplete evidence is unknown."""
    metrics = {"complete": False, "remote_cache_hits": None, "runner_counts": {}}
    if not path.exists():
        return metrics
    finished = False
    action_summary = None
    try:
        for line in path.read_text().splitlines():
            event = json.loads(line)
            if "finished" in event:
                finished = True
            candidate = event.get("buildMetrics", {}).get("actionSummary")
            if candidate is not None:
                action_summary = candidate
    except (ValueError, OSError):
        return metrics
    if not finished or action_summary is None or "runnerCount" not in action_summary:
        return metrics
    try:
        counts = {
            runner["name"]: int(runner.get("count", 0))
            for runner in action_summary.get("runnerCount", [])
        }
    except (ValueError, TypeError, KeyError):
        return metrics
    metrics.update(complete=True, runner_counts=counts,
                   remote_cache_hits=counts.get("remote cache hit", 0))
    return metrics


def execution_records(stream, chunk_size=1024 * 1024):
    """Stream Bazel 8's concatenated, pretty-printed SpawnExec JSON objects."""
    decoder = json.JSONDecoder()
    buffer, exhausted = "", False
    while True:
        buffer = buffer.lstrip()
        if buffer:
            if not buffer.startswith("{"):
                raise ValueError("Execution log contains a non-object record")
            try:
                record, end = decoder.raw_decode(buffer)
            except ValueError:
                if exhausted:
                    raise ValueError("Execution log is malformed or truncated") from None
            else:
                yield record
                buffer = buffer[end:]
                continue
        elif exhausted:
            return
        # Bound memory even for malformed logs. Normal records contain one spawn.
        if len(buffer) > 64 * 1024 * 1024:
            raise ValueError("Execution log record exceeds the 64 MiB parsing limit")
        chunk = stream.read(chunk_size)
        exhausted = not chunk
        buffer += chunk


def owning_repository(label):
    """Attribute the action owner, never its toolchain or input repositories."""
    if re.fullmatch(r"(?:@@?)?//[^:]*:.+", label):
        return "sonic-swss-common", "root"
    external = re.fullmatch(r"@@?([^/\s]+)//[^:]*:.+", label)
    if external:
        return external.group(1), "dependencies"
    return "<unknown>", "unknown"


def execution_log_metrics(source, destination, secrets, expected_remote_hits):
    """Retain only allowlisted spawn metadata and reconcile hits with the BEP.

    SpawnExec.runner and BEP runnerCount both originate in SpawnResult. Count
    records, not unique labels: one target can own many compilation spawns.
    Internal actions and persistent action-cache hits are absent from this log.
    """
    metrics = {
        "complete": False, "spawn_count": 0, "remote_cache_hits": 0,
        "bep_remote_cache_hits": expected_remote_hits, "runner_counts": {},
        "buckets": {bucket: 0 for bucket in ("root", "dependencies", "unknown")},
        "by_repository": {}, "by_mnemonic": {},
        "remote_cached_object_output_count": 0,
        "remote_cached_spawns_with_object_outputs": 0,
    }
    runners, mnemonics = Counter(), Counter()
    try:
        with source.open(encoding="utf-8") as stream, destination.open("wb") as retained:
            for record in execution_records(stream):
                label = record.get("targetLabel", "")
                mnemonic = record.get("mnemonic", "")
                runner = record.get("runner")
                hit = record.get("cacheHit")
                if (not all(isinstance(value, str) for value in (label, mnemonic, runner))
                        or not isinstance(hit, bool)):
                    raise ValueError("Execution log is missing valid spawn metadata")
                if runner == "remote cache hit" and not hit:
                    raise ValueError("Execution log remote-hit fields disagree")
                outputs = record.get("actualOutputs", [])
                if (not isinstance(outputs, list) or any(
                        not isinstance(item, dict) or not isinstance(item.get("path"), str)
                        for item in outputs)):
                    raise ValueError("Execution log has invalid output metadata")
                paths = [item["path"] for item in outputs]
                # Never publish commandArgs, environmentVariables, inputs or arbitrary fields.
                safe = {"target_label": label, "mnemonic": mnemonic, "runner": runner,
                        "cache_hit": hit, "actual_output_paths": paths}
                digest = record.get("digest")
                if isinstance(digest, dict):
                    safe["digest"] = {key: digest[key] for key in
                                      ("hash", "sizeBytes", "hashFunctionName")
                                      if isinstance(digest.get(key), (str, int))}
                retained.write(sanitize(json.dumps(safe).encode() + b"\n", secrets))
                metrics["spawn_count"] += 1
                runners[runner] += 1
                if runner != "remote cache hit":
                    continue
                repository, bucket = owning_repository(label)
                objects = sum(path.endswith(".o") for path in paths)
                metrics["remote_cache_hits"] += 1
                metrics["buckets"][bucket] += 1
                metrics["remote_cached_object_output_count"] += objects
                metrics["remote_cached_spawns_with_object_outputs"] += int(objects > 0)
                mnemonics[mnemonic or "<unknown>"] += 1
                group = metrics["by_repository"].setdefault(repository, {
                    "bucket": bucket, "remote_cache_hits": 0, "by_mnemonic": {},
                    "remote_cached_object_output_count": 0,
                    "remote_cached_spawns_with_object_outputs": 0,
                })
                group["remote_cache_hits"] += 1
                group["remote_cached_object_output_count"] += objects
                group["remote_cached_spawns_with_object_outputs"] += int(objects > 0)
                name = mnemonic or "<unknown>"
                group["by_mnemonic"][name] = group["by_mnemonic"].get(name, 0) + 1
        metrics["runner_counts"] = dict(runners)
        metrics["by_mnemonic"] = dict(mnemonics)
        if expected_remote_hits is None or metrics["remote_cache_hits"] != expected_remote_hits:
            raise ValueError("Execution-log remote cache hits do not match complete BEP evidence")
        metrics["complete"] = True
    except (OSError, ValueError, UnicodeError) as error:
        metrics["error"] = sanitize(str(error).encode(), secrets).decode()
        destination.unlink(missing_ok=True)
    return metrics


def package_differences(reference, candidate, check):
    differences = []
    for expected, observed in zip(reference["invocations"], candidate["invocations"]):
        for package, digest in expected["package_sha256"].items():
            actual = observed["package_sha256"].get(package)
            if digest != actual:
                differences.append({
                    "check": check, "reference_case": reference["name"],
                    "candidate_case": candidate["name"], "yang": expected["yang"],
                    "package": package, "reference_sha256": digest,
                    "candidate_sha256": actual,
                })
    return differences


def execute(command, cwd, environment, log, timeout):
    started = time.monotonic()
    timed_out = False
    with log.open("wb") as output:
        try:
            process = subprocess.Popen(command, cwd=cwd, env=environment,
                                       stdout=output, stderr=subprocess.STDOUT,
                                       start_new_session=True)
        except OSError as error:
            output.write(f"Could not start Bazel: {error}\n".encode())
            return {"exit_code": 127, "wall_seconds": time.monotonic() - started,
                    "timed_out": False}
        try:
            code = process.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            timed_out = True
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            code = 124
    return {"exit_code": code, "wall_seconds": time.monotonic() - started,
            "timed_out": timed_out}


def cache_flags(case, endpoint, instance, disk_cache=None):
    remote = case in ("populate", "remote", "combined")
    return [
        f"--disk_cache={disk_cache or ''}",
        "--remote_executor=",
        "--bes_backend=",
        f"--remote_cache={endpoint if remote else ''}",
        f"--remote_instance_name={instance if remote else ''}",
        f"--remote_accept_cached={'true' if case in ('remote', 'combined') else 'false'}",
        f"--remote_upload_local_results={'true' if case == 'populate' else 'false'}",
        "--remote_cache_async=false",
        "--remote_download_outputs=all",
    ]


def run(args, secret):
    if args.mode == "remote" and not args.remote_instance_name:
        raise ValueError("remote mode requires --remote-instance-name")
    workspace = Path(__file__).resolve().parents[2]
    output = args.output_dir.resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("--output-dir must be absent or empty; use a new directory per run")
    output.mkdir(parents=True, exist_ok=True)
    repository_cache = args.repository_cache.expanduser().resolve()
    repository_cache.mkdir(parents=True, exist_ok=True)
    environment, credentials = child_environment(os.environ)
    credentials.append(secret)
    run_id = uuid.uuid4().hex
    args.started_run_id = run_id
    instance = (args.remote_instance_name if args.mode == "remote" else
                f"sonic-swss-common/cache-pilot/{args.arch.lower()}/{run_id}")
    collect_execution = args.mode == "remote" or args.execution_log
    summary = {
        "schema_version": 1, "mode": args.mode, "arch": args.arch,
        "run_id": run_id, "remote_instance_name": instance if args.mode != "baseline" else None,
        "remote_cache": args.remote_cache if args.mode != "baseline" else None,
        "host_machine": platform.machine(),
        "git_revision": subprocess.check_output(
            ["git", "-c", f"safe.directory={workspace}", "rev-parse", "HEAD"],
            cwd=workspace, text=True).strip(),
        "bazel_version": (workspace / ".bazelversion").read_text().strip(),
        "repository_cache": str(repository_cache),
        "baseline_cache": ("disabled" if args.mode == "remote" else
                           "restored_disk" if args.disk_cache else "cold"),
        "execution_log_enabled": collect_execution,
        "purpose": "cache_action_attribution" if args.mode == "remote" else "timing_comparison",
        "test_results_reused": False,
        "preparation": [], "cases": [], "comparison": None,
        "status": "running",
    }
    if args.disk_cache and args.mode != "remote":
        snapshot = {"files": 0, "bytes": 0}
        for path in args.disk_cache.rglob("*"):
            if path.is_file():
                snapshot["files"] += 1
                snapshot["bytes"] += path.stat().st_size
        summary["disk_cache_snapshot"] = snapshot

    def save():
        data = json.dumps(summary, indent=2).encode() + b"\n"
        (output / "summary.json").write_bytes(sanitize(data, credentials))

    # Raw evidence and the authentication rc are never inside the upload directory.
    with tempfile.TemporaryDirectory(prefix="swss-cache-benchmark-") as temporary:
        private = Path(temporary)
        auth_rc = private / "auth.bazelrc"
        if secret:
            descriptor = os.open(auth_rc, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as stream:
                stream.write("common " + shlex.quote(
                    f"--remote_header=x-buildbuddy-api-key={secret}") + "\n")
        save()

        def invoke(case, yang, fetch=False):
            feature = "yang" if yang else "no-yang"
            stem = f"{case}-{feature}"
            raw_log, raw_bep, raw_profile = [private / (stem + suffix) for suffix in
                                              (".log", ".bep.jsonl", ".profile.json")]
            raw_execution = private / (stem + ".execution.json")
            startup = [args.bazel, "--batch", "--nosystem_rc", "--nohome_rc",
                       f"--output_user_root={private / 'user-root'}",
                       f"--output_base={private / case}"]
            if case in ("populate", "remote", "combined"):
                startup.append(f"--bazelrc={auth_rc}")
            options = ["--config=aarch64"] if args.arch == "ARM64" else []
            options += [f"--//tools/bazel:yang_modules={yang}",
                        f"--repository_cache={repository_cache}", "--lockfile_mode=update"]
            if fetch:
                # Fetch performs no build actions and is outside the measured cases.
                options += ["--remote_cache=", "--remote_executor=", "--bes_backend="]
            else:
                disk_cache = private / f"{case}-disk" if args.disk_cache and case in (
                    "baseline", "combined") else None
                options += cache_flags(case, args.remote_cache, instance, disk_cache)
                options += ["--nocache_test_results", "--test_output=errors",
                            f"--build_event_json_file={raw_bep}", f"--profile={raw_profile}"]
                if collect_execution:
                    options += [f"--execution_log_json_file={raw_execution}",
                                "--execution_log_sort=false"]
            command = startup + ["fetch" if fetch else "test"] + options + targets(yang)
            print(f"{case}: {feature}", flush=True)
            result = execute(command, workspace, environment, raw_log, args.timeout_seconds)
            result.update(yang=yang, targets=targets(yang), artifacts={})
            for label, source in (("log", raw_log), ("bep", raw_bep), ("profile", raw_profile)):
                if publish_artifact(source, output / source.name, credentials):
                    result["artifacts"][label] = source.name
            if not fetch:
                result["cache_metrics"] = bep_metrics(output / raw_bep.name)
                if collect_execution:
                    metadata = output / (stem + ".actions.jsonl")
                    result["action_attribution"] = execution_log_metrics(
                        raw_execution, metadata, credentials,
                        result["cache_metrics"]["remote_cache_hits"])
                    if metadata.exists():
                        result["artifacts"]["actions"] = metadata.name
                result["package_sha256"] = {}
                if result["exit_code"] == 0:
                    for name in PACKAGE_OUTPUTS:
                        package = workspace / "bazel-bin" / name
                        if not package.is_file():
                            result["exit_code"] = 1
                            result["error"] = f"Expected package is missing: {name}"
                            break
                        with package.open("rb") as stream:
                            digest = hashlib.sha256()
                            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                                digest.update(chunk)
                        result["package_sha256"][name] = digest.hexdigest()
            lock = workspace / "MODULE.bazel.lock"
            publish_artifact(lock, output / f"{stem}.MODULE.bazel.lock", credentials)
            return result

        for yang in (True, False):
            result = invoke("preparation", yang, fetch=True)
            summary["preparation"].append(result)
            save()
            if result["exit_code"]:
                summary["status"] = "preparation_failed"
                save()
                return 1

        shutil.rmtree(private / "preparation", ignore_errors=True)

        cases = ([args.mode] if args.mode in ("baseline", "remote") else
                 ["baseline", "populate", "remote"])
        if args.mode == "compare" and args.disk_cache:
            cases.append("combined")
        for case in cases:
            case_result = {"name": case, "invocations": [], "wall_seconds": 0}
            if args.disk_cache and case in ("baseline", "combined"):
                started = time.monotonic()
                shutil.copytree(args.disk_cache, private / f"{case}-disk")
                case_result["disk_cache_copy_seconds"] = time.monotonic() - started
            summary["cases"].append(case_result)
            for yang in (True, False):
                result = invoke(case, yang)
                case_result["invocations"].append(result)
                case_result["wall_seconds"] += result["wall_seconds"]
                save()
                if result["exit_code"] or not result["cache_metrics"]["complete"]:
                    summary["status"] = "build_failed" if result["exit_code"] else "evidence_incomplete"
                    save()
                    return 1
                if collect_execution and not result["action_attribution"]["complete"]:
                    summary["status"] = "execution_log_incomplete"
                    save()
                    return 1
            # Keep only evidence after each case; sysroots otherwise exhaust CI disks.
            shutil.rmtree(private / case, ignore_errors=True)
            shutil.rmtree(private / f"{case}-disk", ignore_errors=True)
        if args.mode == "remote":
            hits = sum(call["cache_metrics"]["remote_cache_hits"]
                       for call in summary["cases"][0]["invocations"])
            summary["remote_reuse_observed"] = hits > 0
            if not hits:
                summary["status"] = "remote_cache_not_verified"
                save()
                return 1
        if args.mode == "compare":
            baseline, remote = summary["cases"][0], summary["cases"][-1]
            populate, remote_only = summary["cases"][1:3]
            hits = sum(call["cache_metrics"]["remote_cache_hits"]
                       for call in remote["invocations"])
            diagnostic_hits = sum(call["cache_metrics"]["remote_cache_hits"]
                                  for call in remote_only["invocations"])
            summary["remote_reuse_observed"] = hits > 0
            summary["package_mismatches"] = []
            for check, field, reference, candidate in (
                ("primary", "primary_output_match", baseline, remote),
                ("remote_roundtrip", "remote_roundtrip_match", populate, remote_only),
                ("cross_cache", "cross_cache_output_match", baseline, populate),
            ):
                differences = package_differences(reference, candidate, check)
                summary[field] = not differences
                summary["package_mismatches"].extend(differences)
            summary["package_hashes_match"] = not summary["package_mismatches"]
            if diagnostic_hits:
                # Preserve the observed pair even when a separate cache family differs.
                summary["comparison"] = {
                    "baseline_case": "baseline", "remote_case": remote["name"],
                    "baseline_seconds": baseline["wall_seconds"],
                    "remote_seconds": remote["wall_seconds"],
                    "saved_seconds": baseline["wall_seconds"] - remote["wall_seconds"],
                    "speedup": baseline["wall_seconds"] / remote["wall_seconds"]
                    if hits and summary["primary_output_match"] and summary["remote_roundtrip_match"]
                    else None,
                    "remote_cache_hits": hits,
                    "remote_only_cache_hits": diagnostic_hits,
                }
            if not summary["package_hashes_match"] or diagnostic_hits == 0:
                summary["status"] = ("package_outputs_differ" if not summary["package_hashes_match"]
                                     else "remote_cache_not_verified")
                save()
                return 1
        summary["status"] = "complete"
        save()
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("baseline", "compare", "remote"), required=True)
    parser.add_argument("--arch", choices=("AMD64", "ARM64"), required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--repository-cache", type=Path,
                        default=Path.home() / ".cache" / "bazel-repo-cache")
    parser.add_argument("--disk-cache", type=Path,
                        help="Compare independent copies of this restored GitHub disk cache")
    parser.add_argument("--bazel", default="bazel")
    parser.add_argument("--remote-cache", default="grpcs://remote.buildbuddy.io")
    parser.add_argument("--remote-instance-name",
                        help="Existing seeded namespace; required only for remote mode")
    parser.add_argument("--execution-log", action="store_true",
                        help="Attribute cached spawns by owner; always enabled in remote mode")
    parser.add_argument("--timeout-seconds", type=int, default=3600)
    args = parser.parse_args()
    secret = os.environ.get("BUILDBUDDY_API_KEY", "") if args.mode != "baseline" else ""
    if args.mode != "baseline" and not secret:
        parser.error("compare and remote modes require BUILDBUDDY_API_KEY; baseline needs no key")
    if args.mode == "remote":
        if not args.remote_instance_name:
            parser.error("remote mode requires --remote-instance-name")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", args.remote_instance_name):
            parser.error("--remote-instance-name must be a nonempty cache namespace path")
        if args.disk_cache:
            parser.error("remote mode disables disk caching; omit --disk-cache")
    elif args.remote_instance_name:
        parser.error("--remote-instance-name is only accepted in remote mode")
    if secret and (not secret.isascii() or not secret.isprintable() or
                   any(character.isspace() for character in secret)):
        parser.error("BUILDBUDDY_API_KEY must contain only non-whitespace ASCII characters")
    endpoint = urlsplit(args.remote_cache)
    if (endpoint.scheme not in ("grpcs", "https") or not endpoint.hostname or
            endpoint.username or endpoint.password or endpoint.query or endpoint.fragment):
        parser.error("--remote-cache must be a TLS endpoint without embedded credentials")
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive")
    if args.disk_cache and not args.disk_cache.is_dir():
        parser.error("--disk-cache must be an existing directory (an empty cache is allowed)")
    try:
        return run(args, secret)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        _, credentials = child_environment(os.environ)
        message = sanitize(f"Benchmark failed: {error}".encode(), credentials + [secret]).decode()
        print(message)
        summary_path = args.output_dir / "summary.json"
        if summary_path.exists():
            summary = json.loads(summary_path.read_text())
            if (summary.get("status") == "running" and
                    summary.get("run_id") == getattr(args, "started_run_id", None)):
                summary.update(status="harness_failed", error=message)
                summary_path.write_text(json.dumps(summary, indent=2) + "\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

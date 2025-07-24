#!/usr/bin/env python

import argparse
import json
import os
import secrets
import shlex
import subprocess
import sys
import urllib.request

from collections.abc import Sequence
from itertools import chain
from typing import Any
from urllib.error import HTTPError

import fuzzydate

from models import Component
from models import Snapshot
from pydantic import ValidationError


def get_check_run_identifier() -> str:
    """Get a shortened check_run_id"""

    check_run_id = os.environ.get("CHECK_RUN_ID", "")
    if check_run_id.isdigit():
        return check_run_id[:5]

    return "1"


def get_component_options(components: list[Component], pr_number: str | None = None) -> list[str]:
    prefix = f"pr-{pr_number}-" if pr_number else ""
    check_run_id = get_check_run_identifier()
    result = []

    for component in components:
        component_name = os.environ.get("BONFIRE_COMPONENT_NAME") or component.name
        revision = component.source.git.revision[:7]
        image = component.container_image.image

        result.extend((
            "--set-template-ref",
            f"{component_name}={component.source.git.revision}",
            "--set-parameter",
            f"{component_name}/IMAGE={image}",
            "--set-parameter",
            f"{component_name}/IMAGE_TAG={prefix}{revision}",
            "--set-parameter",
            f"{component_name}/DBM_IMAGE={image}",
            "--set-parameter",
            f"{component_name}/DBM_IMAGE_TAG={prefix}{revision}",
            "--set-parameter",
            f"{component_name}/DBM_INVOCATION={secrets.randbelow(100)}",
        ))

        if component_name == "koku":
            result.extend((
                "--set-parameter",
                f"{component_name}/SCHEMA_SUFFIX=_{prefix}{revision}_{check_run_id}",
            ))
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("namespace", help="Reserved namespace used for deployment")
    parser.add_argument("requester", help="Pipeline run name")
    parser.add_argument("--check", "-c", action="store_true", help="Output command, do not run")

    return parser.parse_args()


def get_timeout(env_var: str, labels: set[str] | None = None) -> int:
    try:
        timeout = fuzzydate.to_seconds(os.environ.get(env_var, "2h"))
    except (TypeError, ValueError) as exc:
        print(f"{exc}. Using default value of 2h")
        timeout = 2 * 60 * 60

    # If the timeout was not set via env var (defaulted to 2h), and the PR
    # includes the "full-run-smoke-tests" label, apply an extended timeout of 8h
    # as a fallback safeguard to avoid premature termination during long runs.
    if labels and "full-run-smoke-tests" in labels:
        timeout = 8 * 60 * 60

    return int(timeout)


def get_pr_labels(
    pr_number: str,
    owner: str = "project-koku",
    repo: str = "koku",
) -> set[str]:
    if not pr_number:
        return set()

    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"
    try:
        with urllib.request.urlopen(url) as response:
            data = json.loads(response.read())
    except HTTPError as exc:
        sys.exit(f"Error {exc.code} retrieving {exc.url}.")

    return {item["name"] for item in data["labels"]}


def display(command: str | Sequence[Any]) -> None:
    if isinstance(command, str):
        quoted = [command]
    else:
        quoted = [shlex.quote(str(arg)) for arg in command]

    print(" ".join(quoted), flush=True)


def main() -> None:
    args = parse_args()
    namespace = args.namespace
    requester = args.requester

    snapshot_str = os.environ.get("SNAPSHOT", "")
    try:
        snapshot = Snapshot.model_validate_json(snapshot_str)
    except ValidationError:
        sys.exit(f"Missing or invalid SNAPSHOT: {snapshot_str}")

    owner, repo = snapshot.components[0].source.git.url.path.split("/")[1:]
    pr_number = os.environ.get("PR_NUMBER", "")
    labels = get_pr_labels(pr_number, owner=owner, repo=repo) if pr_number else []
    app_name = os.environ.get("APP_NAME")
    components = os.environ.get("COMPONENTS", "").split()
    components_arg = chain.from_iterable(("--component", component) for component in components)
    components_with_resources = os.environ.get("COMPONENTS_W_RESOURCES", "").split()
    components_with_resources_arg = chain.from_iterable(("--no-remove-resources", component) for component in components_with_resources)
    snapshot_components = {component.name for component in snapshot.components}
    deploy_frontends = os.environ.get("DEPLOY_FRONTENDS") or "false"
    deploy_timeout = get_timeout("DEPLOY_TIMEOUT", labels)
    extra_deploy_args = os.environ.get("EXTRA_DEPLOY_ARGS", "")
    optional_deps_method = os.environ.get("OPTIONAL_DEPS_METHOD", "hybrid")
    ref_env = os.environ.get("REF_ENV", "insights-production")

    if not pr_number:
        display("[INFO] No PR number found. Assuming this is a scheduled or manual test run.")
        display("[INFO] Proceeding with full smoke tests...")

    display("[INFO] Starting deploy. Label validation was already handled by Tekton.")

    for secret in ["koku-aws", "koku-gcp"]:
        cmd = f"oc get secret {secret} -o yaml -n ephemeral-base | grep -v '^\s*namespace:\s' | oc apply --namespace={namespace} -f -"
        display(cmd)
        subprocess.run(cmd, shell=True)

    command = [
        "bonfire", "deploy",
        app_name,
        "--source", "appsre",
        "--ref-env", ref_env,
        "--namespace", namespace,
        "--timeout", str(deploy_timeout),
        "--optional-deps-method", optional_deps_method,
        "--frontends", deploy_frontends,
        "--no-single-replicas",
        "--set-parameter", "rbac/MIN_REPLICAS=1",
        "--set-parameter", "trino/HIVE_PROPERTIES_FILE=glue.properties",
        "--set-parameter", "trino/GLUE_PROPERTIES_FILE=hive.properties",
        *components_arg,
        *components_with_resources_arg,
        *extra_deploy_args.split(),
        *get_component_options(snapshot.components, pr_number),
    ]  # fmt: off

    display(command)

    if args.check:
        sys.exit()

    subprocess.check_call(command, env=os.environ | {"BONFIRE_NS_REQUESTER": requester})


if __name__ == "__main__":
    main()

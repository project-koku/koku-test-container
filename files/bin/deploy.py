#!/usr/bin/env python

import argparse
import json
import os
import secrets
import shlex
import subprocess
import sys
import typing as t
import urllib.request
import uuid

from itertools import chain
from urllib.error import HTTPError

import fuzzydate

from pydantic import AnyUrl
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator
from pydantic import ValidationError


class Git(BaseModel):
    model_config = ConfigDict(frozen=True)

    url: AnyUrl
    revision: str


class Source(BaseModel):
    model_config = ConfigDict(frozen=True)

    git: Git


class ContainerImage(BaseModel):
    model_config = ConfigDict(frozen=True)

    image: str
    sha: str


class Component(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    container_image: ContainerImage = Field(alias="containerImage")
    source: Source

    @model_validator(mode="before")
    @classmethod
    def container_image_validator(cls, data: t.Any) -> t.Any:
        if not isinstance(data, t.MutableMapping):
            raise ValueError(f"{data} is not of mapping type")

        image, sha = data["containerImage"].split("@sha256:")
        data["containerImage"] = ContainerImage(image=image, sha=sha)
        return data


class Snapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    application: str
    components: list[Component]


def get_run_identifier() -> str:
    """Return the CHECK_RUN_ID used to identify this run.

    If CHECK_RUN_ID is unset or falsy, return a random string to ensure uniqueness.

    Example:
        CHECK_RUN_ID=31510716818 --> "31510716818"
        CHECK_RUN_ID not set     --> "c91a0f3d-4dbe-4b3b-9e5d-92b542f9f9f7"
    """
    check_run_id = os.environ.get("CHECK_RUN_ID")
    return str(check_run_id) if check_run_id else uuid.uuid4().hex[:8]


def get_component_options(components: list[Component], pr_number: str | None = None) -> list[str]:
    prefix = f"pr-{pr_number}-" if pr_number else ""
    build_number = get_run_identifier()

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
            result.append("--set-parameter")
            result.append(f"{component_name}/SCHEMA_SUFFIX=_{prefix}{revision}_{build_number}")

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

    if labels and "full-run-smoke-tests" in labels:
        timeout = 5 * 60 * 60

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


def display(command: str | t.Sequence[t.Any]) -> None:
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
    labels = get_pr_labels(pr_number, owner=owner, repo=repo)
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

    if "ok-to-skip-smokes" in labels:
        display("PR labeled to skip smoke tests")
        return

    if "koku" in snapshot_components:
        if "smokes-required" in labels and not any(label.endswith("smoke-tests") for label in labels):
            sys.exit("Missing smoke tests labels.")

        # Skip Konflux tests unless explicitly labeled.
        # This prevents tests from running in both Jenkins and Konflux and can be
        # removed when Konflux increases the integration test timeout and
        # Jenkins tests are disabled.
        #
        # https://issues.redhat.com/browse/KONFLUX-5449
        if "run-konflux-tests" not in labels:
            display("PR is not labeled to run tests in Konflux")
            return

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

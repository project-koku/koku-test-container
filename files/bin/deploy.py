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

from itertools import chain
from urllib.error import HTTPError

from pydantic import AnyUrl
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator


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


def get_component_options(pr_number: str) -> list[str]:
    snapshot_str = os.environ.get("SNAPSHOT")

    if snapshot_str is None:
        sys.exit("Missing SNAPSHOT")

    snapshot = Snapshot.model_validate_json(snapshot_str)

    prefix = ""
    if pr_number:
        prefix = f"pr-{pr_number}-"

    result = []
    for component in snapshot.components:
        component_name = os.environ.get("BONFIRE_COMPONENT_NAME") or component.name
        result.extend((
            "--set-template-ref", f"{component_name}={component.source.git.revision}",
            "--set-parameter", f"{component_name}/IMAGE={component.container_image.image}",
            "--set-parameter", f"{component_name}/IMAGE_TAG={prefix}{component.source.git.revision[:7]}",
            "--set-parameter", f"{component_name}/DBM_IMAGE={component.container_image.image}",
            "--set-parameter", f"{component_name}/DBM_IMAGE_TAG={prefix}{component.source.git.revision[:7]}",
            "--set-parameter", f"{component_name}/DBM_INVOCATION={secrets.randbelow(100)}",
        ))  # fmt: off

    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("namespace", help="Reserved namespace used for deployment")
    parser.add_argument("requester", help="Pipeline run name")

    return parser.parse_args()


def override_timeout_based_on_label(labels: set[str]) -> str:
    if "full-run-smoke-tests" in labels:
        return "5h"
    else:
        return os.environ.get("DEPLOY_TIMEOUT", 900)


def get_pr_labels(
    pr_number: str,
    owner: str = "project-koku",
    repo: str = "koku",
) -> set[str]:
    if not pr_number:
        set()

    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"
    try:
        with urllib.request.urlopen(url) as response:
            data = json.loads(response.read())
    except HTTPError as exc:
        sys.exit(f"Error {exc.code} retrieving {exc.url}.")

    labels = {item["name"] for item in data["labels"]}

    return labels


def main() -> None:
    args = parse_args()
    namespace = args.namespace
    requester = args.requester

    app_name = os.environ.get("APP_NAME")
    components = os.environ.get("COMPONENTS", "").split()
    components_arg = chain.from_iterable(("--component", component) for component in components)
    components_with_resources = os.environ.get("COMPONENTS_W_RESOURCES", "").split()
    components_with_resources_arg = chain.from_iterable(("--no-remove-resources", component) for component in components_with_resources)
    deploy_frontends = os.environ.get("DEPLOY_FRONTENDS") or "false"
    extra_deploy_args = os.environ.get("EXTRA_DEPLOY_ARGS", "")
    optional_deps_method = os.environ.get("OPTIONAL_DEPS_METHOD", "hybrid")
    ref_env = os.environ.get("REF_ENV", "insights-production")
    pr_number = os.environ.get("PR_NUMBER", "")
    labels = get_pr_labels(pr_number)
    deploy_timeout = override_timeout_based_on_label(labels)
    cred_params = []

    if "koku" in components:
        if "smokes-required" in labels and not any(label.endswith("-smoke-tests") for label in labels):
            sys.exit("Missing smoke tests labels.")

        # Credentials
        aws_credentials_eph = os.environ.get("AWS_CREDENTIALS_EPH")
        gcp_credentials_eph = os.environ.get("GCP_CREDENTIALS_EPH")
        oci_credentials_eph = os.environ.get("OCI_CREDENTIALS_EPH")
        oci_config_eph = os.environ.get("OCI_CONFIG_EPH")

        cred_params = [
            "--set-parameter", f"koku/AWS_CREDENTIALS_EPH={aws_credentials_eph}",
            "--set-parameter", f"koku/GCP_CREDENTIALS_EPH={gcp_credentials_eph}",
            "--set-parameter", f"koku/OCI_CREDENTIALS_EPH={oci_credentials_eph}",
            "--set-parameter", f"koku/OCI_CONFIG_EPH={oci_config_eph}",
        ]  # fmt: off

    command = [
        "bonfire", "deploy",
        "--source", "appsre",
        "--ref-env", ref_env,
        "--namespace", namespace,
        "--timeout", deploy_timeout,
        "--optional-deps-method", optional_deps_method,
        "--frontends", deploy_frontends,
        "--set-parameter", "rbac/MIN_REPLICAS=1",
        *cred_params,
        *components_arg,
        *components_with_resources_arg,
        *extra_deploy_args.split(),
        *get_component_options(pr_number),
        app_name,
    ]  # fmt: off

    if "ok-to-skip-smokes" in labels:
        print("PR labeled to skip smoke tests")
        return

    print(" ".join(shlex.quote(str(arg)) for arg in command), flush=True)

    subprocess.check_call(command, env=os.environ | {"BONFIRE_NS_REQUESTER": requester})


if __name__ == "__main__":
    main()

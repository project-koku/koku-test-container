#!/usr/bin/env python

import argparse
import json
import os
import secrets
import subprocess
import sys
import typing as t

from itertools import chain

from pydantic import BaseModel, ConfigDict, AnyUrl, Field, model_validator


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


def get_component_options() -> list[str]:
    pr_number = os.environ.get("PR_NUMBER")
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
        ))

    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("namespace", help="Reserved namespace used for deployment")
    parser.add_argument("requester", help="Pipeline run name")

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    namespace = args.namespace
    requester = args.requester

    app_name = os.environ.get("APP_NAME")
    deploy_timeout = os.environ.get("DEPLOY_TIMEOUT", 900)
    ref_env = os.environ.get("REF_ENV", "insights-production")
    deploy_frontends = os.environ.get("DEPLOY_FRONTENDS") or "false"
    optional_deps_method = os.environ.get("OPTIONAL_DEPS_METHOD", "hybrid")
    extra_deploy_args = os.environ.get("EXTRA_DEPLOY_ARGS", "")
    components = os.environ.get("COMPONENTS", "").split()
    components_with_resources = os.environ.get("COMPONENTS_W_RESOURCES", "").split()

    components_arg = chain.from_iterable(("--component", component) for component in components)
    components_with_resources_arg = chain.from_iterable(("--no-remove-resources", component) for component in components_with_resources)

    cred_params = []
    if "koku" in components:
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
        ]

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
        *get_component_options(),
        app_name,
    ]

    print(command)
    subprocess.check_call(command, env=os.environ | {"BONFIRE_NS_REQUESTER": requester})


if __name__ == "__main__":
    main()

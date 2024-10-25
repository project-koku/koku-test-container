#!/usr/bin/env python

import argparse
import json
import os
import sys
import typing as t
import urllib.request

from functools import cached_property
from textwrap import dedent

import sh

from sh import bonfire
from sh import oc


def get_pr_labels(
    pr_number: str,
    owner: str = "project-koku",
    repo: str = "koku"
) -> set[str]:
    if not pr_number:
        set()

    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"
    with urllib.request.urlopen(url) as response:
        if response.status == 200:
            data = json.loads(response.read())

    labels = {item["name"] for item in data["labels"]}

    return labels


class IQERunner:
    def __init__(self,
        namespace: str,
        requester: str,
        check: bool = False,
        pr_number: str = ""
    ) -> None:
        self.namespace = namespace
        self.requester = requester
        self.pod = None
        self.check = check
        self.pr_number = pr_number

        self.component_name = os.environ.get("BONFIRE_COMPONENT_NAME") or os.environ.get("COMPONENT_NAME")
        self.iqe_cji_timeout = os.environ.get("IQE_CJI_TIMEOUT", "10m")
        self.iqe_env = os.environ.get("IQE_ENV", "clowder_smoke")
        self.iqe_image_tag = os.environ.get("IQE_IMAGE_TAG", "")
        self.iqe_plugins = os.environ.get("IQE_PLUGINS", "")
        self.iqe_requirements = os.environ.get("IQE_REQUIREMENTS", "")
        self.iqe_requirements_priority = os.environ.get("IQE_REQUIREMENTS_PRIORITY", "")
        self.iqe_test_importance = os.environ.get("IQE_TEST_IMPORTANCE", "")
        self.selenium = os.environ.get("IQE_SELENIUM", "")

    @cached_property
    def selenium_arg(self) -> list[str]:
        return ["--selenium"] if self.selenium else []

    @cached_property
    def env(self) -> dict[str, str]:
        return os.environ | {"BONFIRE_NS_REQUESTER": self.requester}

    @cached_property
    def iqe_filter_expression(self) -> str:
        if iqe_filter_expression := os.environ.get("IQE_FILTER_EXPRESSION", ""):
            return iqe_filter_expression

        iqe_filter_expression = "test_api"
        if "aws-smoke-tests" in self.pr_labels:
            iqe_filter_expression = "test_api_aws or test_api_ocp_on_aws or test_api_cost_model_aws or test_api_cost_model_ocp_on_aws"
        elif "azure-smoke-tests" in self.pr_labels:
            iqe_filter_expression = "test_api_azure or test_api_ocp_on_azure or test_api_cost_model_azure or test_api_cost_model_ocp_on_azure"
        elif "gcp-smoke-tests" in self.pr_labels:
            iqe_filter_expression = "test_api_gcp or test_api_ocp_on_gcp or test_api_cost_model_gcp or test_api_cost_model_ocp_on_gcp"
        elif "oci-smoke-tests" in self.pr_labels:
            iqe_filter_expression = "test_api_oci or test_api_cost_model_oci"
        elif "ocp-smoke-tests" in self.pr_labels:
            iqe_filter_expression = (
                "(test_api_ocp or test_api_cost_model_ocp or aws_ingest_single or aws_ingest_multi) "
                "and not ocp_on_gcp and not ocp_on_azure and not ocp_on_cloud"
            )
        elif "cost-model-smoke-tests" in self.pr_labels:
            iqe_filter_expression = "test_api_cost_model or ocp_source_raw"
        elif any(item in self.pr_labels for item in ("hot-fix-smoke-tests", "full-run-smoke-tests", "smoke-tests")):
            iqe_filter_expression = "test_api"

        return iqe_filter_expression

    @cached_property
    def iqe_marker_expression(self) -> str:
        if iqe_marker_expression := os.environ.get("IQE_MARKER_EXPRESSION", ""):
            return iqe_marker_expression

        iqe_marker_expression = "cost_required"
        if "ocp-smoke-tests" in self.pr_labels:
            iqe_marker_expression = "cost_smoke and not cost_exclude_ocp_smokes"
        elif "hot-fix-smoke-tests" in self.pr_labels:
            iqe_marker_expression = "cost_hotfix"
        elif "smoke-tests" in self.pr_labels:
            iqe_marker_expression = "cost_required"

        return iqe_marker_expression

    @cached_property
    def pr_labels(self) -> set[str]:
        if self.check:
            return set(os.environ.get("PR_LABELS", "").split()) or {"hot-fix-smoke-tests", "bug"}

        return get_pr_labels(self.pr_number)

    @property
    def container(self) -> t.Any:
        if self.pod is None:
            return

        return oc([
            "get", "pod", self.pod,
            "--namespace", self.namespace,
            "--output", "jsonpath={.status.containerStatuses[0].name}",
        ])

    def run_pod(self) -> str:
        command = [
            "deploy-iqe-cji", self.component_name,
            "--marker", self.iqe_marker_expression,
            "--filter", self.iqe_filter_expression,
            "--image-tag", self.iqe_image_tag,
            "--requirements", self.iqe_requirements,
            "--requirements-priority", self.iqe_requirements_priority,
            "--test-importance", self.iqe_test_importance,
            "--plugins", self.iqe_plugins,
            "--env", self.iqe_env,
            "--cji-name", self.component_name,
            *self.selenium_arg,
            "--namespace", self.namespace,
        ]
        print(command, flush=True)
        print(' '.join(command), flush=True)

        if self.check:
            return 'some-pod'

        result = bonfire(*command, _tee=True, _out=sys.stdout, _err=sys.stderr)
        self.pod = result.rstrip()

        return self.pod

    def follow_logs(self):
        oc.logs(self.pod, namespace=self.namespace, container=self.container, follow=True,
            _out=sys.stdout,
            _err=sys.stderr,
            _timeout=self.iqe_cji_timeout,
        )

    def check_cji_jobs(self) -> None:
        data = oc.get(
            f"cji/{self.component_name}",
            output="json",
            namespace=self.namespace,
        )
        cji = json.loads(data)
        job_map = cji["status"]["jobMap"]
        if not all(v == "Complete" for v in job_map.values()):
            print(dedent(
                f"""
                Some jobs failed: {job_map}
                """
            ))
            sys.exit(1)

        print(dedent(
            f"""
                All jobs succeeded: {job_map}
            """
        ))

    def run(self) -> None:
        if "ok-to-skip-smokes" in self.pr_labels:
            print("PR labeled to skip smoke tests")
            return

        self.run_pod()

        try:
            self.follow_logs()
        except sh.TimeoutException:
            print(f"Test exceeded timeout {self.iqe_cji_timeout}")
            oc.delete.pod(self.pod, namespace=self.namespace, _ok_code=[0, 1])
        except sh.ErrorReturnCode as exc:
            print("Test command failed")
            print(exc)

        self.check_cji_jobs()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("namespace", help="Reserved namespace used for deployment")
    parser.add_argument("requester", help="Pipeline run name")
    parser.add_argument("--check", "-c", action="store_true", help="Output command, do not run")

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    namespace = args.namespace
    requester = args.requester
    pr_number = os.environ.get("PR_NUMBER", "")

    runner = IQERunner(namespace=namespace, requester=requester, check=args.check, pr_number=pr_number)
    runner.run()


if __name__ == "__main__":
    main()

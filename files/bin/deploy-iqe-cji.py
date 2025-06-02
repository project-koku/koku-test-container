#!/usr/bin/env python

import argparse
import json
import os
import sys
import typing as t

from functools import cached_property
from itertools import chain

import sh

from deploy import display
from deploy import get_pr_labels
from deploy import get_timeout
from models import Snapshot
from pydantic import ValidationError
from sh import bonfire
from sh import oc


class IQERunner:
    def __init__(
        self,
        namespace: str,
        requester: str,
        check: bool = False,
        pr_number: str = "",
    ) -> None:
        self.namespace = namespace
        self.requester = requester
        self.pod = None
        self.check = check
        self.pr_number = pr_number

        self.component_name = os.environ.get("BONFIRE_COMPONENT_NAME") or os.environ.get("COMPONENT_NAME")
        self.iqe_env = os.environ.get("IQE_ENV", "clowder_smoke")
        self.iqe_image_tag = os.environ.get("IQE_IMAGE_TAG", "")
        self.iqe_env_vars = os.environ.get("IQE_ENV_VARS", "").split()
        self.iqe_plugins = os.environ.get("IQE_PLUGINS", "")
        self.iqe_requirements = os.environ.get("IQE_REQUIREMENTS", "")
        self.iqe_requirements_priority = os.environ.get("IQE_REQUIREMENTS_PRIORITY", "")
        self.iqe_test_importance = os.environ.get("IQE_TEST_IMPORTANCE", "")
        self.pipeline_run_name = os.environ.get("PIPELINE_RUN_NAME")
        self.check_run_id = self.get_check_run_identifier
        self.selenium = os.environ.get("IQE_SELENIUM", "")

        snapshot_str = os.environ.get("SNAPSHOT", "")
        try:
            self.snapshot = Snapshot.model_validate_json(snapshot_str)
        except ValidationError:
            sys.exit(f"Missing or invalid SNAPSHOT: {snapshot_str}")

    @cached_property
    def get_check_run_identifier(self) -> str:
        """Get a numeric build identifier from CHECK_RUN_ID or fallback to '1'.
        This value must be an integer (Ibutsu).

        Example:
            CHECK_RUN_ID=31510716818 --> '31510'
            CHECK_RUN_ID=abcde       --> '1'
            CHECK_RUN_ID not set     --> '1'
        """
        check_run_id = os.environ.get("CHECK_RUN_ID", "")
        if check_run_id.isdigit():
            return check_run_id[:5]
        return "1"

    @cached_property
    def job_name(self) -> str:
        """Get the job name from the pipeline run name

        Example:
            koku-ci-5rxkp --> koku-ci
        """
        return self.pipeline_run_name.rsplit("-", 1)[0]

    @cached_property
    def schema_suffix(self) -> str:
        # assume the component we care about is first!
        revision = self.snapshot.components[0].source.git.revision[:7]
        prefix = f"pr-{self.pr_number}-" if self.pr_number else ""
        return f"SCHEMA_SUFFIX=_{prefix}{revision}_{self.check_run_id}"

    @cached_property
    def build_url(self) -> str:
        """Create a build URL for the pipeline run"""
        application = os.environ.get("APPLICATION")
        return f"https://konflux-ui.apps.stone-prd-rh01.pg1f.p1.openshiftapps.com/ns/cost-mgmt-dev-tenant/applications/{application}/pipelineruns/{self.pipeline_run_name}"

    @cached_property
    def selenium_arg(self) -> list[str]:
        return ["--selenium"] if self.selenium.lower() == "true" else []

    @cached_property
    def env(self) -> dict[str, str]:
        return os.environ | {"BONFIRE_NS_REQUESTER": self.requester}

    @cached_property
    def iqe_env_vars_arg(self) -> t.Iterable[str]:
        job_name = f"JOB_NAME={self.job_name}"
        build_number = f"BUILD_NUMBER={self.check_run_id}"
        build_url = f"BUILD_URL={self.build_url}"
        iqe_parallel_enabled = "IQE_PARALLEL_ENABLED=false"
        schema_suffix = self.schema_suffix
        env_var_params = [job_name, build_number, build_url, iqe_parallel_enabled, schema_suffix]
        return chain.from_iterable(("--env-var", var) for var in env_var_params)

    @cached_property
    def iqe_filter_expression(self) -> str:
        if iqe_filter_expression := os.environ.get("IQE_FILTER_EXPRESSION", ""):
            return iqe_filter_expression

        iqe_filter_expression = ""
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

        iqe_marker_expression = "cost_smoke"
        if "ocp-smoke-tests" in self.pr_labels:
            iqe_marker_expression = "cost_smoke and not cost_exclude_ocp_smokes"
        elif "hot-fix-smoke-tests" in self.pr_labels:
            iqe_marker_expression = "cost_hotfix"
        elif "smoke-tests" in self.pr_labels:
            iqe_marker_expression = "cost_required"

        return iqe_marker_expression

    @cached_property
    def iqe_cji_timeout(self) -> int:
        return get_timeout("IQE_CJI_TIMEOUT", self.pr_labels)

    @cached_property
    def pr_labels(self) -> set[str]:
        if self.check:
            return set(os.environ.get("PR_LABELS", "").split()) or {"run-konflux-tests", "hot-fix-smoke-tests", "bug"}

        return get_pr_labels(self.pr_number)

    @property
    def container(self) -> t.Any:
        if self.pod is None:
            return

        return oc([
            "get", "pod", self.pod,
            "--namespace", self.namespace,
            "--output", "jsonpath={.status.containerStatuses[0].name}",
        ])  # fmt: off

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
            *self.iqe_env_vars_arg,
            "--namespace", self.namespace,
        ]  # fmt: off
        display(["bonfire"] + command)

        if self.check:
            sys.exit()

        result = bonfire(*command, _tee=True, _env=self.env, _out=sys.stdout, _err=sys.stderr)
        self.pod = result.rstrip()

        return self.pod

    def follow_logs(self) -> None:
        oc.logs(self.pod, namespace=self.namespace, container=self.container, follow=True,
            _out=sys.stdout,
            _err=sys.stderr,
            _timeout=self.iqe_cji_timeout,
        )  # fmt: off

    def check_cji_jobs(self) -> None:
        data = oc.get(
            f"cji/{self.component_name}",
            output="json",
            namespace=self.namespace,
        )
        cji = json.loads(data)
        job_map = cji["status"]["jobMap"]
        if any(v != "Complete" for v in job_map.values()):
            print(f"\nSome jobs failed: {job_map}", flush=True)
            sys.exit(1)

        print(f"\nAll jobs succeeded: {job_map}", flush=True)

    def run(self) -> None:
        if "ok-to-skip-smokes" in self.pr_labels:
            display("PR labeled to skip smoke tests")
            return

        # Skip Konflux tests unless explicitly labeled.
        # This prevents tests from running in both Jenkins and Konflux and can be
        # removed when Konflux increases the integration test timeout and
        # Jenkins tests are disabled.
        #
        # https://issues.redhat.com/browse/KONFLUX-5449
        if self.pr_labels is not None:
            if "run-konflux-tests" not in self.pr_labels:
                display("PR is not labeled to run tests in Konflux")
                return

            if "smokes-required" in self.pr_labels and not any(label.endswith("smoke-tests") for label in self.pr_labels):
                sys.exit("Missing smoke tests labels.")

        self.run_pod()

        try:
            self.follow_logs()
        except sh.TimeoutException:
            display(f"Test exceeded timeout {self.iqe_cji_timeout}")
            oc.delete.pod(self.pod, namespace=self.namespace, _ok_code=[0, 1])
        except sh.ErrorReturnCode as exc:
            display("Test command failed")
            display(str(exc))

        oc([
            "wait", "--timeout", f"{self.iqe_cji_timeout}s",
            "--for", "condition=JobInvocationComplete",
            "--namespace", self.namespace,
            f"cji/{self.component_name}",
        ])  # fmt: off

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

import importlib.util
import json
import os
import sys
import types
import unittest

from pathlib import Path
from unittest import mock


BIN = Path(__file__).resolve().parent.parent / "files" / "bin"

# deploy-iqe-cji.py imports `sh` at module level, which is not needed to compute the schema suffix.
fake_sh = types.ModuleType("sh")
fake_sh.__path__ = []
fake_sh.bonfire = mock.MagicMock()
fake_sh.oc = mock.MagicMock()
fake_sh.ErrorReturnCode = Exception
fake_sh.TimeoutException = Exception
sys.modules.setdefault("sh", fake_sh)
sys.path.insert(0, str(BIN))

import deploy  # noqa: E402

from models import Snapshot  # noqa: E402


spec = importlib.util.spec_from_file_location("deploy_iqe_cji", BIN / "deploy-iqe-cji.py")
deploy_iqe_cji = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy_iqe_cji)

PR_NUMBER = "6476"
PIPELINE_RUN_NAME = "scenario-abcde"


def make_snapshot(*components: tuple[str, str]) -> Snapshot:
    return Snapshot.model_validate_json(
        json.dumps({
            "application": "app",
            "components": [
                {
                    "name": name,
                    "containerImage": "quay.io/x/y@sha256:" + "a" * 64,
                    "source": {"git": {"url": "https://github.com/project-koku/koku", "revision": revision, "context": "./"}},
                }
                for name, revision in components
            ],
            "artifacts": {},
        })
    )


def suffixes(snapshot: Snapshot, pr_number: str, bonfire_component_name: str) -> tuple[list[str], str, list[str]]:
    env = {"BONFIRE_COMPONENT_NAME": bonfire_component_name, "PIPELINE_RUN_NAME": PIPELINE_RUN_NAME}
    with mock.patch.dict(os.environ, env, clear=True):
        options = deploy.get_component_options(snapshot.components, pr_number, None)
        runner = deploy_iqe_cji.IQERunner.__new__(deploy_iqe_cji.IQERunner)
        runner.pr_number = pr_number
        runner.snapshot = snapshot
        iqe = runner.schema_suffix.removeprefix("SCHEMA_SUFFIX=")

    app = [o.split("SCHEMA_SUFFIX=", 1)[1] for o in options if "/SCHEMA_SUFFIX=" in o]
    tags = [o.split("IMAGE_TAG=", 1)[1] for o in options if "/IMAGE_TAG=" in o]
    return app, iqe, tags


class SchemaSuffixParityTest(unittest.TestCase):
    """The app (deploy.py) and IQE (deploy-iqe-cji.py) only share a schema when SCHEMA_SUFFIX is identical."""

    def assert_parity(self, snapshot: Snapshot, pr_number: str, bonfire_component_name: str, suffix: str, tag: str) -> None:
        app, iqe, tags = suffixes(snapshot, pr_number, bonfire_component_name)
        self.assertEqual(app, [suffix])
        self.assertEqual(iqe, suffix)
        self.assertEqual(tags, [tag])

    def test_saas_pr(self):
        self.assert_parity(make_snapshot(("koku", "abc1234ffff")), PR_NUMBER, "", "_pr-6476-abc1234_abcde", "pr-6476-abc1234")

    def test_onprem_pr_component_aliased_to_koku(self):
        # The on-prem PR image is built without the pr- prefix, so only the schema suffix carries it.
        self.assert_parity(make_snapshot(("koku-onprem", "abc1234ffff")), PR_NUMBER, "koku", "_pr-6476-abc1234_abcde", "abc1234")

    def test_saas_push(self):
        self.assert_parity(make_snapshot(("koku", "abc1234ffff")), "", "", "_abc1234_abcde", "abc1234")

    def test_onprem_push(self):
        self.assert_parity(make_snapshot(("koku-onprem", "abc1234ffff")), "", "koku", "_abc1234_abcde", "abc1234")

    def test_only_built_component_gets_pr_image_tag(self):
        snapshot = make_snapshot(("nise-populator", "bbb2222fff"), ("koku", "ccc3333fff"))
        env = {"BONFIRE_COMPONENT_NAME": "nise-populator", "PIPELINE_RUN_NAME": PIPELINE_RUN_NAME}
        with mock.patch.dict(os.environ, env, clear=True):
            options = deploy.get_component_options(snapshot.components, "9", None)

        tags = [o.split("IMAGE_TAG=", 1)[1] for o in options if "/IMAGE_TAG=" in o]
        self.assertEqual(tags, ["pr-9-bbb2222", "ccc3333"])


if __name__ == "__main__":
    unittest.main()

import os
import unittest

from unittest import mock

from test_schema_suffix import deploy_iqe_cji


def check(marker_expression: str | None) -> bool:
    env = {} if marker_expression is None else {"IQE_MARKER_EXPRESSION": marker_expression}
    with mock.patch.dict(os.environ, env, clear=True):
        return deploy_iqe_cji.is_onprem_scenario()


class OnpremScenarioTest(unittest.TestCase):
    def test_onprem_marker(self):
        self.assertTrue(check("cost_ocp_on_prem and not cost_exclude_ocp_smokes"))

    def test_saas_marker(self):
        self.assertFalse(check("test_api_ocp and not ocp_on_cloud"))

    def test_marker_not_set(self):
        self.assertFalse(check(None))

#!/usr/bin/env python
"""Tests for grr.server.flows.general.filetypes."""


import os

from grr.client.client_actions import plist
from grr.lib import flags
from grr.lib.rdfvalues import paths as rdf_paths
from grr.lib.rdfvalues import plist as rdf_plist
from grr.server import flow
from grr.server.flows.general import filetypes
from grr.test_lib import action_mocks
from grr.test_lib import flow_test_lib
from grr.test_lib import test_lib


class TestPlistFlows(flow_test_lib.FlowTestsBaseclass):
  """Tests the PlistValueFilter flow."""

  def _RunFlow(self, flow_name, context=None, query=None):
    client_mock = action_mocks.ActionMock(plist.PlistQuery)
    request = rdf_plist.PlistRequest(context=context, query=query)
    request.pathspec.path = os.path.join(self.base_path, "test.plist")
    request.pathspec.pathtype = rdf_paths.PathSpec.PathType.OS

    for s in flow_test_lib.TestFlowHelper(
        flow_name,
        client_mock,
        client_id=self.client_id,
        token=self.token,
        request=request):
      session_id = s

    return session_id

  def _CheckOutput(self, session_id):
    results = flow.GRRFlow.ResultCollectionForFID(session_id)
    self.assertEqual(len(results), 1)
    self.assertEqual(results[0]["nested1"]["nested11"]["key112"], "value112")

  def testPlistValueFilter(self):

    session_id = self._RunFlow(
        filetypes.PlistValueFilter.__name__, context="", query="")
    self._CheckOutput(session_id)


def main(argv):
  # Run the full test suite
  test_lib.main(argv)


if __name__ == "__main__":
  flags.StartMain(main)

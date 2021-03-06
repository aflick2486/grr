#!/usr/bin/env python
"""Gather information from the registry on windows."""


from grr.lib.rdfvalues import file_finder as rdf_file_finder
from grr.lib.rdfvalues import paths as rdf_paths
from grr.lib.rdfvalues import structs as rdf_structs
from grr.path_detection import windows as path_detection_windows
from grr.proto import flows_pb2
from grr.server import aff4
from grr.server import artifact
from grr.server import artifact_utils
from grr.server import flow
from grr.server.flows.general import collectors
from grr.server.flows.general import file_finder
from grr.server.flows.general import transfer


class RegistryFinderCondition(rdf_structs.RDFProtoStruct):
  protobuf = flows_pb2.RegistryFinderCondition
  rdf_deps = [
      rdf_file_finder.FileFinderContentsLiteralMatchCondition,
      rdf_file_finder.FileFinderContentsRegexMatchCondition,
      rdf_file_finder.FileFinderModificationTimeCondition,
      rdf_file_finder.FileFinderSizeCondition,
  ]


class RegistryFinderArgs(rdf_structs.RDFProtoStruct):
  protobuf = flows_pb2.RegistryFinderArgs
  rdf_deps = [
      rdf_paths.GlobExpression,
      RegistryFinderCondition,
  ]


class RegistryFinder(flow.GRRFlow):
  """This flow looks for registry items matching given criteria."""

  friendly_name = "Registry Finder"
  category = "/Registry/"
  args_type = RegistryFinderArgs
  behaviours = flow.GRRFlow.behaviours + "BASIC"

  def ConditionsToFileFinderConditions(self, conditions):
    ff_condition_type_cls = rdf_file_finder.FileFinderCondition.Type
    result = []
    for c in conditions:
      if c.condition_type == RegistryFinderCondition.Type.MODIFICATION_TIME:
        result.append(
            rdf_file_finder.FileFinderCondition(
                condition_type=ff_condition_type_cls.MODIFICATION_TIME,
                modification_time=c.modification_time))
      elif c.condition_type == RegistryFinderCondition.Type.VALUE_LITERAL_MATCH:
        result.append(
            rdf_file_finder.FileFinderCondition(
                condition_type=ff_condition_type_cls.CONTENTS_LITERAL_MATCH,
                contents_literal_match=c.value_literal_match))
      elif c.condition_type == RegistryFinderCondition.Type.VALUE_REGEX_MATCH:
        result.append(
            rdf_file_finder.FileFinderCondition(
                condition_type=ff_condition_type_cls.CONTENTS_REGEX_MATCH,
                contents_regex_match=c.value_regex_match))
      elif c.condition_type == RegistryFinderCondition.Type.SIZE:
        result.append(
            rdf_file_finder.FileFinderCondition(
                condition_type=ff_condition_type_cls.SIZE, size=c.size))
      else:
        raise ValueError("Unknown condition type: %s", c.condition_type)

    return result

  @classmethod
  def GetDefaultArgs(cls, username=None):
    del username
    return cls.args_type(keys_paths=[
        "HKEY_USERS/%%users.sid%%/Software/"
        "Microsoft/Windows/CurrentVersion/Run/*"
    ])

  @flow.StateHandler()
  def Start(self):
    self.CallFlow(
        file_finder.FileFinder.__name__,
        paths=self.args.keys_paths,
        pathtype=rdf_paths.PathSpec.PathType.REGISTRY,
        conditions=self.ConditionsToFileFinderConditions(self.args.conditions),
        action=rdf_file_finder.FileFinderAction(
            action_type=rdf_file_finder.FileFinderAction.Action.STAT),
        next_state="Done")

  @flow.StateHandler()
  def Done(self, responses):
    if not responses.success:
      raise flow.FlowError("Registry search failed %s" % responses.status)

    for response in responses:
      self.SendReply(response)


# TODO(user): replace this flow with chained artifacts once the capability
# exists.
class CollectRunKeyBinaries(flow.GRRFlow):
  """Collect the binaries used by Run and RunOnce keys on the system.

  We use the RunKeys artifact to get RunKey command strings for all users and
  System. This flow guesses file paths from the strings, expands any
  windows system environment variables, and attempts to retrieve the files.
  """
  category = "/Registry/"
  behaviours = flow.GRRFlow.behaviours + "BASIC"

  @flow.StateHandler()
  def Start(self):
    """Get runkeys via the ArtifactCollectorFlow."""
    self.CallFlow(
        collectors.ArtifactCollectorFlow.__name__,
        artifact_list=["WindowsRunKeys"],
        use_tsk=True,
        store_results_in_aff4=False,
        next_state="ParseRunKeys")

  @flow.StateHandler()
  def ParseRunKeys(self, responses):
    """Get filenames from the RunKeys and download the files."""
    filenames = []
    client = aff4.FACTORY.Open(self.client_id, mode="r", token=self.token)
    kb = artifact.GetArtifactKnowledgeBase(client)

    for response in responses:
      runkey = response.registry_data.string

      environ_vars = artifact_utils.GetWindowsEnvironmentVariablesMap(kb)
      path_guesses = path_detection_windows.DetectExecutablePaths([runkey],
                                                                  environ_vars)

      if not path_guesses:
        self.Log("Couldn't guess path for %s", runkey)

      for path in path_guesses:
        filenames.append(
            rdf_paths.PathSpec(
                path=path, pathtype=rdf_paths.PathSpec.PathType.TSK))

    if filenames:
      self.CallFlow(
          transfer.MultiGetFile.__name__,
          pathspecs=filenames,
          next_state="Done")

  @flow.StateHandler()
  def Done(self, responses):
    for response in responses:
      self.SendReply(response)

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Dict, Generator, List, Optional, Tuple, cast

from nuplan.common.actor_state.agent import Agent
from nuplan.common.actor_state.tracked_objects import TrackedObject, TrackedObjects
from nuplan.common.actor_state.waypoint import Waypoint
from nuplan.common.geometry.interpolate_state import interpolate_future_waypoints
from nuplan.database.common.blob_store.creator import BlobStoreCreator
from nuplan.database.common.blob_store.local_store import LocalStore
from nuplan.database.nuplan_db.nuplan_scenario_queries import (
    get_future_waypoints_for_agents_from_db,
    get_lidarpc_token_timestamp_from_db,
    get_sampled_lidarpc_tokens_in_time_window_from_db,
    get_tracked_objects_for_lidarpc_token_from_db,
)
from nuplan.planning.simulation.trajectory.predicted_trajectory import PredictedTrajectory
from nuplan.planning.simulation.trajectory.trajectory_sampling import TrajectorySampling

logger = logging.getLogger(__name__)

LIDAR_PC_CACHE = 16 * 2**10  # 16K

DEFAULT_SCENARIO_NAME = 'unknown'  # name of scenario (e.g. ego overtaking)
DEFAULT_SCENARIO_DURATION = 20.0  # [s] duration of the scenario (e.g. extract 20s from when the event occurred)
DEFAULT_EXTRACTION_OFFSET = 0.0  # [s] offset of the scenario (e.g. start at -5s from when the event occurred)
DEFAULT_SUBSAMPLE_RATIO = 1.0  # ratio used sample the scenario (e.g. a 0.1 ratio means sample from 20Hz to 2Hz)


@dataclass(frozen=True)
class ScenarioExtractionInfo:
    """
    Structure containing information used to extract a scenario (lidarpc sequence).
    """

    scenario_name: str = DEFAULT_SCENARIO_NAME  # name of the scenario
    scenario_duration: float = DEFAULT_SCENARIO_DURATION  # [s] duration of the scenario
    extraction_offset: float = DEFAULT_EXTRACTION_OFFSET  # [s] offset of the scenario
    subsample_ratio: float = DEFAULT_SUBSAMPLE_RATIO  # ratio to sample the scenario

    def __post_init__(self) -> None:
        """Sanitize class attributes."""
        assert 0.0 < self.scenario_duration, f'Scenario duration has to be greater than 0, got {self.scenario_duration}'
        assert (
            0.0 < self.subsample_ratio <= 1.0
        ), f'Subsample ratio has to be between 0 and 1, got {self.subsample_ratio}'


class ScenarioMapping:
    """
    Structure that maps each scenario type to instructions used in extracting it.
    """

    def __init__(self, scenario_map: Dict[str, Tuple[float, float, float]]) -> None:
        """
        Initializes the scenario mapping class.
        :param scenario_map: Dictionary with scenario name/type as keys and
                             tuples of (scenario duration, extraction offset, subsample ratio) as values.
        """
        self.mapping = {name: ScenarioExtractionInfo(name, *value) for name, value in scenario_map.items()}

    def get_extraction_info(self, scenario_type: str) -> Optional[ScenarioExtractionInfo]:
        """
        Accesses the scenario mapping using a query scenario type.
        If the scenario type is not found, a default extraction info object is returned.
        :param scenario_type: Scenario type to query for.
        :return: Scenario extraction information for the queried scenario type.
        """
        return self.mapping[scenario_type] if scenario_type in self.mapping else ScenarioExtractionInfo()


def download_file_if_necessary(data_root: str, potentially_remote_path: str) -> str:
    """
    Downloads the db file if necessary.
    :param potentially_remote_path: The path from which to download the file.
    :return: The local path for the file.
    """
    # If the file path is a local directory and exists, then return that.
    # e.g. /data/sets/nuplan/nuplan-v1.0/file.db
    if os.path.exists(potentially_remote_path):
        return potentially_remote_path

    log_name = absolute_path_to_log_name(potentially_remote_path)
    download_name = log_name + ".db"

    # TODO: CacheStore seems to be buggy.
    # Behavior seems to be different on our cluster vs locally regarding downloaded file paths.
    #
    # Use the underlying stores manually.
    blob_store = BlobStoreCreator.create_nuplandb(data_root)
    local_store = LocalStore(data_root)

    # Only trigger the download if we have not already acquired the file.
    download_path_name = os.path.join(data_root, download_name)

    if not local_store.exists(download_name):
        # If we have no matches, download the file.
        logger.info("DB path not found. Downloading to %s..." % download_name)
        start_time = time.time()
        content = blob_store.get(potentially_remote_path)
        local_store.put(download_name, content)
        logger.info("Downloading db file took %.2f seconds." % (time.time() - start_time))

    return download_path_name


def extract_tracked_objects(
    token: str, log_file: str, future_trajectory_sampling: Optional[TrajectorySampling] = None
) -> TrackedObjects:
    """
    Extracts all boxes from a lidarpc.
    :param lidar_pc: Input lidarpc.
    :param future_trajectory_sampling: Sampling parameters for future predictions, if not provided, no future poses
    are extracted
    :return: Tracked objects contained in the lidarpc.
    """
    tracked_objects: List[TrackedObject] = []
    agent_indexes: Dict[str, int] = {}
    agent_future_trajectories: Dict[str, List[Waypoint]] = {}

    for idx, tracked_object in enumerate(get_tracked_objects_for_lidarpc_token_from_db(log_file, token)):
        if future_trajectory_sampling and isinstance(tracked_object, Agent):
            agent_indexes[tracked_object.metadata.track_token] = idx
            agent_future_trajectories[tracked_object.metadata.track_token] = []
        tracked_objects.append(tracked_object)

    if future_trajectory_sampling:
        timestamp_time = get_lidarpc_token_timestamp_from_db(log_file, token)
        end_time = timestamp_time + (1e6 * future_trajectory_sampling.time_horizon)

        # TODO: This is somewhat inefficient because the resampling should happen in SQL layer
        for track_token, waypoint in get_future_waypoints_for_agents_from_db(
            log_file, list(agent_indexes.keys()), timestamp_time, end_time
        ):
            agent_future_trajectories[track_token].append(waypoint)

        for key in agent_future_trajectories:
            # We can only interpolate waypoints if there is more than one in the future.
            if len(agent_future_trajectories[key]) == 1:
                tracked_objects[agent_indexes[key]]._predictions = [
                    PredictedTrajectory(1.0, agent_future_trajectories[key])
                ]
            elif len(agent_future_trajectories[key]) > 1:
                tracked_objects[agent_indexes[key]]._predictions = [
                    PredictedTrajectory(
                        1.0,
                        interpolate_future_waypoints(
                            agent_future_trajectories[key],
                            future_trajectory_sampling.time_horizon,
                            future_trajectory_sampling.interval_length,
                        ),
                    )
                ]

    return TrackedObjects(tracked_objects=tracked_objects)


def extract_lidarpc_tokens_as_scenario(
    log_file: str, anchor_timestamp: float, scenario_extraction_info: ScenarioExtractionInfo
) -> Generator[str, None, None]:
    """
    Extract a list of lidarpc tokens that form a scenario around an anchor timestamp.
    :param log_file: The log file to access
    :param anchor_timestamp: Timestamp of Lidarpc representing the start of the scenario.
    :param scenario_extraction_info: Structure containing information used to extract the scenario.
    :return: List of extracted lidarpc tokens representing the scenario.
    """
    start_timestamp = int(anchor_timestamp + scenario_extraction_info.extraction_offset * 1e6)
    end_timestamp = int(start_timestamp + scenario_extraction_info.scenario_duration * 1e6)
    subsample_step = int(1.0 / scenario_extraction_info.subsample_ratio)

    return cast(
        Generator[str, None, None],
        get_sampled_lidarpc_tokens_in_time_window_from_db(log_file, start_timestamp, end_timestamp, subsample_step),
    )


def absolute_path_to_log_name(absolute_path: str) -> str:
    """
    Gets the log name from the absolute path to a log file.
    E.g.
        input: data/sets/nuplan/nuplan-v1.0/mini/2021.10.11.02.57.41_veh-50_01522_02088.db
        output: 2021.10.11.02.57.41_veh-50_01522_02088

        input: /tmp/abcdef
        output: abcdef
    :param absolute_path: The absolute path to a log file.
    :return: The log name.
    """
    filename = os.path.basename(absolute_path)

    # Files generated during caching do not end with ".db"
    # They have no extension.
    if filename.endswith(".db"):
        filename = os.path.splitext(filename)[0]
    return filename

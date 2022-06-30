from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Dict, List, Tuple

import numpy as np

from nuplan.common.actor_state.state_representation import Point2D
from nuplan.common.maps.abstract_map import AbstractMap
from nuplan.common.maps.maps_datatypes import SemanticMapLayer, TrafficLightStatusData, TrafficLightStatusType
from nuplan.common.maps.nuplan_map.utils import (
    build_lane_segments_from_blps,
    connect_lane_conn_predecessor,
    connect_lane_conn_successor,
    extract_polygon_from_map_object,
)


class OnRouteStatusType(IntEnum):
    """
    Enum for OnRouteStatusType.
    """

    OFF_ROUTE = 0
    ON_ROUTE = 1
    UNKNOWN = 2


class VectorFeatureLayer(IntEnum):
    """
    Enum for VectorFeatureLayer.
    """

    LANE = 0
    BOUNDARIES = 1
    STOP_LINE = 2
    CROSSWALK = 3
    ROUTE = 4

    @classmethod
    def deserialize(cls, layer: str) -> VectorFeatureLayer:
        """Deserialize the type when loading from a string."""
        return VectorFeatureLayer.__members__[layer]


@dataclass
class VectorFeatureLayerMapping:
    """
    Dataclass for associating VectorFeatureLayers with SemanticMapLayers for extracting map object polygons.
    """

    _semantic_map_layer_mapping = {
        VectorFeatureLayer.STOP_LINE: SemanticMapLayer.STOP_LINE,
        VectorFeatureLayer.CROSSWALK: SemanticMapLayer.CROSSWALK,
    }

    @classmethod
    def available_polygon_layers(cls) -> List[VectorFeatureLayer]:
        """
        List of VectorFeatureLayer for which mapping is supported.
        :return List of available layers.
        """
        return list(cls._semantic_map_layer_mapping.keys())

    @classmethod
    def semantic_map_layer(cls, feature_layer: VectorFeatureLayer) -> SemanticMapLayer:
        """
        Returns associated SemanticMapLayer for feature extraction, if exists.
        :param feature_layer: specified VectorFeatureLayer to look up.
        :return associated SemanticMapLayer.
        """
        return cls._semantic_map_layer_mapping[feature_layer]


@dataclass
class LaneOnRouteStatusData:
    """
    Route following status data represented as binary encoding per lane segment [num_lane_segment, 2].
    The binary encoding: off route [0, 1], on route [1, 0], unknown [0, 0].
    """

    on_route_status: List[Tuple[int, int]]

    _binary_encoding = {
        OnRouteStatusType.OFF_ROUTE: (0, 1),
        OnRouteStatusType.ON_ROUTE: (1, 0),
        OnRouteStatusType.UNKNOWN: (0, 0),
    }
    _encoding_dim: int = 2

    def to_vector(self) -> List[List[float]]:
        """
        Returns data in vectorized form.
        :return: vectorized on route status data per lane segment as [num_lane_segment, 2].
        """
        return [list(data) for data in self.on_route_status]

    @classmethod
    def encode(cls, on_route_status_type: OnRouteStatusType) -> Tuple[int, int]:
        """
        Binary encoding of OnRouteStatusType: off route [0, 0], on route [0, 1], unknown [1, 0].
        """
        return cls._binary_encoding[on_route_status_type]

    @classmethod
    def encoding_dim(cls) -> int:
        """
        Dimensionality of associated data encoding.
        :return: encoding dimensionality.
        """
        return cls._encoding_dim


@dataclass
class LaneSegmentCoords:
    """
    Lane-segment coordinates in format of [N, 2, 2] representing [num_lane_segment, [start coords, end coords]].
    """

    coords: List[Tuple[Point2D, Point2D]]

    def to_vector(self) -> List[List[List[float]]]:
        """
        Returns data in vectorized form.
        :return: vectorized lane segment coordinates in [num_lane_segment, 2, 2].
        """
        return [[[start.x, start.y], [end.x, end.y]] for start, end in self.coords]


@dataclass
class LaneSegmentConnections:
    """
    Lane-segment connection relations in format of [num_connection, 2] and each column in the array is
    (from_lane_segment_idx, to_lane_segment_idx).
    """

    connections: List[Tuple[int, int]]

    def to_vector(self) -> List[List[int]]:
        """
        Returns data in vectorized form.
        :return: vectorized lane segment connections as [num_lane_segment, 2, 2].
        """
        return [[start, end] for start, end in self.connections]


@dataclass
class LaneSegmentGroupings:
    """
    Lane-segment groupings in format of [num_lane, num_segment_in_lane (variable size)]
    containing a list of indices of lane segments in corresponding coords list for each lane.
    """

    groupings: List[List[int]]

    def to_vector(self) -> List[List[int]]:
        """
        Returns data in vectorized form.
        :return: vectorized groupings of lane segments as [num_lane, num_lane_segment_in_lane].
        """
        return [[segment_id for segment_id in grouping] for grouping in self.groupings]


@dataclass
class LaneSegmentLaneIDs:
    """
    IDs of lane/lane connectors that lane segment at specified index belong to.
    """

    lane_ids: List[str]


@dataclass
class LaneSegmentRoadBlockIDs:
    """
    IDs of roadblock/roadblock connectors that lane segment at specified index belong to.
    """

    roadblock_ids: List[str]


@dataclass
class LaneSegmentTrafficLightData:
    """
    Traffic light data represented as one-hot encoding per segment [num_lane_segment, 4].
    The one-hot encoding: green [1, 0, 0, 0], yellow [0, 1, 0, 0], red [0, 0, 1, 0], unknown [0, 0, 0, 1].
    """

    traffic_lights: List[Tuple[int, int, int, int]]

    _one_hot_encoding = {
        TrafficLightStatusType.GREEN: (1, 0, 0, 0),
        TrafficLightStatusType.YELLOW: (0, 1, 0, 0),
        TrafficLightStatusType.RED: (0, 0, 1, 0),
        TrafficLightStatusType.UNKNOWN: (0, 0, 0, 1),
    }
    _encoding_dim: int = 4

    def to_vector(self) -> List[List[float]]:
        """
        Returns data in vectorized form.
        :return: vectorized traffic light data per segment as [num_lane_segment, 4].
        """
        return [list(data) for data in self.traffic_lights]

    @classmethod
    def encode(cls, traffic_light_type: TrafficLightStatusType) -> Tuple[int, int, int, int]:
        """
        One-hot encoding of TrafficLightStatusType: green [1, 0, 0, 0], yellow [0, 1, 0, 0], red [0, 0, 1, 0],
            unknown [0, 0, 0, 1].
        """
        return cls._one_hot_encoding[traffic_light_type]

    @classmethod
    def encoding_dim(cls) -> int:
        """
        Dimensionality of associated data encoding.
        :return: encoding dimensionality.
        """
        return cls._encoding_dim


@dataclass
class MapObjectPolylines:
    """
    Collection of map object polylines, each represented as a list of x, y coords
    [num_elements, num_points_in_element (variable size), 2].
    """

    polylines: List[List[Point2D]]

    def to_vector(self) -> List[List[List[float]]]:
        """
        Returns data in vectorized form
        :return: vectorized coords of map object polylines as [num_elements, num_points_in_element (variable size), 2].
        """
        return [[[coord.x, coord.y] for coord in polygon] for polygon in self.polylines]


def lane_segment_coords_from_lane_segment_vector(coords: List[List[List[float]]]) -> LaneSegmentCoords:
    """
    Convert lane segment coords [N, 2, 2] to nuPlan LaneSegmentCoords.
    :param coords: lane segment coordinates in vector form.
    :return: lane segment coordinates as LaneSegmentCoords.
    """
    return LaneSegmentCoords([(Point2D(start[0], start[1]), Point2D(end[0], end[1])) for start, end in coords])


def get_lane_polylines(
    map_api: AbstractMap, point: Point2D, radius: float
) -> Tuple[MapObjectPolylines, MapObjectPolylines, MapObjectPolylines, List[LaneSegmentLaneIDs]]:
    """
    Extract ids, baseline path polylines, and boundary polylines of neighbor lanes and lane connectors around ego vehicle.
    :param map_api: map to perform extraction on.
    :param point: [m] x, y coordinates in global frame.
    :param radius [m] floating number about extraction query range.
    :return:
        lanes_mid: extracted lane/lane connector baseline polylines.
        lanes_left: extracted lane/lane connector left boundary polylines.
        lanes_right: extracted lane/lane connector right boundary polylines.
        lane_ids: ids of lanes/lane connector associated polylines were extracted from at given index.
    """
    lanes_mid: List[List[Point2D]] = []  # shape: [num_lanes, num_points_per_lane (variable), 2]
    lanes_left: List[List[Point2D]] = []  # shape: [num_lanes, num_points_per_lane (variable), 2]
    lanes_right: List[List[Point2D]] = []  # shape: [num_lanes, num_points_per_lane (variable), 2]
    lane_ids: List[LaneSegmentLaneIDs] = []  # shape: [num_lanes, num_points_per_lane (variable)]
    layer_names = [SemanticMapLayer.LANE, SemanticMapLayer.LANE_CONNECTOR]
    layers = map_api.get_proximal_map_objects(point, radius, layer_names)

    for layer_name in layer_names:
        for map_obj in layers[layer_name]:
            # center lane
            baseline_path_polyline = [Point2D(node.x, node.y) for node in map_obj.baseline_path.discrete_path]
            lanes_mid.append(baseline_path_polyline)

            # boundaries
            lanes_left.append([Point2D(node.x, node.y) for node in map_obj.left_boundary.discrete_path])
            lanes_right.append([Point2D(node.x, node.y) for node in map_obj.right_boundary.discrete_path])

            # lane ids
            lane_ids.append(LaneSegmentLaneIDs([map_obj.id for _ in range(len(baseline_path_polyline))]))

    return (
        MapObjectPolylines(lanes_mid),
        MapObjectPolylines(lanes_left),
        MapObjectPolylines(lanes_right),
        lane_ids,
    )


def get_map_object_polygons(
    map_api: AbstractMap, point: Point2D, radius: float, layer_name: SemanticMapLayer
) -> MapObjectPolylines:
    """
    Extract polygons of neighbor map object around ego vehicle for specified semantic layers.
    :param map_api: map to perform extraction on.
    :param point: [m] x, y coordinates in global frame.
    :param radius [m] floating number about extraction query range.
    :param layer_name: semantic layer to query.
    :return extracted map object polygons.
    """
    polygons: List[List[Point2D]] = []
    layers = map_api.get_proximal_map_objects(point, radius, [layer_name])

    for map_obj in layers[layer_name]:
        polygon = extract_polygon_from_map_object(map_obj)
        polygons.append(polygon)

    return MapObjectPolylines(polygons)


def get_route_polygon_from_roadblock_ids(map_api: AbstractMap, route_roadblock_ids: List[str]) -> MapObjectPolylines:
    """
    Extract route polygon from map for route specified by list of roadblock ids.
    :param map_api: map to perform extraction on.
    :param route_roadblock_ids: ids of roadblocks/roadblock connectors specifying route.
    :return route as sequence of roadblock/roadblock connector polygons.
    """
    route_polygons: List[List[Point2D]] = []

    for route_roadblock_id in route_roadblock_ids:
        # roadblock
        roadblock_obj = map_api.get_map_object(route_roadblock_id, SemanticMapLayer.ROADBLOCK)

        # roadblock connector
        if not roadblock_obj:
            roadblock_obj = map_api.get_map_object(route_roadblock_id, SemanticMapLayer.ROADBLOCK_CONNECTOR)

        if roadblock_obj:
            polygon = extract_polygon_from_map_object(roadblock_obj)
            route_polygons.append(polygon)

    return MapObjectPolylines(route_polygons)


def get_on_route_status(
    route_roadblock_ids: List[str], roadblock_ids: LaneSegmentRoadBlockIDs
) -> LaneOnRouteStatusData:
    """
    Identify whether given lane segments lie within goal route.
    :param route_roadblock_ids: List of ids of roadblocks (lane groups) within goal route.
    :param roadblock_ids: Roadblock ids (lane group associations) pertaining to associated lane segments.
    :return on_route_status: binary encoding of on route status for each input roadblock id.
    """
    if route_roadblock_ids:

        # initialize on route status as OFF_ROUTE
        on_route_status = np.full(
            (len(roadblock_ids.roadblock_ids), len(OnRouteStatusType) - 1),
            LaneOnRouteStatusData.encode(OnRouteStatusType.OFF_ROUTE),
        )

        # Get indices of the segments that lie on the route
        on_route_indices = np.arange(on_route_status.shape[0])[
            np.in1d(roadblock_ids.roadblock_ids, route_roadblock_ids)
        ]

        #  Set segments on route to ON_ROUTE
        on_route_status[on_route_indices] = LaneOnRouteStatusData.encode(OnRouteStatusType.ON_ROUTE)

    else:
        # set on route status to UNKNOWN if no route available
        on_route_status = np.full(
            (len(roadblock_ids.roadblock_ids), len(OnRouteStatusType) - 1),
            LaneOnRouteStatusData.encode(OnRouteStatusType.UNKNOWN),
        )

    return LaneOnRouteStatusData(list(map(tuple, on_route_status)))  # type: ignore


def get_traffic_light_encoding(
    lane_seg_ids: LaneSegmentLaneIDs, traffic_light_data: List[TrafficLightStatusData]
) -> LaneSegmentTrafficLightData:
    """
    Encode the lane segments with traffic light data.
    :param lane_seg_ids: The lane_segment ids [num_lane_segment].
    :param traffic_light_data: A list of all available data at the current time step.
    :returns: Encoded traffic light data per segment.
    """
    # Initialize with all segment labels with UNKNOWN status
    traffic_light_encoding = np.full(
        (len(lane_seg_ids.lane_ids), len(TrafficLightStatusType)),
        LaneSegmentTrafficLightData.encode(TrafficLightStatusType.UNKNOWN),
    )

    # Extract ids of red and green lane connectors
    green_lane_connectors = [
        str(data.lane_connector_id) for data in traffic_light_data if data.status == TrafficLightStatusType.GREEN
    ]
    red_lane_connectors = [
        str(data.lane_connector_id) for data in traffic_light_data if data.status == TrafficLightStatusType.RED
    ]

    # Assign segments with corresponding traffic light status
    for tl_id in green_lane_connectors:
        indices = np.argwhere(np.array(lane_seg_ids.lane_ids) == tl_id)
        traffic_light_encoding[indices] = LaneSegmentTrafficLightData.encode(TrafficLightStatusType.GREEN)

    for tl_id in red_lane_connectors:
        indices = np.argwhere(np.array(lane_seg_ids.lane_ids) == tl_id)
        traffic_light_encoding[indices] = LaneSegmentTrafficLightData.encode(TrafficLightStatusType.RED)

    return LaneSegmentTrafficLightData(list(map(tuple, traffic_light_encoding)))  # type: ignore


def get_neighbor_vector_map(
    map_api: AbstractMap, point: Point2D, radius: float
) -> Tuple[
    LaneSegmentCoords, LaneSegmentConnections, LaneSegmentGroupings, LaneSegmentLaneIDs, LaneSegmentRoadBlockIDs
]:
    """
    Extract neighbor vector map information around ego vehicle.
    :param map_api: map to perform extraction on.
    :param point: [m] x, y coordinates in global frame.
    :param radius [m] floating number about vector map query range.
    :return
        lane_seg_coords: lane_segment coords in shape of [num_lane_segment, 2, 2].
        lane_seg_conns: lane_segment connections [start_idx, end_idx] in shape of [num_connection, 2].
        lane_seg_groupings: collection of lane_segment indices in each lane in shape of
            [num_lane, num_lane_segment_in_lane].
        lane_seg_lane_ids: lane ids of segments at given index in coords in shape of [num_lane_segment 1].
        lane_seg_roadblock_ids: roadblock ids of segments at given index in coords in shape of [num_lane_segment 1].
    """
    lane_seg_coords: List[List[List[float]]] = []  # shape: [num_lane_segment, 2, 2]
    lane_seg_conns: List[Tuple[int, int]] = []  # shape: [num_connection, 2]
    lane_seg_groupings: List[List[int]] = []  # shape: [num_lanes]
    lane_seg_lane_ids: List[str] = []  # shape: [num_lane_segment]
    lane_seg_roadblock_ids: List[str] = []  # shape: [num_lane_segment]
    cross_blp_conns: Dict[str, Tuple[int, int]] = dict()

    layer_names = [SemanticMapLayer.LANE, SemanticMapLayer.LANE_CONNECTOR]
    nearest_vector_map = map_api.get_proximal_map_objects(point, radius, layer_names)

    # create lane segment vectors from baseline paths
    for layer_name in layer_names:

        for map_obj in nearest_vector_map[layer_name]:
            # current number of coords needed for indexing lane segments
            start_lane_seg_idx = len(lane_seg_coords)
            # update lane segment info with info for given lane/lane connector
            (
                obj_coords,
                obj_conns,
                obj_groupings,
                obj_lane_ids,
                obj_roadblock_ids,
                obj_cross_blp_conn,
            ) = build_lane_segments_from_blps(map_obj, start_lane_seg_idx)
            lane_seg_coords += obj_coords
            lane_seg_conns += obj_conns
            lane_seg_groupings += obj_groupings
            lane_seg_lane_ids += obj_lane_ids
            lane_seg_roadblock_ids += obj_roadblock_ids
            cross_blp_conns[map_obj.id] = obj_cross_blp_conn

    # create connections between adjoining lanes and lane connectors
    for lane_conn in nearest_vector_map[SemanticMapLayer.LANE_CONNECTOR]:
        lane_seg_conns += connect_lane_conn_predecessor(lane_conn, cross_blp_conns)
        lane_seg_conns += connect_lane_conn_successor(lane_conn, cross_blp_conns)

    return (
        lane_segment_coords_from_lane_segment_vector(lane_seg_coords),
        LaneSegmentConnections(lane_seg_conns),
        LaneSegmentGroupings(lane_seg_groupings),
        LaneSegmentLaneIDs(lane_seg_lane_ids),
        LaneSegmentRoadBlockIDs(lane_seg_roadblock_ids),
    )


def get_neighbor_vector_set_map(
    map_api: AbstractMap,
    map_features: List[str],
    point: Point2D,
    radius: float,
    route_roadblock_ids: List[str],
    traffic_light_status_data: List[TrafficLightStatusData],
) -> Tuple[Dict[str, MapObjectPolylines], Dict[str, List[LaneSegmentTrafficLightData]]]:
    """
    Extract neighbor vector set map information around ego vehicle.
    :param map_api: map to perform extraction on.
    :param map_features: Name of map features to extract.
    :param point: [m] x, y coordinates in global frame.
    :param radius [m] floating number about vector map query range.
    :param route_roadblock_ids: List of ids of roadblocks/roadblock connectors (lane groups) within goal route.
    :param traffic_light_status_data: A list of all available data at the current time step.
    :return:
        coords: Dictionary mapping feature name to polyline vector sets.
        traffic_light_data: Dictionary mapping feature name to traffic light info corresponding to map elements
            in coords.
    """
    coords: Dict[str, MapObjectPolylines] = {}
    traffic_light_data: Dict[str, List[LaneSegmentTrafficLightData]] = {}
    feature_layers: List[VectorFeatureLayer] = []

    for feature_name in map_features:
        try:
            feature_layers.append(VectorFeatureLayer[feature_name])
        except KeyError:
            raise ValueError(f"Object representation for layer: {feature_name} is unavailable")

    # extract lanes
    if VectorFeatureLayer.LANE in feature_layers:
        lanes_mid, lanes_left, lanes_right, lane_ids = get_lane_polylines(map_api, point, radius)

        # lane baseline paths
        coords[VectorFeatureLayer.LANE.name] = lanes_mid

        # lane traffic light data
        lane_traffic_light_data: List[LaneSegmentTrafficLightData] = [
            get_traffic_light_encoding(lane_segment_ids, traffic_light_status_data) for lane_segment_ids in lane_ids
        ]
        traffic_light_data[VectorFeatureLayer.LANE.name] = lane_traffic_light_data

        # lane boundaries
        if VectorFeatureLayer.BOUNDARIES in feature_layers:
            coords[VectorFeatureLayer.BOUNDARIES.name] = MapObjectPolylines(
                lanes_left.polylines + lanes_right.polylines
            )

    # extract route
    if VectorFeatureLayer.ROUTE in feature_layers:
        route_polygons = get_route_polygon_from_roadblock_ids(map_api, route_roadblock_ids)
        coords[VectorFeatureLayer.ROUTE.name] = route_polygons

    # extract generic map objects
    for feature_layer in feature_layers:

        if feature_layer in VectorFeatureLayerMapping.available_polygon_layers():
            polygons = get_map_object_polygons(
                map_api, point, radius, VectorFeatureLayerMapping.semantic_map_layer(feature_layer)
            )
            coords[feature_layer.name] = polygons

    return coords, traffic_light_data

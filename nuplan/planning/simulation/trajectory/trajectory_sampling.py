from dataclasses import dataclass
from typing import Optional


@dataclass
class TrajectorySampling:
    """
    Trajectory sampling config. The variables are set as optional, to make sure we can deduce last variable if only
        two are set.
    """

    # Number of poses in trajectory in addition to initial state
    num_poses: Optional[int] = None
    # [s] the time horizon of a trajectory
    time_horizon: Optional[float] = None
    # [s] length of an interval between two states
    interval_length: Optional[float] = None

    def __post_init__(self) -> None:
        """
        Make sure all entries are correctly initialized.
        """
        if self.num_poses and self.time_horizon and not self.interval_length:
            self.interval_length = self.time_horizon / self.num_poses
        elif self.num_poses and self.interval_length and not self.time_horizon:
            self.time_horizon = self.num_poses * self.interval_length
        elif self.time_horizon and self.interval_length and not self.num_poses:
            if self.time_horizon % self.interval_length != 0:
                raise ValueError(
                    "The time horizon must be a multiple of interval length! "
                    f"time_horizon = {self.time_horizon}, interval = {self.interval_length}"
                )
            self.num_poses = int(self.time_horizon / self.interval_length)
        elif self.num_poses and self.time_horizon and self.interval_length:
            if self.num_poses != self.time_horizon / self.interval_length:
                raise ValueError(
                    "Not valid initialization of sampling class!"
                    f"time_horizon = {self.time_horizon}, "
                    f"interval = {self.interval_length}, num_poses = {self.num_poses}"
                )

        else:
            raise ValueError(
                f"Cant initialize class! num_poses = {self.num_poses}, "
                f"interval = {self.interval_length}, time_horizon = {self.time_horizon}"
            )

    @property
    def step_time(self) -> float:
        """
        :return: [s] The time difference between two poses.
        """
        if not self.interval_length:
            raise RuntimeError("Invalid interval length!")
        return self.interval_length

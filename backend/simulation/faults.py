import random
from dataclasses import dataclass
from typing import Iterable

from simulation.models import Node


MEAN_NODE_UPTIME_SECONDS = 300.0
MEAN_NODE_REPAIR_SECONDS = 15.0
FAULT_MODEL_NAME = "independent-exponential-node-outages-v1"


@dataclass
class NodeFaultSchedule:
    random_source: random.Random
    next_transition_at: float
    is_down: bool = False

    def transition(self, now: float) -> bool:
        self.is_down = not self.is_down
        mean_duration = (
            MEAN_NODE_REPAIR_SECONDS
            if self.is_down
            else MEAN_NODE_UPTIME_SECONDS
        )
        self.next_transition_at = now + self.random_source.expovariate(
            1.0 / mean_duration
        )
        return self.is_down


def create_node_fault_schedules(
    nodes: Iterable[Node], seed: int
) -> dict[str, NodeFaultSchedule]:
    schedules = {}
    for node in nodes:
        if not node.online:
            continue
        random_source = random.Random(f"{seed}:{node.node_id}")
        schedules[node.node_id] = NodeFaultSchedule(
            random_source=random_source,
            next_transition_at=random_source.expovariate(
                1.0 / MEAN_NODE_UPTIME_SECONDS
            ),
        )
    return schedules


def fault_model_configuration(seed: int) -> dict[str, int | float | str]:
    return {
        "name": FAULT_MODEL_NAME,
        "mean_node_uptime_seconds": MEAN_NODE_UPTIME_SECONDS,
        "mean_node_repair_seconds": MEAN_NODE_REPAIR_SECONDS,
        "seed": seed,
    }

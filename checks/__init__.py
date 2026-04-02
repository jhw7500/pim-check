from __future__ import annotations

from checks.process import ProcessCheck
from checks.cam_state import CamStateCheck
from checks.legacy import LegacyFileCheck
from checks.thermal import ThermalCheck
from checks.jq_fork import JqForkCheck
from checks.log import LogCheck
from checks.recording import RecordingCheck
from checks.custom import CustomCommandCheck

ALL_CHECKS = [
    ProcessCheck(),
    CamStateCheck(),
    LegacyFileCheck(),
    ThermalCheck(),
    JqForkCheck(),
    LogCheck(),
    RecordingCheck(),
    CustomCommandCheck(),
]

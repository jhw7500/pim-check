from __future__ import annotations

import glob
import importlib.util
import os

from checks.process import ProcessCheck
from checks.cam_state import CamStateCheck
from checks.cam_health import CamHealthCheck
from checks.legacy import LegacyFileCheck
from checks.max9296_abi import Max9296AbiCheck
from checks.thermal import ThermalCheck
from checks.jq_fork import JqForkCheck
from checks.log import LogCheck
from checks.recording import RecordingCheck
from checks.custom import CustomCommandCheck
from checks.base_check import BaseCheck
from checks.target_identity import TargetIdentityCheck
from checks.bps_evidence import BpsEvidenceCheck
from checks.mixed_combo_evidence import MixedComboEvidenceCheck

ALL_CHECKS = [
    ProcessCheck(),
    CamStateCheck(),
    CamHealthCheck(),
    LegacyFileCheck(),
    Max9296AbiCheck(),
    ThermalCheck(),
    JqForkCheck(),
    LogCheck(),
    RecordingCheck(),
    CustomCommandCheck(),
    TargetIdentityCheck(),
    BpsEvidenceCheck(),
    MixedComboEvidenceCheck(),
]


def checks_for_scope(scope: str) -> list[BaseCheck]:
    """Return only checks explicitly registered for one execution scope."""
    return [check for check in ALL_CHECKS if check.scope == scope]


def load_plugins(plugin_dir: str | None = None) -> list:
    """plugins/ 디렉토리에서 BaseCheck 서브클래스를 자동 로드한다."""
    if plugin_dir is None:
        plugin_dir = os.path.join(os.path.dirname(__file__), "plugins")
    if not os.path.isdir(plugin_dir):
        return []

    loaded = []
    for filepath in sorted(glob.glob(os.path.join(plugin_dir, "*.py"))):
        if os.path.basename(filepath).startswith("_"):
            continue
        module_name = os.path.splitext(os.path.basename(filepath))[0]
        spec = importlib.util.spec_from_file_location(f"checks.plugins.{module_name}", filepath)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (isinstance(attr, type) and issubclass(attr, BaseCheck)
                    and attr is not BaseCheck):
                instance = attr()
                loaded.append(instance)

    return loaded


# 앱 시작 시 플러그인 자동 로드
_plugins = load_plugins()
if _plugins:
    ALL_CHECKS.extend(_plugins)

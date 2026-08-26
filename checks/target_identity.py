from __future__ import annotations

import re
import shlex
from typing import Any, Dict, List, Optional, Tuple

from checks.base_check import BaseCheck


_ALLOWED_KINDS = {"module_sha256", "module_version", "file_sha256"}
_ALLOWED_PATH_PREFIXES = ("/boot/", "/lib/modules/", "/root/shared_v/")
_MODULE_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _allowed_path(path: object) -> bool:
    return isinstance(path, str) and any(path.startswith(prefix) for prefix in _ALLOWED_PATH_PREFIXES)


def _sha256_from_output(output: object) -> Optional[str]:
    if not isinstance(output, str):
        return None
    fields = output.strip().split()
    if not fields or not _SHA256_RE.fullmatch(fields[0]):
        return None
    return fields[0]


class TargetIdentityCheck(BaseCheck):
    """Collect allowlisted target identity descriptors through ``SshClient.run``."""

    name = "target_identity"
    scope = "hardware_evidence"

    def _resolved_path(self, ssh, path: str) -> Tuple[Optional[str], Optional[str]]:
        resolved = ssh.run("readlink -f -- {0}".format(shlex.quote(path)))
        resolved = resolved.strip() if isinstance(resolved, str) else ""
        if not resolved:
            return None, "could not resolve target path"
        if not _allowed_path(resolved):
            return None, "resolved path is outside the hashing allowlist"
        return resolved, None

    def _hash_path(self, ssh, path: str) -> Optional[str]:
        return _sha256_from_output(ssh.run("sha256sum -- {0}".format(shlex.quote(path))))

    def collect(self, ssh, config: dict) -> dict:
        descriptors = config.get("target_identity")
        errors: List[str] = []
        claims: List[Dict[str, Any]] = []
        if not isinstance(descriptors, list) or not descriptors:
            return {"claims": claims, "errors": ["no identity claims configured"]}

        for descriptor in descriptors:
            if not isinstance(descriptor, dict):
                errors.append("identity descriptor is not an object")
                continue
            identifier = descriptor.get("id")
            kind = descriptor.get("kind")
            if not isinstance(identifier, str) or not identifier:
                errors.append("identity descriptor has no id")
                continue
            if kind not in _ALLOWED_KINDS:
                errors.append("{0}: unsupported descriptor kind".format(identifier))
                continue
            if kind.startswith("module_"):
                module = descriptor.get("module")
                if not isinstance(module, str) or not _MODULE_RE.fullmatch(module):
                    errors.append("{0}: unsafe module name".format(identifier))
                    continue
                if kind == "module_version":
                    expected = descriptor.get("version")
                    raw = ssh.run("modinfo {0}".format(module))
                    match = re.search(r"^version:\s*(\S+)", raw or "", re.MULTILINE)
                    if not match:
                        errors.append("{0}: modinfo version not found".format(identifier))
                        continue
                    claims.append({
                        "id": identifier, "kind": kind, "module": module,
                        "expected": expected, "actual": match.group(1),
                    })
                    continue

                expected = descriptor.get("sha256")
                module_path = ssh.run("modinfo -n {0}".format(module))
                module_path = module_path.strip() if isinstance(module_path, str) else ""
                if not module_path:
                    errors.append("{0}: modinfo module path not found".format(identifier))
                    continue
                resolved, error = self._resolved_path(ssh, module_path)
                if error:
                    errors.append("{0}: {1}".format(identifier, error))
                    continue
                actual = self._hash_path(ssh, resolved)
                if actual is None:
                    errors.append("{0}: sha256sum did not return one SHA-256".format(identifier))
                    continue
                claims.append({
                    "id": identifier, "kind": kind, "module": module, "path": resolved,
                    "expected": expected, "actual": actual,
                })
                continue

            path = descriptor.get("path")
            expected = descriptor.get("sha256")
            if not isinstance(path, str) or not path.startswith("/"):
                errors.append("{0}: file path is unsafe".format(identifier))
                continue
            resolved, error = self._resolved_path(ssh, path)
            if error:
                errors.append("{0}: {1}".format(identifier, error))
                continue
            actual = self._hash_path(ssh, resolved)
            if actual is None:
                errors.append("{0}: sha256sum did not return one SHA-256".format(identifier))
                continue
            claims.append({
                "id": identifier, "kind": kind, "path": resolved,
                "expected": expected, "actual": actual,
            })
        return {"claims": claims, "errors": errors}

    def validate(self, data: dict, config: dict) -> tuple[bool, str]:
        if not isinstance(data, dict):
            return False, "identity evidence is not an object"
        errors = data.get("errors")
        if not isinstance(errors, list):
            return False, "identity evidence errors are malformed"
        if errors:
            return False, "; ".join(str(error) for error in errors)
        claims = data.get("claims")
        descriptors = config.get("target_identity")
        if not isinstance(descriptors, list) or not descriptors:
            return False, "no identity claims configured"
        if not isinstance(claims, list) or len(claims) != len(descriptors):
            return False, "identity claims are incomplete"
        for claim in claims:
            if not isinstance(claim, dict):
                return False, "identity claim is malformed"
            expected = claim.get("expected")
            actual = claim.get("actual")
            if not isinstance(expected, str) or not isinstance(actual, str):
                return False, "identity claim has no comparable value"
            if expected != actual:
                return False, "identity claim {0} mismatch".format(claim.get("id", "unknown"))
        return True, "OK"

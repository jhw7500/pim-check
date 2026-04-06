"""
user_config.py — 사용자 설정 파일 관리

~/.pim-check.yaml에 기본 설정을 저장하여 매번 CLI 입력을 줄인다.
"""
from __future__ import annotations

import os

import yaml

DEFAULT_PATH = os.path.expanduser("~/.pim-check.yaml")


def load_user_config(path: str = DEFAULT_PATH) -> dict:
    """사용자 설정 파일을 로드한다. 없으면 빈 dict."""
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def save_user_config(config: dict, path: str = DEFAULT_PATH) -> None:
    """사용자 설정을 파일에 저장한다."""
    with open(path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)


def init_user_config(path: str = DEFAULT_PATH) -> str:
    """기본 설정 파일을 생성한다. 이미 있으면 경로만 반환."""
    if os.path.exists(path):
        return path
    default = {
        "default_host": "192.168.0.5",
        "default_user": "root",
        "default_password": "root",
        "webhook_url": "",
        "log_enabled": False,
    }
    save_user_config(default, path)
    return path


def apply_defaults(args, config: dict) -> None:
    """CLI args에 사용자 설정의 기본값을 적용한다. CLI 값이 우선."""
    if args.host is None and config.get("default_host"):
        args.host = config["default_host"]
    if args.user is None and config.get("default_user"):
        args.user = config["default_user"]
    if args.password is None and config.get("default_password"):
        args.password = config["default_password"]
    if args.webhook is None and config.get("webhook_url"):
        args.webhook = config["webhook_url"]

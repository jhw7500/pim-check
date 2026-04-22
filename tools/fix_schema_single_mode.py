"""
schema.yaml 단독-모드 테스트 결함 정정.

결함 1: per-channel 축(vflip/hflip/ae/awb_chN)이 대상 채널만 enable=true로 설정하고
        나머지 채널을 명시 disable하지 않아, 베이스 edgeconf 상태에 따라 dual/quad
        모드로 실행되어 single-mode 버그를 은폐했음.

결함 2: 검증 커맨드가 `dual 주소 || single 주소` fallback 패턴이라서 어느 모드로
        돌아가든 맞는 값이 나오면 PASS — 실제 동작 모드를 검증하지 못함.

정정:
  - 각 per-channel 축의 values에 대상 외 3채널 enable=false 명시
  - 검증 커맨드의 dual fallback 제거 → single mode 주소(0x3c)만 읽도록 변경
  - hflip 축은 enable:true가 빠져 있어 추가

스크립트는 in-place로 schema.yaml을 수정한다.
실행 전 백업 권장: cp profiles/schema.yaml profiles/schema.yaml.bak
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

SCHEMA_PATH = Path(__file__).parent.parent / "profiles" / "schema.yaml"

# 모든 채널 키 매핑 (channel-id → edgeconf jq path)
ALL_CHANNELS = {
    "ch0": ".VHL_CAM.i2c2.ch0.enable",
    "ch1": ".VHL_CAM.i2c2.ch1.enable",
    "ch2": ".VHL_CAM.i2c1.ch2.enable",
    "ch3": ".VHL_CAM.i2c1.ch3.enable",
}


def transform_dual_fallback(content: str) -> tuple[str, int]:
    """검증 커맨드의 dual 주소 fallback 제거.

    Before:
      (i2ctransfer -f -y 2 w2@0x11 0x10 0x0c r2 2>/dev/null || \
       i2ctransfer -f -y 2 w2@0x3c 0x10 0x0c r2 2>/dev/null) | tr -d ' '
    After:
      i2ctransfer -f -y 2 w2@0x3c 0x10 0x0c r2 2>/dev/null | tr -d ' '
    """
    pattern = re.compile(
        '"\\(i2ctransfer -f -y (\\d+) w2@0x(?:11|12) '
        "(0x[0-9a-fA-F]+) (0x[0-9a-fA-F]+) r2 2>/dev/null \\|\\| "
        "i2ctransfer -f -y \\1 w2@0x3c \\2 \\3 r2 2>/dev/null\\) "
        "\\| tr -d ' '\""
    )
    replacement = (
        "\"i2ctransfer -f -y \\1 w2@0x3c \\2 \\3 r2 2>/dev/null | tr -d ' '\""
    )
    new_content, count = pattern.subn(replacement, content)
    return new_content, count


def add_other_channel_disable(content: str) -> tuple[str, int]:
    """per-channel 축의 values 블록에 대상 외 3채널 enable=false 추가.

    대상 패턴: values 블록이 enable:true + 동일 채널의 다른 속성 한 줄로 끝나는 경우.
    (channel_combo 축은 모든 채널이 명시되어 있으므로 이 패턴에 매칭되지 않음)

    Before:
                  values:
                    ".VHL_CAM.i2c2.ch1.enable": true
                    ".VHL_CAM.i2c2.ch1.vflip": true
    After:
                  values:
                    ".VHL_CAM.i2c2.ch0.enable": false
                    ".VHL_CAM.i2c1.ch2.enable": false
                    ".VHL_CAM.i2c1.ch3.enable": false
                    ".VHL_CAM.i2c2.ch1.enable": true
                    ".VHL_CAM.i2c2.ch1.vflip": true
    """
    # 매칭: values: 줄 + enable:true + 동일 채널의 attr 줄
    pattern = re.compile(
        r'(            values:\n)'
        r'(              "\.VHL_CAM\.(i2c[12])\.(ch\d)\.enable": true\n)'
        r'(              "\.VHL_CAM\.\3\.\4\.[a-z_]+": [^\n]+\n)',
        re.MULTILINE,
    )

    def repl(match: "re.Match[str]") -> str:
        values_line = match.group(1)
        enable_line = match.group(2)
        # group 3=bus, group 4=channel, group 5=attr line
        target_ch = match.group(4)
        attr_line = match.group(5)

        # 대상 외 3채널 disable 라인 (ch0, ch1, ch2, ch3 순서 유지)
        disable_lines = [
            f'              "{path}": false\n'
            for ch_id, path in ALL_CHANNELS.items()
            if ch_id != target_ch
        ]

        return values_line + "".join(disable_lines) + enable_line + attr_line

    new_content, count = pattern.subn(repl, content)
    return new_content, count


def fix_hflip_axis(content: str) -> tuple[str, int]:
    """hflip 축은 enable:true가 없음 → values에 ch0.enable=true + 다른 채널 disable 추가.

    Before:
              - name: "hflip_off"
                values:
                  ".VHL_CAM.i2c2.ch0.hflip": false
                verify:
    After:
              - name: "hflip_off"
                values:
                  ".VHL_CAM.i2c2.ch1.enable": false
                  ".VHL_CAM.i2c1.ch2.enable": false
                  ".VHL_CAM.i2c1.ch3.enable": false
                  ".VHL_CAM.i2c2.ch0.enable": true
                  ".VHL_CAM.i2c2.ch0.hflip": false
                verify:
    """
    pattern = re.compile(
        r'(            values:\n)'
        r'(              "\.VHL_CAM\.i2c2\.ch0\.hflip": (?:true|false)\n)'
        r'(            verify:)',
        re.MULTILINE,
    )

    def repl(match: "re.Match[str]") -> str:
        values_line = match.group(1)
        hflip_line = match.group(2)
        verify_line = match.group(3)
        # 대상=ch0, 나머지 disable
        disable_lines = []
        for other_ch, other_path in ALL_CHANNELS.items():
            if other_ch == "ch0":
                continue
            disable_lines.append(f'              "{other_path}": false\n')
        enable_line = '              ".VHL_CAM.i2c2.ch0.enable": true\n'
        return values_line + "".join(disable_lines) + enable_line + hflip_line + verify_line

    new_content, count = pattern.subn(repl, content)
    return new_content, count


def main() -> int:
    content = SCHEMA_PATH.read_text()
    original = content

    # 1. hflip 축 먼저 (enable:true 라인 추가)
    content, hflip_count = fix_hflip_axis(content)

    # 2. 그 외 per-channel 축들 (enable:true 앞에 다른 채널 disable 삽입)
    content, disable_count = add_other_channel_disable(content)

    # 3. 검증 커맨드 dual fallback 제거
    content, fallback_count = transform_dual_fallback(content)

    if content == original:
        print("변경사항 없음")
        return 1

    SCHEMA_PATH.write_text(content)

    print(f"✅ schema.yaml 정정 완료:")
    print(f"   - hflip 축 enable+disable 삽입: {hflip_count}")
    print(f"   - per-channel 축 disable 삽입: {disable_count}")
    print(f"   - dual fallback 제거: {fallback_count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

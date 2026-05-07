#!/bin/bash
# vflip self-relative frame comparison test
# - ch0만 활성 + vflip OFF/ON 두 시나리오 녹화
# - 마지막 frame 추출 → 로컬로 가져옴 → ffmpeg로 비교
# - frame_off를 수직 flip 한 영상이 frame_on과 일치해야 PASS

set -uo pipefail

TARGET=${TARGET_HOST:-192.168.0.5}
SSH="sshpass -p root ssh -o StrictHostKeyChecking=no -o LogLevel=ERROR -o ConnectTimeout=5 root@$TARGET"
SCP_OPTS="-o StrictHostKeyChecking=no -o LogLevel=ERROR"
WORK=/tmp/vflip_compare
LOCAL=$(dirname "$0")/vflip_compare_artifacts
mkdir -p "$LOCAL"

apply_and_record() {
  local label=$1
  local vflip=$2
  echo "================================="
  echo "[$label] edgeconf 적용 (ch0 only, vflip=$vflip)"
  echo "================================="
  $SSH "
    jq '
      .VHL_CAM.cam_width = 1280 |
      .VHL_CAM.cam_height = 720 |
      .VHL_CAM.fps = 15 |
      .VHL_CAM.recording_time = 1 |
      .VHL_CAM.muxer = \"mp4\" |
      .VHL_CAM.capture.enable = false |
      .VHL_CAM.i2c2.ch0.enable = true |
      .VHL_CAM.i2c2.ch0.bps = [2048, 1024] |
      .VHL_CAM.i2c2.ch0.vflip = $vflip |
      .VHL_CAM.i2c2.ch0.hflip = false |
      .VHL_CAM.i2c2.ch0.ae_on = true |
      .VHL_CAM.i2c2.ch0.awb = \"auto\" |
      .VHL_CAM.i2c2.ch1.enable = false |
      .VHL_CAM.i2c1.ch2.enable = false |
      .VHL_CAM.i2c1.ch3.enable = false
    ' /root/shared_v/edgeconf_pim.json > /tmp/e.json && mv /tmp/e.json /root/shared_v/edgeconf_pim.json && echo OK
  "
  echo "[$label] reboot"
  $SSH "sync && reboot &" >/dev/null 2>&1
  sleep 5
  echo "[$label] SSH 복구 대기"
  for i in $(seq 1 60); do
    if $SSH "echo ok" 2>/dev/null | grep -q ok; then echo "[$label] up after ${i}s"; break; fi
    sleep 3
  done
  echo "[$label] 30s stabilize"
  sleep 30
  echo "[$label] 75s 녹화 대기 (recording_time=1m + buffer)"
  sleep 75

  # 최신 ch0 mp4 찾고 마지막 frame 추출
  echo "[$label] frame 추출"
  $SSH "
    LATEST=\$(find /dev/shm/recordings -name '*-ch0.mp4' -printf '%T@ %p\n' 2>/dev/null | sort -n | tail -1 | cut -d' ' -f2-)
    [ -z \"\$LATEST\" ] && { echo NO_VIDEO; exit 1; }
    echo \"video: \$LATEST\"
    DUR=\$(ffprobe -v error -show_entries format=duration -of csv=p=0 \"\$LATEST\" 2>/dev/null)
    echo \"duration: \$DUR\"
    # 중간 frame (10초 위치)
    ffmpeg -y -ss 10 -i \"\$LATEST\" -frames:v 1 -f image2 /tmp/frame_${label}.png 2>&1 | tail -3
  "
  # 로컬로 가져오기
  sshpass -p root scp $SCP_OPTS root@$TARGET:/tmp/frame_${label}.png "$LOCAL/frame_${label}.png"
  ls -la "$LOCAL/frame_${label}.png"
  echo "[$label] 완료"
  echo
}

# 시작 시각
echo "## vflip frame comparison test ##"
echo "현재 시각: $(date +%H:%M:%S)"
date -Iseconds > "$LOCAL/run.txt"

# 1. vflip OFF 녹화
apply_and_record "off" "false"

# 2. vflip ON 녹화
apply_and_record "on" "true"

# 3. 비교: frame_off를 수직 flip 한 영상이 frame_on과 일치해야 함
echo "================================="
echo "## frame 비교"
echo "================================="
ffmpeg -y -i "$LOCAL/frame_off.png" -vf "vflip" "$LOCAL/frame_off_flipped.png" 2>&1 | tail -2

echo
echo "[직접 비교] frame_off ↔ frame_on (서로 달라야 함, 즉 SSIM 낮아야)"
ffmpeg -i "$LOCAL/frame_off.png" -i "$LOCAL/frame_on.png" \
  -filter_complex "ssim" -f null - 2>&1 | grep -i ssim | tail -3

echo
echo "[검증] frame_off_flipped ↔ frame_on (일치해야 함, 즉 SSIM 높아야)"
ffmpeg -i "$LOCAL/frame_off_flipped.png" -i "$LOCAL/frame_on.png" \
  -filter_complex "ssim" -f null - 2>&1 | grep -i ssim | tail -3

echo
echo "## 산출물:"
ls -la "$LOCAL/"
echo "끝: $(date +%H:%M:%S)"

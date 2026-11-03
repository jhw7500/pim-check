# pim-check 비양산 경량 CI·소스 반영 계약

## 목적

`pim-check`는 현재 **비양산 QA 개발 프로젝트**다. 개발 커밋마다
`jhw-pr`와 `pre-pr-tribunal`을 호출하지 않고, 기존 CI 검증 결과와
정확한 Git 커밋 및 콘텐츠 SHA256으로 소스 후보를 검토한다.

**이 문서는 비싼 AI 코드 리뷰 제거에 관한 정책이다.**
실보드 시험 권한, 사용자 소유 빌드서버 파일의 수정 권한, 안전 검사에서
이전에 차단된 staging의 재시도 허가는 아니다. 양산용 저장소 및
`main`/제품 릴리스의 강화된 승인 경계는 그대로 유지한다.

## 구현된 경량 CI (추가 AI 리뷰 호출 0회)

`.github/workflows/test.yml`의 `push` 트리거는 오직
`main`과 `feat/qa-recovery-ord-vcm-20261026`에 대해 유효하다.
다른 프로젝트/브랜치로 확대하지 않는다.

- 기능 브랜치에 일반 Git commit/push 후 이미 있던 `lint`,
  Python 3.9/3.11/3.12 `test`, `js-smoke` 실행. PR을 요구하지 않는다.
- 세 Job이 모두 성공한 경우에만 `nonprod-source-candidate`가 동작한다.
  이 작업은 GitHub-hosted `ubuntu-24.04`, `contents:read` 한정이다.
- `scripts/qa_nonprod_source_manifest.py`가 CI checkout `HEAD`와
  GitHub event SHA/branch가 **정확히 같은지**, Git worktree가 깨끗한지,
  모든 추적 파일이 안전한 일반 파일인지 확인하고 각 파일의 SHA256을 기록한다.
- `nonprod-source-manifest.json`을 SHA 기준으로 최대 14일간
  GitHub Actions artifact로 제공한다. 실제 소스 파일의 새 배포 패키지나
  호스트 접근 정보는 포함하지 않는다.
- **명시적으로 `source_candidate_status=OWNER_REVIEW_REQUIRED`**,
  `source_delivery_completed=false`,
  `buildserver_deployment_authorized=false`,
  `pim_board_execution_authorized=false`이다.

### 소스 승인/반영 경계

일반 개발 커밋은 기존 검증된 기능 브랜치에 직접 푸시할 수 있다.
**이것만으로 빌드서버 적용 권한이 생기지 않는다.**

1. 검증된 기능 브랜치의 정확한 Git SHA를 정하고, 해당 SHA의 GitHub
   `Tests` 3개 Job과 `nonprod-source-candidate`가 모두 성공했는지 확인한다.
   CI 기록이 없거나 이전 SHA의 성공이라면 소스 반영을 차단한다.
2. 소스 manifest와 실제 받을 빌드서버 Git status/HEAD를 대조한다.
   기존 `pim-check/main`의 사용자 미커밋 13경로를 보존하며
   `git reset`, `stash`, 강제 `checkout`, 강제 `pull`, 충돌 덮어쓰기를
   자동 수행하지 않는다.
3. **소유자가 명시적으로 승인한 별도의 정식 소스 수신·적용 경로**에서만
   파일을 반영하고 설치 파일 SHA256/모드/의존성을 readback 검증한다.
   이전 안전 검사에서 거부된 별도 소스 staging을 CI artifact, 공유폴더,
   GitHub Actions, 우회 명령 또는 호스트 임의 실행으로 대체하지 않는다.
4. 검증 후에도 S0/S1의 `pim-system-live-readonly` 호스트 등록형
   읽기 전용 프로파일은 별도 owner-owned executable SHA, 입력 해시,
   공식 board lease와 일회성 사용자 승인 절차를 거쳐야 한다.
   고위험 S2~S6는 Owner writer fence와 독립 자동 원복이 실제 설치·검증되어야 한다.

## 판정 구분

| 신호 | 의미 |
| --- | --- |
| Branch `Tests` green | 정확한 소스 커밋의 CI 기본 검증 성공 (아직 제품 아님) |
| `nonprod-source-manifest.json` 생성 | 검토용 소스 해시 증거 확보 (아직 설치 아님) |
| Owner-reviewed source delivery + buildserver readback | 특정 수신 환경에 소스가 정확히 반영됐다는 별도 증거 |
| Host-profile one-use approval | 요청한 읽기 전용 실보드 시험의 실행 승인 |
| S0/S1 PIM 실보드 PASS | 실제 설치된 통합 시스템의 현재 정상 동작 검증 |
| S2~S6 PASS | 설정 반영·외부 통신·저장·자동 복구·지속 동작까지 입증 |

## 구현 한계

- 현재 MCP의 `prepare_git_operation(action=pr|merge)`는 여전히
  네이티브 `jhw-pr`/`pre-pr-tribunal` handoff를 강제한다.
  이 경량 CI는 해당 도구의 PR/merge 동작을 변경하거나 우회하지 않는다.
- 빌드서버 `pim-check` 소스가 구버전이고 사용자 변경이 남아 있으므로,
  **별도 소유자 승인 source-promotion 정책이 마련되기 전에는**
  빌드서버에 직접 반영하지 않는다.
- GitHub-hosted 워크플로우 작업과 단위 테스트에서 실제 PIM board SSH,
  OHT 메시지, config 변경, 서비스 재시작, 녹화 파일 삭제를 수행하지 않는다.

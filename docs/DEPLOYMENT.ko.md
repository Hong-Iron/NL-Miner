# NL-Miner — 배포 가이드

NL-Miner를 패키징하고, 버전을 매기고, 내보내는 방법. 내보낼 산출물은 셋이고
보통 따로따로 배포합니다.

| 산출물 | 대상 | 빌드 |
|---|---|---|
| **옵시디언 플러그인** | 옵시디언 사용자 | `cd plugin && npm run build` → `main.js` + 벤더링된 `backend/` |
| **Claude Code 스킬** | Claude Code 사용자 | 빌드 없음 — `.claude/skills/nl-miner/` 그 자체 |
| **파이썬 코어** | 라이브러리 재사용 / CI | `src/` + `interface/` + `payload.py` |

**전제 조건 — 모든 산출물이 필요로 하는 환경:**

- **Python ≥ 3.10.** 데이터 모델이 `str | None`을 쓰는데, pydantic이 임포트 시점에
  이를 평가합니다. 더 낮은 파이썬은 pydantic 내부에서 `TypeError`를 냅니다.
- **pydantic 2** (`>=2,<3`). `payload.py`가 v2 전용 API를 씁니다. 핀이 없으면 pip이
  v1을 깔 수 있고 나중에 직렬화에서 터집니다.
- 그 외엔 없음. 네트워크·모델·네이티브 확장 없음. `src/preflight.py`가 시작 시 둘 다
  검사해서 잘못된 환경을 한 문장으로 알려줍니다.
- Node.js는 플러그인을 *빌드*할 때만 필요하고, 실행에는 불필요합니다.

아래 경로는 전부 레포 루트 기준입니다. 구조는 [ARCHITECTURE.md](ARCHITECTURE.md),
사용법은 [USAGE.md](USAGE.md), 이 프로젝트가 뭐고 얼마나 쓸모 있는지는
[OVERVIEW.md](OVERVIEW.md)를 보세요.

> **이 문서는 [DEPLOYMENT.md](DEPLOYMENT.md)의 번역입니다.** 명령어·수치가 어긋나면
> 영문판이 정본입니다. 사실이 바뀌면 **양쪽을 같이** 고치세요 — 한쪽만 고친 문서는
> 없는 문서보다 나쁩니다.

---

## 1. 플러그인 빌드

```sh
cd plugin
npm install        # 최초 1회; esbuild, typescript, obsidian 타입, @types/node 설치
npm run build      # = sync-backend.mjs  →  esbuild (production) → plugin/main.js
```

`npm run build`가 실제로 하는 일:

1. `scripts/sync-backend.mjs` — `src/`, `interface/`, `observer/`, `payload.py`,
   `grammar.cfg`, `requirements.txt`를 `plugin/backend/`로 복사
   (`__pycache__`/`.venv` 제외). **빌드마다 다시 돕니다** — 벤더링된 백엔드가
   레포 수정과 어긋나지 않게 하려고요.
2. `esbuild.config.mjs production` — `src/main.ts` → `plugin/main.js` 번들.
   `obsidian`/`electron`은 external (벤더링할 런타임 의존성이 없습니다).
   `platform: node`, `format: cjs`, target `es2018`.

산출물: `plugin/main.js` (~90 KB). `plugin/backend/`도 빌드 산출물이지만
**의도적으로 커밋합니다** — 플러그인이 자기완결적이어야 하니까요.

> `npm run dev`는 sync + esbuild를 **watch** 모드로 돌립니다.

---

## 2. 볼트에 설치 (로컬 배포)

옵시디언 플러그인 폴더에 최소한 **`main.js` + `manifest.json` + `styles.css` +
`backend/`** 가 있어야 하고, PATH에 pydantic이 깔린 파이썬이 필요합니다.

```sh
VAULT="$HOME/Vault"
DEST="$VAULT/.obsidian/plugins/nl-miner"
mkdir -p "$DEST"
cp plugin/main.js plugin/manifest.json plugin/styles.css -t "$DEST/"
cp -r plugin/backend "$DEST/"
pip install -r "$DEST/backend/requirements.txt"
```

그다음 옵시디언에서 **커뮤니티 플러그인 → NL-Miner 활성화**, 그리고 한 번
**설정 → NL-Miner → Python interpreter: Detect**.

> **흔한 실패:** 시스템 `python3`에 pydantic이 없으면 기본 설정으로는 아무것도
> 안 됩니다. Detect가 못 찾으면 가상환경 경로를 직접 넣으세요
> (예: `/path/to/repo/.venv/bin/python`).

### 개발용 심볼릭 링크 (빌드마다 복사하지 않기)

플러그인 폴더를 볼트에 링크해두면 `npm run build`(또는 `dev` watch)가 바로 반영됩니다.

```sh
VAULT="$HOME/Vault"
DEST="$VAULT/.obsidian/plugins/nl-miner"
mkdir -p "$(dirname "$DEST")"
ln -s "$(pwd)/plugin" "$DEST"       # plugin 디렉터리 통째로 링크
pip install -r plugin/backend/requirements.txt
```

리빌드할 때마다 플러그인을 껐다 켜거나 "Reload app without saving"으로 새 `main.js`를
집어오게 하세요. `npm run dev` watch는 저장할 때마다 `main.js`를 다시 뱉습니다.

---

## 2b. 기존 설치본 업데이트

새 파일을 덮어쓰면 끝입니다. 다만 아래 두 가지는 **자동으로 처리되니** 놀라지
않도록 적어둡니다.

- **폐기된 뷰 타입.** 옵시디언은 열려 있던 창을 뷰 타입으로 `workspace.json`에
  기록합니다. 0.2.0 이전에는 뷰를 셋(`nl-miner-query`, `nl-miner-graph`,
  `nl-miner-vault`) 등록했는데, 지금은 전부 `nl-miner-panel` 하나의 탭입니다.
  플러그인이 로드 시 폐기된 타입의 leaf를 떼어냅니다(`main.ts`의
  `RETIRED_VIEW_TYPES`). 안 그러면 사용자가 "No view of type…" 죽은 탭을 물려받고,
  **workspace 파일에 이름이 남아 있어 껐다 켤 때마다 다시 나타납니다.**
  **저 이름들을 살아있는 뷰에 다시 쓰면 안 됩니다.**
- **스토어 스키마.** `GraphStore._migrate_locked()`가 없는 컬럼을 추가합니다
  (`PRAGMA table_info` + `ALTER TABLE`). `CREATE TABLE IF NOT EXISTS`는 기존
  테이블을 그대로 두기 때문입니다. 옛 DB는 새 컬럼이 NULL인 채로 계속 동작하고,
  읽는 쪽은 NULL을 "모름"으로 처리합니다.

**업데이트 후 *전체 분석*을 다시 돌리라고 안내하세요.** 안 해도 안 깨지지만, 옛
토크나이저로 만든 DB에는 낡은 행이 남습니다 — lemma가 없어서 `tool`/`tools`가
안 묶이고, 마크다운·코드 토큰은 조회 시점에만 걸러집니다. 100개 노트에 몇 초입니다.

---

## 3. 버전 (세 파일을 함께 올리기)

릴리스 전에 **셋 다** 올려야 합니다 — 옵시디언 자동 업데이터가 교차 확인합니다.

- `plugin/manifest.json` → `version` (예: `0.2.0`). 옵시디언이 읽는 **정본** 버전.
- `plugin/versions.json` → `"<version>": "<minAppVersion>"` 매핑. 어느 릴리스가
  어느 최소 앱 버전인지 알려줍니다 (예: `"0.2.0": "1.4.0"`).
- `plugin/package.json` → `version`. 도구 위생상 같게 유지.

```sh
# 예: 0.1.0 → 0.2.0
sed -i 's/"version": "0.1.0"/"version": "0.2.0"/' plugin/manifest.json plugin/package.json
# versions.json: "0.2.0" → minAppVersion 항목 추가
```

`main.js`는 gitignore 대상(빌드 산출물)이고, `plugin/` 아래 나머지는 전부 커밋합니다
(manifest, styles, src, scripts, 벤더링된 backend, README).

---

## 4. GitHub에 릴리스 (업데이트 채널)

옵시디언은 `main` 브랜치가 아니라 레포의 **GitHub Releases**에서 플러그인을
업데이트합니다. 릴리스마다:

1. 깨끗하게 빌드: `cd plugin && npm install && npm run build`
2. 커밋·태그·푸시:
   ```sh
   git tag -a v0.2.0 -m "v0.2.0"
   git push origin v0.2.0
   ```
3. 그 태그로 **GitHub Release**를 만들고 **파일 3개**를 애셋으로 첨부
   (옵시디언이 `<…/releases/latest/download/<file>`로 받아갑니다):
   - `plugin/main.js`
   - `plugin/manifest.json`
   - `plugin/styles.css`

   *(zip으로 묶지 마세요 — 옵시디언은 파일을 직접 가져갑니다.)*
4. `versions.json`에 새 버전 → minAppVersion이 이미 있으므로 기존 설치본이
   자동 업데이트됩니다.

---

## 5. 옵시디언 커뮤니티 플러그인 등록

옵시디언 → 커뮤니티 플러그인 → 찾아보기에서 노출되려면:

1. 레포가 공개이고 위처럼 빌드 파일이 릴리스에 올라가 있어야 합니다.
2. `obsidianmd/obsidian-releases`에 PR을 열어 `community-plugins.json`에 항목 추가:
   ```jsonc
   {
     "id": "nl-miner",
     "name": "NL-Miner",
     "author": "NL-Miner",
     "description": "Find the notes that should link to each other. Indexes your vault into a block/concept graph locally (no LM, no network), suggests unlinked notes that share uncommon concepts, and audits what's orphaned. Korean and English.",
     "repo": "yourname/nl-miner"
   }
   ```
   `description`은 `manifest.json`의 것과 **글자 그대로 같게** 두세요 — 같은 문장의
   사본 두 개라 어긋나면 리뷰어가 알아봅니다.

   리뷰는 수동입니다 (보안 / 샌드박싱 / **플러그인이 주장하는 일을 실제로 하는지**).
   마지막 항목이 §8의 description 규칙이 중요한 이유입니다: 파스 레이어를 내세우면
   리뷰어가 실제 노트를 열었을 때 빈 구절 목록을 보게 됩니다. 병합 전까지는
   사용자가 수동 설치(§2)합니다.

### BRAT으로 베타 배포

스토어 리뷰 없이 빠르게 베타를 돌리려면 **BRAT**(Beta Reviewers Auto-update Tool)
플러그인을 쓰세요. 레포 URL을 BRAT에 넣으면 GitHub Releases에서 설치·업데이트합니다.
BRAT도 `main.js` + `manifest.json` + `styles.css` 애셋이 똑같이 필요합니다.

---

## 6. Claude Code 스킬 배포

스킬은 그냥 파일입니다 — 빌드 없음.

- **프로젝트 범위 (기본):** `.claude/skills/nl-miner/SKILL.md`에 있고 이 레포에
  커밋돼 있습니다. 이 레포를 Claude Code로 열면 자동으로 잡힙니다 — 커밋 말고
  따로 배포할 게 없습니다.
- **개인/전역:** 사용자 전역 스킬 디렉터리에 복사하거나 링크하면 어느 레포에서든
  씁니다:
  ```sh
  mkdir -p ~/.claude/skills
  ln -s "$(pwd)/.claude/skills/nl-miner" ~/.claude/skills/nl-miner
  ```
  여전히 `interface.cli`를 임포트할 수 있는 파이썬(`interface/`, `src/`,
  `payload.py`) + pydantic이 필요하므로, 이 레포 안에 두거나 `PYTHONPATH`를 레포
  루트로 잡으세요. 완전히 이식 가능한 개인 스킬을 원하면 스킬 옆에 `backend/`를
  벤더링하고 SKILL.md가 그걸 호출하게 하세요.

---

## 7. 릴리스 체크리스트

릴리스마다 복사해서 체크하세요:

```
[ ] 모든 코드 커밋 & 버전 브랜치에 푸시
[ ] plugin/manifest.json 버전 올림
[ ] plugin/versions.json  <새버전> -> <minAppVersion> 추가
[ ] plugin/package.json 버전 올림 (일치)
[ ] cd plugin && npm install && npm run build   → main.js 생성, 에러 없음
[ ] cd plugin && npx tsc --noEmit -p tsconfig.json   → 타입 에러 없음
[ ] .venv/bin/python -m pytest test/ -q          → 347 passed (회귀 없음)
[ ] 스모크 1 — 플러그인이 호출하는 방식 그대로 (cwd = 벤더링된 backend):
      (cd plugin/backend && python -m interface.cli view ../../octopus.md --parse --json | head -c 200)
[ ] 스모크 2 — 연결성 경로 (--pos 필요):
      python -m interface.cli --db /tmp/smoke.db index ./docs --pos && \
      python -m interface.cli --db /tmp/smoke.db audit
[ ] manifest.json description이 사실인지 확인 (§8)
[ ] git tag -a v<x.y.z> && git push origin v<x.y.z>
[ ] 태그로 GitHub Release, main.js + manifest.json + styles.css 첨부
[ ] (커뮤니티 스토어면) obsidian-releases community-plugins.json에 PR
[ ] (Claude 스킬) .claude/skills/nl-miner/SKILL.md 커밋; 바뀐 CLI 예제를
    현재 서브커맨드에 맞게 갱신
```

---

## 8. 주의사항

- **`main.js`는 커밋하지 않습니다** (gitignore된 빌드 산출물). §2(로컬 복사)나
  §4(Release 애셋)로만 세상에 나갑니다. 벤더링된 `plugin/backend/`는 **커밋합니다** —
  갓 clone한 레포에서 `npm run build`만으로 자기완결적이어야 하니까요.
- **`--db`는 서브커맨드 앞에** 와야 합니다. argparse의 강한 계약이라, CLI를 호출하는
  설치 스크립트나 훅을 쓴다면 지켜야 합니다 (플러그인의 `Backend`도, 스킬의 예제도
  그렇게 합니다).
- 플러그인은 **데스크톱 전용** (`isDesktopOnly: true`) — 스토어가 모바일 주장을
  거부하고, 애초에 모바일에서 파이썬 자식 프로세스를 못 띄웁니다.
- v1에서는 **파이썬 인터프리터를 번들하지 마세요** (스토어 리뷰는 "사용자 PATH의
  파이썬을 쓴다"에 더 우호적입니다). 플랫폼별 wheel은 나중 선택지.
- **`manifest.json`의 `description`은 플러그인 브라우저에 나가는 홍보 문구입니다** —
  대부분의 사람이 설치 전에 읽는 유일한 문장입니다. 파이프라인이 아니라 **사용자에게
  뭘 해주는지**("링크되어야 할 노트를 찾아준다")를 쓰고, **결과를 못 내는 레이어를
  절대 광고하지 마세요**: 이 프로젝트의 파스 레이어는 실제 산문에서 약 2%
  커버리지입니다 ([OVERVIEW.md](OVERVIEW.md#3-what-each-layer-is-actually-worth)).
  "phrase & dependency parse"를 앞세운 설명은 플러그인이 지킬 수 없는 약속입니다.
- **프라이버시는 기능이고 홍보 문구에 들어가야 합니다**: 모델 없음, 네트워크 호출
  없음 — 백엔드는 로컬 자식 프로세스고 스토어는 볼트 안의 파일 하나입니다.

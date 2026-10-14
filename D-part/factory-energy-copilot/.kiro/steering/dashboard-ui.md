---
inclusion: always
---

# Factory Energy Copilot 대시보드 UI 디자인 시스템 (Steering)

## A-0. 목업은 "레이아웃·색·위계" 참고용이다. 목업의 숫자·라벨·설비명은 전부 가짜다

목업과 실제 데이터 사이에 다음 차이가 있다. 이 차이가 있는 곳에서는 **항상 데이터와 requirements/design 문서가 이긴다.**

| 목업 내용 | 실제/규칙 | 처리 |
|---|---|---|
| Boiler-01, Chiller-02, Module 07 같은 설비명 | 실제 module 값은 `1(PM-3)`, `2(L-1전등)`, `3(분쇄기(2))`, `4(분쇄기(1))`, `5(좌측분전반)`, `11(우측분전반1)`, `12(4호기)`, `13(3호기)`, `14(2호기)`, `15(예비건조기)`, `16(호이스트)`, `17(6호기)`, `18(우측분전반2)` (13개) | artifacts의 `module` 값을 그대로 표시. 하드코딩 금지 |
| 시간별 전력량이 10,000~40,000 사이에서 크게 출렁임 | 실제 공장 전체 hourly_pow는 약 39,000 부근, 변동폭이 수백 단위로 매우 작다 | 선 그래프 y축은 데이터 범위에 맞춰 확대하고 "y축 0에서 시작하지 않음" 캡션 표시 |
| 총 전력량 1,248,632 / 요금 187,295 | 5월 505시점 총합은 약 1.97e7(agg_pow), 요금은 ×180이라 수십억 단위 | 숫자는 artifacts에서만. 큰 수는 축약 포맷(억/만 또는 M/B) 통일 |
| 온도(°C), 유량(m³/h), 진동, 출구온도 | 데이터에 없는 센서 | 이벤트 type은 전압·역률·전류·activePower·비율 계열만 |
| "Possible causes: Equipment fault" | C4·R9: "고장" 단정 금지 | "설비 측 요인(현장 확인 필요)" 같은 후보 표현만 |
| Status "Fault", "Energy Intensity kWh/unit", "Top 5 Saving Opportunities + Confidence %", "vs previous period" | 고장 진단·생산량 대비 효율은 비목표, 절감 기회·신뢰도는 근거 데이터 없음 | 화면에 넣지 않는다 |
| 채팅 "KRW 187,295" vs 브리핑 "KRW 187.29M", "kgCO2e" vs "tCO2e" | 같은 값이 두 곳에서 다른 단위 → C2/S5 위반 사례 | 모든 숫자는 공용 포매터 하나로만 출력 |
| 장비 "All: 15 selected" | O-5: 최대 13, 발견된 설비만 | 발견 수를 동적으로 표시, 13과 다르면 안내 |
| Venn 다이어그램(세 원 겹침) | 숫자 합이 안 맞기 쉽고 Streamlit 기본 차트로 정확히 못 그림 | 겹침 표(7개 조합)로 대체 |
| 일별 요금·탄소 막대 | 하루 합계가 거의 같아 막대 높이가 전부 동일 → 정보 없음 | 시간대(0~23시) 평균 프로파일 선 그래프로 대체 |

## A-1. 단위 표기 규칙 [결정 필요 → 기본값 적용]

- hourly_pow = Σ(activePower × 5/3600). activePower 단위는 [미확인]이다.
- 요금·탄소는 **정답 파일과 같은 척도**(NFR-8). 화면에서 단위를 "kWh", "원"으로 단정하지 않는다.
- `config.UNIT_LABELS`를 쓰고, 카드 하단에 `config.UNIT_FOOTNOTE` 캡션을 둔다. 팀이 단위를 확정하면 config 한 곳만 바꾼다.

## A-2. 디자인 토큰 (단일 출처: `src/app/ui/theme.py` `THEMES` + `.streamlit/config.toml`)

라이트 모드 기준 밝은 색 체계. 색 리터럴은 `theme.py`의 `THEMES['light']`에만 둔다(§A-9). 지표색은 채움·선·막대·색 띠용 밝은 색과 글자용 진한 색 한 쌍으로 정의한다.

| 지표(키) | 채움·선·막대 | 글자용 | 사용처 |
|---|---|---|---|
| power 전력량 | `#3B82F6` | `#1D4ED8` | 전력량 KPI, 예측 선·밴드 |
| bill 요금 | `#8B5CF6` | `#6D28D9` | 요금 KPI, 요금 프로파일 선 |
| carbon 탄소 | `#10B981` | `#047857` | 탄소 KPI, 탄소 프로파일 선 |
| accuracy MAE·정확도 | `#6366F1` | `#4338CA` | MAE KPI, 베이스라인, 원인 후보 띠 |
| anomaly 이상 징후 | `#F43F5E` | `#BE123C` | 이상 행수 KPI, 히트맵, 이상 비중 막대 |
| info 관찰 사실 등 안내 | `#0EA5E9` | `#0369A1` | 관찰 사실 띠, info 칩 |

| 상태(설비 상태·경고 전용) | 채움 | 글자용 |
|---|---|---|
| ok | `#22C55E` | `#15803D` |
| watch | `#F59E0B` | `#B45309` |
| alert | `#EF4444` | `#B91C1C` |
| unknown(미확정) | `#9CA3AF` | `#6B7280` |

```
중립·브랜드: bg #F5F6F8, surface #FFFFFF, border #E3E6EB, nav #0F1B2D, text #1B2430,
            muted #6B7280, accent #0E9F8E(브랜드·선택 상태), accent_bg #E6F5F3, actual #334155(실제값 선)
3상 선 색(상태색과 겹치지 않게): R #0EA5E9, S #A855F7, T #14B8A6
tint(옅은 배경): 별도 색을 만들지 않고 지표색 + 투명도 0.12 → theme.tint(color, alpha=None)  (라이트 0.12)
RADIUS=8; GAP=16; CARD_PAD=20
FONT="Pretendard, Inter, -apple-system, sans-serif"
NUM_FONT="tabular-nums"
```

- **상태색은 차트 계열색으로 쓰지 않는다.** 설비 상태·경고(상태 칩, 상태 도넛, 구간 이탈 표시, 임계 미달 음영)에만 쓴다.
- **데이터 계열(선·막대·밴드)은 지표색을 쓴다.** accent는 브랜드·선택 표시(상단바, 선택 탭, 사이드바 제목 막대, 기본 아이콘)에만 쓴다.
- 한 화면의 색 수는 지표색 최대 5개 + 상태색 4개 + 중립으로 제한한다. 위 목록 외 새 색을 추가하지 않는다.
- `config.STATUS_THRESHOLDS_APPROVED`가 False면 status는 회색 칩 `미확정` + 이상 비율 막대로만 표시한다(주의 설비 KPI는 unknown 회색 톤).
- 차트는 Plotly 템플릿 하나(`fec_template`, 활성 테마 값으로 생성)로 통일: 흰 배경, 옅은 가로 그리드만, 범례 상단 우측, hover 포맷 공용.
- 접근자: `theme.metric(name)`, `theme.metric_text(name)`, `theme.phase('R')`, `theme.tint(color)`, `theme.status_color(label)`, `theme.status_text_color(label)`. 기존 `COLOR`, `STATUS_COLOR`, `DEFAULT_STATUS_COLOR`, `ACCENT_COLORSCALE`는 활성 테마에서 읽어 호환 유지.
## A-3. 레이아웃 골격 — 상단바·페이지 전환·높이 예산 (v2)

- **상단 네이비 바(한 줄)**: `st.container(key="topbar")` + CSS `.st-key-topbar` 로 그린다. 한 줄 안에 브랜드(로고+제품명+팀) | 페이지 전환(`st.segmented_control`) | 역할 표시(텍스트) | 기간 칩 | LLM 칩을 모두 넣는다. 기본 Streamlit 헤더는 CSS로 숨김.
- **페이지 전환**: `st.tabs`가 아니라 `st.segmented_control`을 쓰고 선택값을 `st.session_state.page`에 저장한다. 페이지는 `개요 | 예측 | 이상 탐지 | AI Copilot`, 기본은 **개요**(역할 무관). 역할별 순서는 §A-5 참고(개요는 항상 맨 앞, AI Copilot은 맨 뒤).
- **기간 칩은 페이지별 실제 데이터 기간**을 쓴다. 예측/개요=predictions id 범위, 이상=anomaly_events localtime 최소~최대. 하드코딩 금지. `분석 기간 YYYY-MM-DD ~ YYYY-MM-DD · 검증 모드` 형식. "LIVE"·실시간 배지 금지.
- **사이드바 240px**: 역할 selectbox(최상단) / 날짜범위 date_input(range, min·max=페이지 기간) / 설비 체크박스(전체+발견 n개, 360px 고정 스크롤, `n/13 선택`) / 데이터 출처+생성시각. 섹션 구분선. '선택 역할' 중복·기간 반복 설명줄 금지(§7-6).
- **높이 예산(§A-8)**: 같은 행 패널은 같은 height. 차트 height = `theme.chart_height(panel_height)` = 패널 height − 헤더44 − 패딩32.
- 역할명만 쓰고 개인 이름 없음(REQ-USER). 역할별 기본 **탭(개요 다음 순서)**: `energy`→예측, `facility`→이상.

## A-4. Streamlit 구현 매핑

| 목업 요소 | 구현 |
|---|---|
| 전체 톤 | `.streamlit/config.toml [theme]` + `src/app/ui/style.css` 한 파일 CSS 주입 |
| 상단 네이비 바 | `st.markdown` HTML 헤더, 기본 헤더는 CSS로 숨김 |
| 탭 | `st.tabs`, 역할에 따라 순서 변경 |
| KPI 카드 | 공용 `kpi_card(title, value, unit, formula, footnote)` HTML. `st.metric` 미사용 |
| 실제 vs 예측 + 구간 | Plotly: 구간 `fill="tonexty"` 두 trace, 이탈점 빨간 marker |
| 설비 상태표 | `st.dataframe` + `ProgressColumn`(비율) + `LineChartColumn`(24h) + 상태 칩 |
| 히트맵 | Plotly `go.Heatmap`, accent 단색 스케일 |
| 방법 비교 | 표 2개(방법별 건수 / 겹침 7조합). 캡션 "규칙 대비 프록시 지표 — 정답 아님"(B3) |
| 이벤트 로그 | `st.dataframe`, Detected by는 `규칙 ● 통계 ○ IF ●` |
| 드릴다운 | 상태표 행 선택(`on_select="rerun"`) → `st.session_state.selected_module` → 하단 상세 |
| 3상 동기 차트 | Plotly `make_subplots(rows=2, shared_xaxes=True)`, 임계선 config 상수(210/85) |
| AI 채팅 | `st.chat_message` + `st.chat_input`, 답변 아래 출처 칩 |
| 브리핑 전환 | `st.segmented_control` 또는 `st.radio(horizontal=True)` |
| 계산값 vs AI | 계산값 카드=회색+고정폭+"Python 계산값", AI 문장=흰 배경+"AI 서술" (D4) |
| LLM 상태 | 우상단 칩 `LLM 연결됨` / `대체 모드: 규칙 기반 요약`(C5) |

## A-5. 페이지별 사양 (개요/예측/이상/상세/AI, v2)

**개요(Overview) — 기본 페이지, 역할 무관**: 목업 ⑤ 골격 `[9|3]` 2열. 상단 KPI 4개(총 전력량/요금/탄소/기간 이상 행수) → 좌측 주 영역(실제 vs 예측 소형 차트 → 설비별 이상 비중 가로막대[학습기간 2024-12-01~2025-04-30, 제목에 기간 명시, 전력 비중 artifact 없어 규칙 이상 건수 비중으로 대체] → 시간대 프로파일) / 우측 보조(상태 요약 도넛 → 이상 징후 상위 5설비 → 최근 경고 → AI 요약 3줄). 비목표 요소(에너지 집약도·설비 결함 상태·절감 기회·확신도·전기간 대비 증감)는 넣지 않고 유효 콘텐츠로 교체.

**예측 (energy 기본)**: KPI 4개(총 전력량/요금/탄소/MAE 504·505, 각 계산식) → `[8|4]` 주 차트(실제 vs 예측 + 구간, 범례 `예측구간 P5–P95 (잠정)`, y축 확대+캡션) + 경고 패널(구간 이탈·급증, 없으면 "해당 없음") → 베이스라인 비교표(HTML 정적 표, 선택 모델 행 강조) → 시간대 프로파일(0~23시 요금·탄소) → 푸터 `MAE 73/89는 참고용(합격 기준 아님)`.

**이상 탐지 (facility 기본)**: 요약 카드 3개(발견 n/13·기간 이상 행수[축약 금지, 전체 자릿수]·주의 설비[O-3 미확정이면 "기준 미확정"]) → `[7|5]` 설비 상태표(st.dataframe + ProgressColumn[이상 비율 ×100, `%.2f%%`] + LineChartColumn[주차 추세 스파크라인] + 상태 텍스트 기호 `■ 이상 / ▲ 주의 / ○ 미확정 / — 기록 없음`, 이상 비율 내림차순·0건 맨아래, 최근 관측 `MM-DD HH:MM`, `on_select='rerun'` single-row) + 히트맵(x축 **주차** 단위) → `[5|7]` 방법 비교 2표(HTML 정적 표) + 이벤트 로그 → 행 선택 드릴다운.

**설비 상세 드릴다운(이상 탐지 하단 패널)**: breadcrumb 헤더 + "관찰 기록일 뿐 설비 판정이 아님" → 전압/역률 3상 동기 차트(`make_subplots rows=2 shared_xaxes`, 임계선 config 210/85) **또는** 연속 5초 시계열 미포함 시 `anomaly_events` 점(scatter) + 캡션 `연속 시계열 미포함 — 이상 행만 표시`((b) 방식: `anomaly_context.parquet` 부재) → 세 구획(관찰 사실/원인 후보/현장 확인, 단정 금지). 원본 CSV 접근 금지(S7). 새 artifact 생성 금지.

**AI Copilot**: `[6|6]` 좌 채팅(패널, 추천 질문 3~4, 답변 아래 출처 칩, 숫자 없으면 `데이터에 없음`, 미래 표현 금지) / 우상 브리핑 생성기(`st.segmented_control` 경영진 5줄·에너지·설비) / 우하 "Python 계산값" 패널(브리핑 숫자 원본, 같은 포매터). LLM 상태 칩은 채팅 패널 헤더 우측.

## A-6. 공통 데이터 규칙

- Dashboard는 `artifacts/`만 읽는다(`@st.cache_data` 1회). 원본 CSV 경로 import·참조 금지.
- 숫자 포맷은 `src/app/ui/fmt.py` 하나: `fmt_pow`, `fmt_bill`, `fmt_carbon`, `fmt_mae`, `fmt_pct`. 카드·표·hover·에이전트 모두 이것만.
- 필수 artifact가 없으면 빈 상태 카드(`데이터에 없음: <파일명>`)를 그리고 앱은 죽지 않는다.
- 라벨 언어: 한국어 기본. 기술 용어(MAE, Isolation Forest, P5–P95)는 원어 유지.
- 금지 문자열(소스 전체 grep 0건): "고장", "Fault", "LIVE", "실시간", "Energy Intensity", "Confidence", "Venn". 비목표 개념은 한국어 완곡어로만("설비 결함 상태", "확신도" 등도 화면에는 넣지 않는다).
- 숫자 포매터 추가: `fmt_count`(정수 자릿수 전체, 축약 금지 — 이상 행수 60,312용), `fmt_ratio_pct`(0..1 비율 ×100, `%.2f%%`).

## A-7. 데이터 표시 오류 수정 (§7, v2에서 반드시 지킬 것)

1. 기간 칩 = 페이지별 실제 기간(예측/개요 predictions id, 이상 anomaly_events localtime). 하드코딩 금지.
2. 이상 비율 `0.0050` → `0.50%` : ProgressColumn에는 ratio×100 값을 저장하고 `%.2f%%`로 라벨.
3. 최근 관측 `2025-04-29T23:49:15+09:00` → `04-29 23:49` 로 축약(`ui_logic.fmt_localtime_short`). 전체 타임스탬프는 툴팁/보조.
4. 이상 행 수는 축약 금지, 전체 자릿수(`fmt_count`).
5. 상태표는 이상 비율 내림차순, 0건 설비는 맨 아래(`ui_logic.status_rows_sorted`).
6. 사이드바 '선택 역할' 중복·기간 반복 설명줄 삭제.

## A-8. 패널 시스템 — 가장 중요 (v2)

- 모든 정보 덩어리(KPI 카드 제외)는 `components.panel(height=H, key=...)` = `st.container(border=True, height=H)` 안에 둔다. 패널 밖에 떠 있는 제목 금지 — 제목은 `components.panel_header()`로 패널 첫 줄에.
- 같은 행의 패널은 같은 height. 차트 height = `theme.chart_height(H)` = H − 헤더44 − 패딩32.
- **`st.markdown`으로 위젯을 감싸는 여는/닫는 `<div>` 패턴 절대 금지**(동작 안 함). 카드 모양은 CSS(`[data-testid="stVerticalBlockBorderWrapper"]`, `.fec-*`)로만. 순수 HTML 덩어리(KPI 카드·칩·알림·표·계산값)만 `st.markdown` 한 번에 통째로 렌더하며, 한 호출 안에서 `<div>`는 반드시 같은 호출 안에서 닫는다.
- 차트·표는 `use_container_width=True`. 넘치는 내용은 패널 내부 스크롤.
- 컴포넌트: `panel`, `panel_header`, `page_title`, `kpi_card`, `chip`, `status_text`(텍스트 기호), `html_table`(정적 표, 행 강조), `kv_table`, `calc_card`, `ai_card`/`ai_card_lines`, `source_chip`, `alert_item`, `empty_state`, `breadcrumb`. 아이콘은 `ui/icons.py`의 인라인 SVG(외부 CDN 금지).
- 폰트: Pretendard → "Noto Sans KR" → sans-serif. 페이지 제목 20px/700, 설명 13px muted, 패널 제목 15px/600, KPI 숫자 28px tabular-nums. `st.title` 금지.

## A-9. 색 사용 규칙과 다크 모드 전환 규칙

### 지표별 색 매핑

| 화면 요소 | 색 |
|---|---|
| KPI 카드(`kpi_card(tone=...)`): 상단 4px 띠 + 큰 숫자 | 전력량=power, 요금=bill, 탄소=carbon, MAE=accuracy, 이상 행수=anomaly, 발견 설비=neutral, 주의 설비=기준 미확정이면 unknown(회색)/승인되면 watch |
| 실제 vs 예측 차트 | 실제=actual 실선, 예측=power 점선, 예측구간 밴드=power tint, 구간 이탈=alert |
| 시간대 프로파일 | 요금=bill, 탄소=carbon |
| 히트맵 / 설비별 이상 비중 막대 | 흰색→anomaly 단색 스케일 / anomaly |
| 상태 도넛 | 상태색(미확정=회색) |
| 3상 동기 차트 | R/S/T 3상 선 색, 임계선=muted 점선, 임계 미달 음영=alert tint. 이상 점 그래프는 유형별 3상 선 색 |
| 칩(`chip(tone=...)`) | power/bill/carbon/accuracy/anomaly/info: 해당 tint 배경 + 글자용 진한 색 |
| 패널 헤더 아이콘(`panel_header(tone=...)`) | 해당 지표 패널만 지표색, 기본은 accent |
| 설비 상세 세 구획 | 관찰 사실=info, 원인 후보=accuracy, 현장 확인=accent (왼쪽 4px 띠) |
| 사이드바 섹션 제목 | 앞에 3px accent 막대 |

### 리터럴 규칙

- 16진수 색 리터럴은 `theme.py`의 `THEMES`에만 둔다. `style.css`, `components.py`, `charts.py`, `tabs/*.py`, `ui_logic.py`에는 `#RRGGBB` 금지(`icons.py`의 `currentColor`는 허용). CSS는 `var(--fec-*)`만, Python은 theme 접근자만 쓴다. `tests/test_ui_theme.py`가 정규식으로 검사한다.
- CSS 변수는 `theme.css_variables()`가 활성 테마에서 생성하고 `load_css()`가 `style.css` 앞에 붙인다. 변수 이름: 중립 `--fec-bg`…, 지표 `--fec-c-<metric>` / `-text` / `-tint`, 상태 `--fec-<status>` / `-text` / `-tint`, 3상 `--fec-phase-r|s|t`.
- 모든 테마는 같은 키 집합을 가진다(테스트로 검사). 글자용 색은 surface 대비 명암비 4.5 이상이어야 한다.

### 다크 전환 규칙 (예약, 미구현)

- bg / surface / nav는 짙은 남색 계열로, 글자(text·muted)는 밝은 회색으로 바꾼다.
- 지표 채움색은 유지하거나 한 단계 밝게 한다.
- 글자용 지표색은 한 단계 밝은(300 단계) 색으로 바꾼다.
- tint는 같은 규칙으로 두되 `tint_alpha`를 0.18로 한다.
- 상태색은 같은 색상환을 유지하면서 밝게 한다.
- 테마 키 집합은 light와 동일하게 유지한다.
---

## v3/v4 업데이트 (멘토링 피드백 반영 — 충돌 시 이 절이 위 내용보다 우선)

- **IA(5페이지)**: 홈 · 설비별 분석 · 이상치 모니터링 · AI Copilot · 설정. 기본 페이지는 홈(역할 무관). 기존 "예측"은 홈으로 통합, "개요"는 홈이 대체.
- **내비게이션**: 상단 네이비 바 제거. 왼쪽 사이드바(260px)에 브랜드 + 세로 메뉴(`st.button` + `nav.go` 콜백) + 하단(역할, 데이터 출처, LLM 상태). 현재 페이지는 `st.session_state["page"]` 하나로만 결정(`segmented_control` 내비게이션/`page_switch` 키 금지).
- **필터 바**: 페이지 헤더 오른쪽(기간 단위 시간/일/주 + 날짜 범위 + 알림 벨 팝오버). 사이드바에 필터 위젯 없음. 설비 필터는 설비별 분석·이상치 페이지 안에 둔다.
- **설정(세션 한정)**: 단가·탄소계수·탄소 가격(기본 빈칸)·알림 임계값·야간 시간대. MAE/평가는 항상 evaluation.json(config 기본 계수). 요금·탄소 표시가 기본 계수와 다르면 `설정값 기준` 칩. "탄소세" 대신 "예상 탄소비용(가정)" 사용.
- **오류 vs 빈 상태**: artifact 없음/읽기 실패 = `components.error_state(파일명)`, 요약 숫자는 `—`. 원시 예외 문자열 금지. 정상 0건 = `empty_state`.
- **새 artifact**: `actual_vs_pred.csv`(evaluate 단계 산출, id/actual_pow/pred_pow/pred_low/pred_high). UI는 원본 정답 CSV를 읽지 않는다(S7). `module_quality_hourly.parquet`은 아직 없음 → 3상 품질 지표는 "준비 중".
- **시각 토큰(v4)**: 카드 radius 12, 카드 간 간격 20px, 사이드바 260, KPI 높이 홈 120 / 기타 110. 색은 모두 `theme.py` THEMES(키 추가: faint/hover/chip_bg/grid/bar_gray/partial/warn*/alert_bg/calc*/heat_*). CSS는 `var(--fec-*)`만.
- **Markdown 래퍼 주의**: Streamlit이 markdown 컨테이너에 -1rem 하단 여백을 줘 HTML 카드(.fec-tile 등) 아래 간격이 줄어든다 → `style.css`의 `:has()` 보정 목록에 카드 클래스를 추가한다.

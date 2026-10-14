"""OpenAI function-calling agent with rule-based fallback and cache (8.3 / C2-C5).

Design contract:

- All numbers come from the artifacts-only tools in ``tools.py`` (C2, R10). The
  LLM never computes figures. Anything missing from the tool results is reported
  as ``데이터에 없음`` (C2).
- The agent produces a period briefing and switches tone by audience: executive
  (5 lines or fewer), energy manager, and field operator (C3).
- It never asserts ``고장`` / ``위험 확정``. Anomaly answers are split into three
  sections — observed facts / possible causes / field-check items (C4, R9).
- When ``OPENAI_API_KEY`` is absent or the API call fails, it degrades to the
  rule-based summary **without raising**, and caches results (C5).
- Key and model name are read from the environment only; neither value is logged
  (NFR-6). ``config.get_openai_api_key`` / ``get_openai_model`` are the only
  accessors.

The rule-based path is fully deterministic and testable without network access.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import json
import logging

from config import config
from src.agent import briefing as briefing_mod
from src.agent import tools as tools_mod

logger = logging.getLogger("factory_energy_copilot.agent")
logger.addHandler(logging.NullHandler())

# Audience modes for C3 target switching.
AUDIENCE_EXECUTIVE = "executive"
AUDIENCE_ENERGY = "energy"
AUDIENCE_FIELD = "facility"  # 설비 관리 담당자 (legacy key "field" is aliased in briefing)
AUDIENCES = (AUDIENCE_EXECUTIVE, AUDIENCE_ENERGY, AUDIENCE_FIELD)

# Phrases that must never appear in anomaly answers (C4, R9).
FORBIDDEN_ASSERTIONS = ("고장", "위험 확정")

# Section labels for the three-part anomaly structure (C4).
SECTION_OBSERVED = "관찰된 사실"
SECTION_CAUSES = "가능한 원인 후보"
SECTION_CHECKS = "현장 확인 항목"

NO_DATA = tools_mod.NO_DATA


def _fmt(value: float) -> str:
    """Format a number with thousands separators and up to 2 decimals."""

    return f"{value:,.2f}"


@dataclass
class AgentResult:
    """A single agent answer. ``source`` distinguishes 계산 vs AI (D4)."""

    text: str
    source: str  # "rule" (deterministic) or "llm" (OpenAI narration)
    data: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Rule-based summaries (deterministic; also the fallback for C5).
# ---------------------------------------------------------------------------
def build_forecast_briefing(
    audience: str = AUDIENCE_EXECUTIVE,
    *,
    start: str | None = None,
    end: str | None = None,
    artifacts_dir: Path | None = None,
) -> AgentResult:
    """Rule-based period briefing (C2, C3), built as a structured ``Briefing``.

    ``text`` is the plain-text rendering (copy / chat fallback); the structured
    object is in ``data["briefing"]`` for the card UI. Numbers come only from the
    artifacts-only tools and the shared formatter; missing data yields
    ``데이터에 없음`` lines.
    """

    briefing = briefing_mod.build_briefing(
        audience, start, end, artifacts_dir=artifacts_dir
    )
    return AgentResult(
        text=briefing_mod.to_plaintext(briefing),
        source="rule",
        data={"briefing": briefing},
    )


def build_anomaly_answer(
    module: str | None = None,
    *,
    start: str | None = None,
    end: str | None = None,
    type: str | None = None,  # noqa: A002 - mirrors tool schema
    artifacts_dir: Path | None = None,
) -> AgentResult:
    """Rule-based anomaly answer in the three mandated sections (C4, R9).

    Never asserts 고장/위험 확정; frames findings as observations + candidates +
    checks. All counts come from the artifacts-only tools (C2).
    """

    events = tools_mod.get_anomaly_events(module, start, end, type, artifacts_dir=artifacts_dir)
    status = tools_mod.get_module_status(artifacts_dir=artifacts_dir)

    events_ok = "error" not in events
    event_count = events["count"] if events_ok else 0

    if not events_ok:
        observed = f"이상 이벤트 데이터: {NO_DATA} ({events.get('message', '')})"
        types_text = NO_DATA
    elif event_count == 0:
        observed = "해당 조건에서 관찰된 이상 이벤트가 없습니다."
        types_text = "없음"
    else:
        type_counts: dict[str, int] = {}
        for ev in events["events"]:
            type_counts[ev["type"]] = type_counts.get(ev["type"], 0) + 1
        types_text = ", ".join(f"{t}:{c}건" for t, c in sorted(type_counts.items()))
        scope = f"설비 {module}" if module else "전체 설비"
        observed = f"{scope}에서 이상 이벤트 {event_count}건 관찰 (유형별 {types_text})."

    # Status context (also observation, never assertion).
    status_line = ""
    if "error" not in status and module:
        for row in status["modules"]:
            if row["module"] == module:
                status_line = (
                    f" 설비 상태 라벨: {row['status']} (경계값 미승인 시 '미확정')."
                )
                break

    text = "\n".join(
        [
            f"[{SECTION_OBSERVED}]",
            observed + status_line,
            "",
            f"[{SECTION_CAUSES}]",
            "3상 전압/역률 동시 저하 규칙·통계·비지도 방법이 함께 표시한 구간일 수 있습니다. "
            "원인은 후보이며 단정하지 않습니다.",
            "",
            f"[{SECTION_CHECKS}]",
            "해당 설비의 전압·역률 시계열과 세 방법 비교표를 현장에서 교차 확인하세요. "
            "확정 진단 전까지 '이상 구간/이상 징후'로만 다룹니다.",
        ]
    )

    # Defensive guard: strip any forbidden assertion if it ever slipped in.
    text = _sanitize_assertions(text)

    return AgentResult(text=text, source="rule", data={"events": events, "module_status": status})


def _sanitize_assertions(text: str) -> str:
    """Replace forbidden assertions with non-committal wording (C4)."""

    cleaned = text
    cleaned = cleaned.replace("위험 확정", "위험 가능성(미확정)")
    cleaned = cleaned.replace("고장", "이상 징후")
    return cleaned


def contains_forbidden_assertion(text: str) -> bool:
    """True if any forbidden assertion phrase is present (used by tests, C4)."""

    return any(phrase in text for phrase in FORBIDDEN_ASSERTIONS)


# ---------------------------------------------------------------------------
# Agent facade: OpenAI function calling with cache + fallback (C5, NFR-6).
# ---------------------------------------------------------------------------
class Agent:
    """Agent facade. Uses OpenAI when a key exists, else rule-based fallback."""

    def __init__(self, *, artifacts_dir: Path | None = None) -> None:
        self.artifacts_dir = artifacts_dir
        self._cache: dict[str, AgentResult] = {}

    # -- public API ---------------------------------------------------------
    def briefing(
        self,
        audience: str = AUDIENCE_EXECUTIVE,
        *,
        start: str | None = None,
        end: str | None = None,
    ) -> AgentResult:
        key = f"briefing:{audience}:{start}:{end}"
        if key in self._cache:
            return self._cache[key]
        result = build_forecast_briefing(
            audience, start=start, end=end, artifacts_dir=self.artifacts_dir
        )
        if config.has_openai_key():
            narrated = briefing_mod.narrate_with_llm(result.data["briefing"])
            if narrated.mode == "llm":
                result = AgentResult(
                    text=briefing_mod.to_plaintext(narrated),
                    source="llm",
                    data={"briefing": narrated},
                )
        self._cache[key] = result
        return result

    def anomaly(
        self,
        module: str | None = None,
        *,
        start: str | None = None,
        end: str | None = None,
        type: str | None = None,  # noqa: A002
    ) -> AgentResult:
        key = f"anomaly:{module}:{start}:{end}:{type}"
        if key in self._cache:
            return self._cache[key]
        result = build_anomaly_answer(
            module, start=start, end=end, type=type, artifacts_dir=self.artifacts_dir
        )
        self._cache[key] = result
        return result

    def ask(self, question: str, *, audience: str = AUDIENCE_EXECUTIVE) -> AgentResult:
        """Answer a free-text question.

        Tries the OpenAI path only when a key is configured; any absence or
        failure falls back to the rule-based summary without raising (C5).
        """

        cache_key = f"ask:{audience}:{question}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        result: AgentResult
        if config.has_openai_key():
            try:
                result = self._ask_openai(question, audience)
            except Exception:  # noqa: BLE001 - any failure must fall back (C5)
                logger.warning("openai call failed; using rule-based fallback")
                result = self._ask_rule_based(question, audience)
        else:
            logger.info("no OPENAI_API_KEY; using rule-based fallback")
            result = self._ask_rule_based(question, audience)

        result = AgentResult(
            text=_sanitize_assertions(result.text),
            source=result.source,
            data=result.data,
        )
        self._cache[cache_key] = result
        return result

    # -- internal helpers ---------------------------------------------------
    def _ask_rule_based(self, question: str, audience: str) -> AgentResult:
        """Deterministic routing: anomaly keywords -> anomaly, else briefing."""

        lowered = question.lower()
        anomaly_hint = any(k in question for k in ("이상", "설비", "전압", "역률")) or (
            "anomaly" in lowered
        )
        if anomaly_hint:
            return build_anomaly_answer(artifacts_dir=self.artifacts_dir)
        return build_forecast_briefing(audience, artifacts_dir=self.artifacts_dir)

    def _ask_openai(self, question: str, audience: str) -> AgentResult:
        """OpenAI function-calling path. Key/model read from env only (NFR-6)."""

        from openai import OpenAI  # imported lazily so the dep is optional

        client = OpenAI(api_key=config.get_openai_api_key())
        model = config.get_openai_model()  # value never logged
        system = (
            "너는 공장 에너지 코파일럿이다. 숫자는 반드시 제공된 도구 결과에서만 쓰고, "
            "없으면 '데이터에 없음'이라고 답한다. '고장'·'위험 확정' 같은 단정을 쓰지 말고 "
            "이상 관련 답변은 관찰된 사실/가능한 원인 후보/현장 확인 항목 세 구획으로 나눈다."
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": question},
        ]
        schemas = tools_mod.openai_tool_schemas()

        first = client.chat.completions.create(
            model=model, messages=messages, tools=schemas, tool_choice="auto"
        )
        choice = first.choices[0].message
        tool_calls = getattr(choice, "tool_calls", None) or []

        messages.append(
            {"role": "assistant", "content": choice.content or "", "tool_calls": tool_calls}
        )
        for call in tool_calls:
            fn_name = call.function.name
            try:
                fn_args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                fn_args = {}
            tool_fn = tools_mod.TOOL_REGISTRY.get(fn_name)
            tool_output = (
                tool_fn(artifacts_dir=self.artifacts_dir, **fn_args)
                if tool_fn
                else {"error": "bad_input", "message": f"unknown tool {fn_name}"}
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(tool_output, ensure_ascii=False, default=str),
                }
            )

        if tool_calls:
            second = client.chat.completions.create(model=model, messages=messages)
            answer = second.choices[0].message.content or ""
        else:
            answer = choice.content or ""

        return AgentResult(text=answer, source="llm", data={"audience": audience})

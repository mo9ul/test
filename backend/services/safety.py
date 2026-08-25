"""마스킹·신뢰도 게이트·응답 검증 모듈.

LLM 응답을 그대로 신뢰하지 않는다는 것이 이 모듈의 전제다.
마스킹은 LLM 호출 '전', 게이트/검증은 LLM 호출 '후'에 적용된다.

주의: 이 모듈은 전송/인증 요소를 차단하지 않는다. 전송 버튼까지 에이전트가
직접 클릭해 작업을 완결하는 것이 제품 목표이므로, 민감 요소는 '탐지 후 로깅'만
하고 LLM 전달 목록에서 제외하지 않는다. 실행 자체를 막는 게이트는
confidence 게이트(check_confidence)와 응답 검증(validate_*)뿐이다.
"""

import re

from backend.schemas.request import ElementDTO
from backend.schemas.response import DecideResponse

_RESIDENT_ID_PATTERN = re.compile(r"\d{6}-\d{7}")
_PHONE_PATTERN = re.compile(r"0\d{1,2}-?\d{3,4}-?\d{4}")
_ACCOUNT_NUMBER_PATTERN = re.compile(r"\d{8,}")
_MASK = "****"


def detect_sensitive_elements(
    elements: list[ElementDTO], sensitive_keywords: list[str]
) -> list[ElementDTO]:
    """위험 키워드(전송/인증/삭제 등)가 매칭되는 element를 '탐지'한다.

    반환값은 로깅·관측용이며 호출부는 이 결과로 elements를 걸러내지 않는다.
    전송 단계 도달 여부를 서버 로그에서 추적하기 위한 신호로만 쓴다.
    """
    detected: list[ElementDTO] = []
    for element in elements:
        combined_text = " ".join(filter(None, [element.text, element.content_description]))
        if any(keyword in combined_text for keyword in sensitive_keywords):
            detected.append(element)
    return detected


def mask_sensitive_text(elements: list[ElementDTO]) -> list[ElementDTO]:
    """전화번호, 계좌번호형(8자리 이상 연속 숫자), 주민번호형(6자리-7자리) 패턴을 마스킹한다.

    개인정보가 LLM으로 나가는 것만 막는다. 노드 자체는 그대로 전달되므로
    에이전트의 화면 조작 능력에는 영향을 주지 않는다.
    원본 element는 수정하지 않고 복사본을 반환한다.
    """
    return [
        element.model_copy(
            update={
                "text": _mask_value(element.text),
                "content_description": _mask_value(element.content_description),
            }
        )
        for element in elements
    ]


def _mask_value(value: str | None) -> str | None:
    if value is None:
        return None
    masked = _RESIDENT_ID_PATTERN.sub(_MASK, value)
    masked = _PHONE_PATTERN.sub(_MASK, masked)
    masked = _ACCOUNT_NUMBER_PATTERN.sub(_MASK, masked)
    return masked


def check_confidence(response: DecideResponse, threshold: float) -> DecideResponse:
    """confidence가 threshold 미만이면 status를 ASK_USER로 강제 override한다.

    확신 없는 조작은 실행하지 않고 사용자에게 되묻는다. 전송 화면인지 여부와
    무관하게 동일한 임계값이 적용된다.
    """
    if response.confidence >= threshold:
        return response

    return response.model_copy(
        update={
            "target_node_id": None,
            "action_type": None,
            "input_value": None,
            "status": "ASK_USER",
            "voice_message": response.voice_message
            or "어떻게 해야 할지 확실하지 않아요. 다시 알려주시겠어요?",
            "reason": f"confidence {response.confidence:.2f} below threshold {threshold:.2f}",
        }
    )


def validate_target_node_id(
    response: DecideResponse, elements: list[ElementDTO]
) -> DecideResponse:
    """target_node_id가 요청 elements에 실재하는 id인지 검증한다.

    LLM이 존재하지 않는 노드를 지어내는(hallucination) 경우를 차단한다.
    """
    if response.target_node_id is None:
        return response

    valid_ids = {element.id for element in elements}
    if response.target_node_id in valid_ids:
        return response

    return _to_unsupported(response, "target_node_id not found in elements")


def validate_action(response: DecideResponse) -> DecideResponse:
    """action_type과 나머지 필드의 정합성을 검증한다.

    - 조작 대상이 있으면 action_type이 반드시 있어야 클라이언트가 실행할 수 있다.
    - SET_TEXT인데 input_value가 없으면 클라이언트가 무엇을 입력할지 알 수 없다.
    """
    if response.action_type == "LAUNCH_APP":
        # 앱 실행은 노드가 아니라 패키지명을 대상으로 한다.
        if not response.input_value:
            return _to_unsupported(response, "LAUNCH_APP given without input_value")
        return response

    if response.target_node_id is None:
        return response

    if response.action_type is None:
        return _to_unsupported(response, "target_node_id given without action_type")

    if response.action_type == "SET_TEXT" and not response.input_value:
        return _to_unsupported(response, "SET_TEXT given without input_value")

    return response


def _to_unsupported(response: DecideResponse, reason: str) -> DecideResponse:
    return response.model_copy(
        update={
            "target_node_id": None,
            "action_type": None,
            "input_value": None,
            "status": "UNSUPPORTED",
            "voice_message": "죄송해요, 이 화면에서는 어떻게 해야 할지 모르겠어요.",
            "reason": reason,
        }
    )


# ---------------------------------------------------------------------------
# 되돌릴 수 없는 행동 게이트 (구두 동의)
#
# 위 모듈 설명이 밝힌 대로 이 파일은 원래 "실행을 막는 게이트는 confidence뿐"이었다.
# 이 게이트는 제품 요구사항 — "결제·전송 직전에 구두 동의를 받고, 동의하면 그 버튼까지
# AI가 누른다" — 을 서버단에서 보장하기 위해 추가된 것이다.
#
# 설계상 중요한 점 두 가지:
#  1. 새 status를 만들지 않고 기존 ASK_USER를 재사용한다. Android가 이미 ASK_USER를
#     처리하므로 클라이언트를 한 줄도 고치지 않아도 동의 왕복이 성립한다.
#  2. 프롬프트에 의존하지 않는다. LLM이 확인을 빠뜨려도 서버가 독립적으로 잡는다.
# ---------------------------------------------------------------------------

_HANGUL_BASE = 0xAC00
_HANGUL_LAST = 0xD7A3


def _object_particle(word: str) -> str:
    """받침 유무로 목적격 조사를 고른다. TTS 문장이 어색해지지 않게 한다."""
    last = word.strip()[-1:]
    if not last:
        return "를"
    code = ord(last)
    if not _HANGUL_BASE <= code <= _HANGUL_LAST:
        return ""
    return "을" if (code - _HANGUL_BASE) % 28 else "를"


def is_irreversible(label: str, irreversible_keywords: list[str]) -> bool:
    return bool(label) and any(k in label for k in irreversible_keywords)


def _contains_affirmative(text: str | None, affirmative_words: list[str]) -> bool:
    if not text:
        return False
    return any(word in text for word in affirmative_words)


def _consent_text(goal: str, goal_snapshot: str, user_speech: str | None) -> str:
    """사용자가 확인 질문에 답한 내용만 뽑아낸다.

    클라이언트는 답변을 두 경로 중 하나로 보낸다 — 짧은 예/아니오는 user_speech로,
    그 외는 goal 뒤에 이어붙여서(CLAUDE.md §5-1). 어느 쪽이든 받아야 한다.

    **goal 전체를 보면 안 된다.** 원래 목표가 "엄마한테 사진 보내줘"라면 그 안의 "보내"를
    동의로 오인해 묻지도 않고 전송해버린다. 확인을 요청한 시점 이후에 늘어난 부분만 본다.
    """
    delta = goal[len(goal_snapshot):] if goal.startswith(goal_snapshot) else ""
    return f"{user_speech or ''} {delta}"


def check_irreversible_action(
    response: DecideResponse,
    elements: list[ElementDTO],
    *,
    pending_confirmation: str | None,
    pending_goal_snapshot: str,
    goal: str,
    user_speech: str | None,
    irreversible_keywords: list[str],
    affirmative_words: list[str],
) -> tuple[DecideResponse, str | None]:
    """되돌릴 수 없는 버튼은 구두 동의를 받은 뒤에만 통과시킨다.

    반환값은 (응답, 갱신된 pending_confirmation)이다.

    통과 조건은 두 가지가 모두 참일 때뿐이다 —
      (a) 직전에 바로 그 노드에 대해 확인을 요청해 두었고,
      (b) 확인을 요청한 이후에 들어온 말(user_speech 또는 goal 증분)에 동의 표현이 있다.
    둘 중 하나라도 아니면 다시 물어본다. 사용자가 거절했는데 LLM이 계속 누르려 해도 막힌다.
    """
    if (
        response.status != "CONTINUE"
        or response.action_type != "CLICK"
        or response.target_node_id is None
    ):
        return response, pending_confirmation

    target = next((e for e in elements if e.id == response.target_node_id), None)
    if target is None:
        return response, pending_confirmation

    label = target.label
    if not is_irreversible(label, irreversible_keywords):
        # 다른 행동으로 넘어갔으므로 대기 중이던 확인은 무효가 된다.
        return response, None

    if pending_confirmation == label and _contains_affirmative(
        _consent_text(goal, pending_goal_snapshot, user_speech), affirmative_words
    ):
        return response, None  # 동의 확인됨 → 통과하고 기록을 지운다

    return (
        response.model_copy(
            update={
                "target_node_id": None,
                "action_type": None,
                "input_value": None,
                "status": "ASK_USER",
                "instruction": f"irreversible action '{label}' requires spoken confirmation",
                "voice_message": f"{label}{_object_particle(label)} 진행할까요?",
                "reason": "awaiting spoken confirmation",
            }
        ),
        label,
    )

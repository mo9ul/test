import logging
import time

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.routers.decide import get_ai_client
from backend.schemas.response import DecideResponse
from backend.services.ai_client import AIClientError, MockAIClient
from backend.services.session import session_manager

client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolate_sessions():
    """세션은 프로세스 메모리에 남으므로 테스트마다 비운다.

    history·결정 캐시·화면 반복 카운터가 모두 session_id 단위로 누적되는데
    테스트들이 같은 session_id를 공유하기 때문에, 비우지 않으면 실행 순서에
    따라 결과가 달라진다.
    """
    session_manager.reset()
    yield
    session_manager.reset()

BASE_ELEMENT = {
    "id": 1,
    "text": "조회",
    "content_description": None,
    "class_name": "android.widget.TextView",
    "clickable": True,
    "bounds": [0, 0, 100, 50],
}


def _payload(
    elements: list[dict] | None = None,
    session_id: str = "test-session",
    user_speech: str | None = None,
) -> dict:
    return {
        "session_id": session_id,
        "goal": "엄마한테 사진 보내줘",
        "app_package": "com.kakao.talk",
        "elements": elements if elements is not None else [dict(BASE_ELEMENT)],
        "user_speech": user_speech,
        "history": None,
    }


def _stub_client(response: DecideResponse):
    """고정 응답을 돌려주는 AI 클라이언트 스텁을 주입한다."""

    class StubAIClient:
        def decide(self, goal, app_package, elements, history, user_speech=None, installed_apps=None):
            return response

    app.dependency_overrides[get_ai_client] = lambda: StubAIClient()


def _response(**overrides) -> DecideResponse:
    defaults = {
        "target_node_id": 1,
        "action_type": "CLICK",
        "input_value": None,
        "instruction": "node 1 클릭",
        "voice_message": "누를게요.",
        "confidence": 0.9,
        "status": "CONTINUE",
        "reason": None,
    }
    return DecideResponse(**{**defaults, **overrides})


@pytest.fixture(autouse=True)
def _isolate_ai_client():
    """기본 클라이언트를 Mock으로 고정한다.

    이게 없으면 개발자 .env에 GEMINI_API_KEY가 있을 때 테스트가 실제 API를 호출해
    과금되고, 응답이 비결정적이라 테스트가 흔들린다. 개별 테스트는 이 위에 덮어쓴다.
    """
    app.dependency_overrides[get_ai_client] = lambda: MockAIClient()
    yield
    app.dependency_overrides.pop(get_ai_client, None)


# --- 정상 경로 -------------------------------------------------------------


def test_decide_returns_continue_on_normal_case() -> None:
    response = client.post("/api/v1/decide", json=_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "CONTINUE"
    assert body["target_node_id"] == 1
    assert body["action_type"] == "CLICK"
    assert body["voice_message"]


def test_edit_text_element_returns_set_text_with_input_value() -> None:
    elements = [{**BASE_ELEMENT, "class_name": "android.widget.EditText", "text": None}]

    response = client.post("/api/v1/decide", json=_payload(elements=elements))

    body = response.json()
    assert body["action_type"] == "SET_TEXT"
    assert body["input_value"]


def test_no_clickable_element_returns_ask_user() -> None:
    elements = [{**BASE_ELEMENT, "clickable": False}]

    response = client.post("/api/v1/decide", json=_payload(elements=elements))

    assert response.json()["status"] == "ASK_USER"


# --- 요청 검증 -------------------------------------------------------------


def test_empty_elements_returns_422() -> None:
    response = client.post("/api/v1/decide", json=_payload(elements=[]))

    assert response.status_code == 422


def test_invalid_bounds_returns_422() -> None:
    elements = [{**BASE_ELEMENT, "bounds": [100, 0, 0, 50]}]

    response = client.post("/api/v1/decide", json=_payload(elements=elements))

    assert response.status_code == 422


def test_bounds_with_wrong_length_returns_422() -> None:
    elements = [{**BASE_ELEMENT, "bounds": [0, 0, 100]}]

    response = client.post("/api/v1/decide", json=_payload(elements=elements))

    assert response.status_code == 422


def test_validation_error_uses_common_error_format() -> None:
    response = client.post("/api/v1/decide", json=_payload(elements=[]))

    body = response.json()
    assert body["error_code"] == "VALIDATION_ERROR"
    assert "message" in body


# --- 안전 게이트 -----------------------------------------------------------


def test_low_confidence_is_overridden_to_ask_user() -> None:
    _stub_client(_response(confidence=0.1))

    body = client.post("/api/v1/decide", json=_payload()).json()

    assert body["status"] == "ASK_USER"
    assert body["target_node_id"] is None
    assert body["action_type"] is None


def test_hallucinated_target_node_id_returns_unsupported() -> None:
    _stub_client(_response(target_node_id=999))

    body = client.post("/api/v1/decide", json=_payload()).json()

    assert body["status"] == "UNSUPPORTED"
    assert body["target_node_id"] is None


def test_set_text_without_input_value_returns_unsupported() -> None:
    _stub_client(_response(action_type="SET_TEXT", input_value=None))

    body = client.post("/api/v1/decide", json=_payload()).json()

    assert body["status"] == "UNSUPPORTED"


def test_target_without_action_type_returns_unsupported() -> None:
    _stub_client(_response(action_type=None))

    body = client.post("/api/v1/decide", json=_payload()).json()

    assert body["status"] == "UNSUPPORTED"


def test_ai_client_error_returns_unsupported_not_500() -> None:
    """LLM 호출 실패에도 서버는 계약 스키마로 응답해야 한다 (CLAUDE.md 12장)."""

    class FailingAIClient:
        def decide(self, goal, app_package, elements, history, user_speech=None, installed_apps=None):
            raise AIClientError("Gemini API error 503: unavailable")

    app.dependency_overrides[get_ai_client] = lambda: FailingAIClient()

    response = client.post("/api/v1/decide", json=_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "UNSUPPORTED"
    assert body["target_node_id"] is None
    assert body["voice_message"]


def test_ai_client_error_detail_is_not_leaked_to_client() -> None:
    """API 키나 내부 오류 문자열이 응답 본문으로 새면 안 된다."""

    class FailingAIClient:
        def decide(self, goal, app_package, elements, history, user_speech=None, installed_apps=None):
            raise AIClientError("Gemini API error 401: bad key AIzaSyTOPSECRET")

    app.dependency_overrides[get_ai_client] = lambda: FailingAIClient()

    raw = client.post("/api/v1/decide", json=_payload()).text

    assert "AIzaSyTOPSECRET" not in raw
    assert "401" not in raw


def test_ai_client_timeout_returns_unsupported(monkeypatch) -> None:
    monkeypatch.setattr("backend.routers.decide.AI_CLIENT_TIMEOUT_SECONDS", 0.1)

    class SlowAIClient:
        def decide(self, goal, app_package, elements, history, user_speech=None, installed_apps=None):
            time.sleep(0.5)
            return _response()

    app.dependency_overrides[get_ai_client] = lambda: SlowAIClient()

    response = client.post("/api/v1/decide", json=_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "UNSUPPORTED"
    assert body["reason"] == "AI 응답 지연"


# --- 전송 자동 진행 (차단하지 않아야 함) -----------------------------------


def test_send_element_is_not_filtered_from_ai_input() -> None:
    """전송 버튼은 LLM 입력에서 제외되지 않는다. 에이전트가 전송까지 완결해야 하므로."""
    captured: list = []

    class SpyAIClient:
        def decide(self, goal, app_package, elements, history, user_speech=None, installed_apps=None):
            captured.extend(elements)
            return _response()

    app.dependency_overrides[get_ai_client] = lambda: SpyAIClient()

    elements = [{**BASE_ELEMENT, "text": "전송"}]
    client.post("/api/v1/decide", json=_payload(elements=elements))

    assert [element.id for element in captured] == [1]
    assert captured[0].text == "전송"


def test_send_element_requires_spoken_confirmation() -> None:
    """전송 버튼은 구두 동의 전까지 눌리지 않는다.

    ※ 정책 변경: 이전에는 전송 버튼도 특별 취급 없이 통과시켰다. 제품 요구사항
    ("결제·전송 직전에 동의를 받고, 동의하면 그 버튼까지 AI가 누른다")에 맞춰
    서버가 ASK_USER로 되묻도록 바뀌었다. Android는 기존 ASK_USER 처리를 그대로 쓴다.
    """
    _stub_client(_response(instruction="전송 버튼 클릭"))

    elements = [{**BASE_ELEMENT, "text": "전송"}]
    body = client.post("/api/v1/decide", json=_payload(elements=elements)).json()

    assert body["status"] == "ASK_USER"
    assert body["target_node_id"] is None
    assert body["voice_message"] == "전송을 진행할까요?"


def test_send_element_is_clicked_after_affirmative_confirmation() -> None:
    """동의를 받은 뒤에는 AI가 전송 버튼을 실제로 누른다."""
    _stub_client(_response(instruction="전송 버튼 클릭"))
    elements = [{**BASE_ELEMENT, "text": "전송"}]

    first = client.post("/api/v1/decide", json=_payload(elements=elements)).json()
    assert first["status"] == "ASK_USER"

    second = client.post(
        "/api/v1/decide", json=_payload(elements=elements, user_speech="응 보내줘")
    ).json()

    assert second["status"] == "CONTINUE"
    assert second["target_node_id"] == 1
    assert second["action_type"] == "CLICK"


def test_send_element_stays_blocked_without_affirmative() -> None:
    """확인 질문에 동의가 아닌 답이 오면 계속 막는다."""
    _stub_client(_response(instruction="전송 버튼 클릭"))
    elements = [{**BASE_ELEMENT, "text": "전송"}]

    client.post("/api/v1/decide", json=_payload(elements=elements))
    body = client.post(
        "/api/v1/decide", json=_payload(elements=elements, user_speech="잠깐만요")
    ).json()

    assert body["status"] == "ASK_USER"
    assert body["target_node_id"] is None


def test_ordinary_element_is_not_gated() -> None:
    """되돌릴 수 없는 행동이 아니면 게이트가 개입하지 않는다."""
    _stub_client(_response())

    body = client.post("/api/v1/decide", json=_payload()).json()

    assert body["status"] == "CONTINUE"
    assert body["target_node_id"] == 1


# --- 마스킹 / 로깅 ---------------------------------------------------------


def test_personal_data_is_masked_before_ai_call() -> None:
    captured: list = []

    class SpyAIClient:
        def decide(self, goal, app_package, elements, history, user_speech=None, installed_apps=None):
            captured.extend(elements)
            return _response()

    app.dependency_overrides[get_ai_client] = lambda: SpyAIClient()

    elements = [{**BASE_ELEMENT, "text": "010-1234-5678", "content_description": "901231-1234567"}]
    client.post("/api/v1/decide", json=_payload(elements=elements))

    assert "1234-5678" not in (captured[0].text or "")
    assert "901231-1234567" not in (captured[0].content_description or "")


def test_logs_do_not_contain_sensitive_text(caplog) -> None:
    with caplog.at_level(logging.INFO):
        client.post("/api/v1/decide", json=_payload())

    for record in caplog.records:
        assert "조회" not in record.getMessage()
        assert not hasattr(record, "text")
        assert not hasattr(record, "content_description")


# --- 사용자 응답 (user_speech) ---------------------------------------------


def test_negative_user_speech_stops_the_flow() -> None:
    body = client.post("/api/v1/decide", json=_payload(user_speech="아니 취소해줘")).json()

    assert body["status"] == "DONE"
    assert body["target_node_id"] is None


def test_affirmative_user_speech_continues() -> None:
    body = client.post("/api/v1/decide", json=_payload(user_speech="응 그래")).json()

    assert body["status"] == "CONTINUE"
    assert body["target_node_id"] == 1


def test_unrecognized_user_speech_asks_again() -> None:
    body = client.post("/api/v1/decide", json=_payload(user_speech="음 글쎄요")).json()

    assert body["status"] == "ASK_USER"


# --- 세션 ------------------------------------------------------------------


def test_session_history_is_passed_to_ai_on_next_turn() -> None:
    captured: list = []

    class SpyAIClient:
        def decide(self, goal, app_package, elements, history, user_speech=None, installed_apps=None):
            captured.append(history)
            return _response()

    app.dependency_overrides[get_ai_client] = lambda: SpyAIClient()

    client.post("/api/v1/decide", json=_payload(session_id="session-history"))
    client.post("/api/v1/decide", json=_payload(session_id="session-history"))

    assert captured[0] == []
    assert len(captured[1]) == 1
    assert captured[1][0].step == 1


def test_sessions_are_isolated_by_session_id() -> None:
    captured: list = []

    class SpyAIClient:
        def decide(self, goal, app_package, elements, history, user_speech=None, installed_apps=None):
            captured.append(history)
            return _response()

    app.dependency_overrides[get_ai_client] = lambda: SpyAIClient()

    client.post("/api/v1/decide", json=_payload(session_id="session-a"))
    client.post("/api/v1/decide", json=_payload(session_id="session-b"))

    assert captured[1] == []


def test_health_endpoint() -> None:
    assert client.get("/health").json() == {"status": "ok"}


# --- TTS 문구 ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ("사진 보내기", "를"),  # 받침 없음
        ("보내기", "를"),
        ("조회", "를"),
        ("서울", "을"),  # 받침 있음
        ("부산행", "을"),
        ("확인", "을"),
        ("PDF", ""),  # 한글이 아니면 조사 생략
    ],
)
def test_object_particle_matches_final_consonant(word: str, expected: str) -> None:
    from backend.services.ai_client import _object_particle

    assert _object_particle(word) == expected


# --- 앱 실행 (첫 스텝, app_package=None) ------------------------------------

APPS = [
    {"package": "com.kakao.talk", "label": "카카오톡"},
    {"package": "com.nhn.android.search", "label": "네이버"},
]


def _launch_payload(goal: str, installed_apps=APPS, session_id="launch-session") -> dict:
    return {
        "session_id": session_id,
        "goal": goal,
        "app_package": None,
        "elements": [],
        "installed_apps": installed_apps,
        "user_speech": None,
        "history": None,
    }


def test_rule_resolves_app_without_calling_llm() -> None:
    """goal에 앱이 드러나면 LLM을 부르지 않고 바로 실행한다."""
    calls = []

    class CountingAIClient:
        def decide(self, goal, app_package, elements, history, user_speech=None,
                   installed_apps=None):
            calls.append(1)
            return _response()

    app.dependency_overrides[get_ai_client] = lambda: CountingAIClient()

    body = client.post(
        "/api/v1/decide", json=_launch_payload("카톡으로 엄마한테 사진 보내줘")
    ).json()

    assert body["action_type"] == "LAUNCH_APP"
    assert body["input_value"] == "com.kakao.talk"
    assert body["status"] == "CONTINUE"
    assert calls == []  # LLM 콜 0회


def test_ambiguous_goal_falls_through_to_llm() -> None:
    """앱을 짚어낼 수 없으면 규칙이 개입하지 않고 LLM에게 넘긴다."""
    _stub_client(
        _response(
            target_node_id=None,
            action_type="LAUNCH_APP",
            input_value="com.kakao.talk",
            voice_message="카카오톡을 열게요.",
        )
    )

    body = client.post(
        "/api/v1/decide", json=_launch_payload("엄마한테 사진 보내줘")
    ).json()

    assert body["action_type"] == "LAUNCH_APP"
    assert body["input_value"] == "com.kakao.talk"


def test_empty_elements_allowed_only_without_app_package() -> None:
    ok = client.post("/api/v1/decide", json=_launch_payload("카톡 열어줘"))
    assert ok.status_code == 200

    bad = client.post("/api/v1/decide", json=_payload(elements=[]))
    assert bad.status_code == 422


def test_launch_app_without_package_is_rejected() -> None:
    """LLM이 패키지명을 빠뜨리면 실행할 수 없으므로 UNSUPPORTED로 막는다."""
    _stub_client(
        _response(target_node_id=None, action_type="LAUNCH_APP", input_value=None)
    )

    body = client.post(
        "/api/v1/decide", json=_launch_payload("엄마한테 사진 보내줘")
    ).json()

    assert body["status"] == "UNSUPPORTED"


# --- 동의 인식: 두 경로 모두 + 오인식 방지 --------------------------------------
#
# 클라이언트는 확인 질문의 답을 두 갈래로 보낸다 (CLAUDE.md §5-1):
#   "응"처럼 짧으면 user_speech, 그 외("응 보내줘")는 goal 뒤에 이어붙여서.
# 게이트는 어느 쪽이든 받아야 하고, 동시에 원래 goal의 단어를 동의로 오인하면 안 된다.

SEND_ELEMENTS = [{**BASE_ELEMENT, "text": "전송"}]


def _send_payload(goal: str, user_speech: str | None = None) -> dict:
    return {
        "session_id": "consent-session",
        "goal": goal,
        "app_package": "com.kakao.talk",
        "elements": SEND_ELEMENTS,
        "user_speech": user_speech,
        "history": None,
    }


def test_consent_via_user_speech() -> None:
    _stub_client(_response())
    base = "엄마한테 사진 전달해줘"

    first = client.post("/api/v1/decide", json=_send_payload(base)).json()
    assert first["status"] == "ASK_USER"

    second = client.post("/api/v1/decide", json=_send_payload(base, user_speech="응")).json()
    assert second["status"] == "CONTINUE"
    assert second["target_node_id"] == 1


def test_consent_via_goal_accumulation() -> None:
    """긴 답변("응 보내줘")은 goal에 누적돼 온다 — 이 경로도 통과해야 한다."""
    _stub_client(_response())
    base = "엄마한테 사진 전달해줘"

    first = client.post("/api/v1/decide", json=_send_payload(base)).json()
    assert first["status"] == "ASK_USER"

    second = client.post("/api/v1/decide", json=_send_payload(f"{base}. 응 보내줘")).json()
    assert second["status"] == "CONTINUE"
    assert second["target_node_id"] == 1


def test_original_goal_wording_is_not_mistaken_for_consent() -> None:
    """가장 위험한 오작동: 원래 목표의 "보내줘"를 동의로 읽고 묻지도 않고 전송하는 것.

    확인을 요청한 시점 이후에 늘어난 말만 동의 후보로 본다.
    """
    _stub_client(_response())

    body = client.post(
        "/api/v1/decide", json=_send_payload("엄마한테 사진 보내줘")
    ).json()

    assert body["status"] == "ASK_USER"
    assert body["target_node_id"] is None


def test_declining_keeps_the_button_blocked() -> None:
    _stub_client(_response())
    base = "엄마한테 사진 전달해줘"

    client.post("/api/v1/decide", json=_send_payload(base))
    body = client.post(
        "/api/v1/decide", json=_send_payload(f"{base}. 아니요 하지 마세요")
    ).json()

    assert body["status"] == "ASK_USER"
    assert body["target_node_id"] is None

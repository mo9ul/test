"""규칙 계층 테스트 — LLM 없이 결정론적으로 처리되는 부분."""

import pytest

from backend.config import Settings
from backend.schemas.request import ElementDTO
from backend.services import rules

SETTINGS = Settings()


def _el(id: int, label: str | None = None, cls: str = "android.widget.TextView",
        clickable: bool = True, editable: bool = False, scrollable: bool = False,
        bounds: list[int] | None = None) -> ElementDTO:
    return ElementDTO(
        id=id, text=label, content_description=None, class_name=cls,
        clickable=clickable, editable=editable, scrollable=scrollable,
        bounds=bounds or [0, id * 10, 100, id * 10 + 9],
    )


# --- 필터링 -------------------------------------------------------------------


def test_drops_zero_area_and_pure_containers() -> None:
    elements = [
        _el(1, "검색"),
        _el(2, None, cls="android.widget.FrameLayout", clickable=False),  # 라벨X 조작X
        _el(3, "친구", bounds=[0, 0, 0, 0]),                              # 면적 0
    ]
    assert [e.id for e in rules.filter_elements(elements, SETTINGS)] == [1]


def test_keeps_unlabeled_clickable_nodes() -> None:
    """사진 그리드 셀처럼 라벨은 없지만 눌러야 하는 항목은 살아남아야 한다."""
    elements = [_el(i, None, cls="android.widget.ImageView") for i in (1, 2, 3)]
    assert len(rules.filter_elements(elements, SETTINGS)) == 3


def test_dedupes_wrapper_with_same_label() -> None:
    elements = [
        _el(1, "전송", cls="android.widget.Button"),
        _el(2, "전송", cls="android.widget.Button"),
    ]
    assert len(rules.filter_elements(elements, SETTINGS)) == 1


def test_sorts_in_reading_order() -> None:
    elements = [
        _el(1, "아래", bounds=[0, 500, 100, 560]),
        _el(2, "위", bounds=[0, 100, 100, 160]),
    ]
    assert [e.id for e in rules.filter_elements(elements, SETTINGS)] == [2, 1]


# --- 위치 힌트 ----------------------------------------------------------------


def test_position_hints_only_for_unlabeled_clickables() -> None:
    elements = [
        _el(1, "최근 항목", clickable=False),
        _el(2, None, cls="android.widget.ImageView"),
        _el(3, None, cls="android.widget.ImageView"),
    ]
    hints = rules.position_hints(elements)
    assert hints == {2: "이름 없는 1번째 항목", 3: "이름 없는 2번째 항목"}


def test_hints_reach_the_prompt_payload() -> None:
    """힌트가 실제로 LLM 입력에 실리지 않으면 의미가 없다."""
    from backend.services import prompt

    elements = [_el(9, None, cls="android.widget.ImageView")]
    body = prompt.build_input("사진 보내줘", "com.x", elements, None, None)
    assert "이름 없는 1번째 항목" in body


# --- 편집 가능 판별 ------------------------------------------------------------


def test_editable_detected_from_class_when_field_absent() -> None:
    """구버전 클라이언트가 editable을 안 보내도 클래스명으로 판별된다."""
    assert rules.is_editable(_el(1, None, cls="android.widget.EditText")) is True
    assert rules.is_editable(_el(2, "버튼", cls="android.widget.Button")) is False


# --- 화면 지문 ----------------------------------------------------------------


def test_signature_is_stable_across_reassigned_ids() -> None:
    """id는 화면 덤프마다 재부여되므로 지문에 영향을 주면 안 된다."""
    a = [_el(1, "검색"), _el(2, "친구")]
    b = [_el(77, "검색"), _el(88, "친구")]
    assert rules.screen_signature("com.x", a) == rules.screen_signature("com.x", b)


def test_signature_changes_with_content() -> None:
    assert rules.screen_signature("com.x", [_el(1, "검색")]) != rules.screen_signature(
        "com.x", [_el(1, "전송")]
    )


# --- 로딩 화면 판정 ------------------------------------------------------------


def test_loading_only_when_nothing_survives_filtering() -> None:
    """글자는 보이는데 누를 게 없는 화면은 '로딩'이 아니라 '막힌 상태'다.

    이걸 로딩으로 오판하면 LLM이 ASK_USER로 되물을 기회를 서버가 가로채게 된다.
    """
    stuck = rules.filter_elements([_el(1, "조회", clickable=False)], SETTINGS)
    assert rules.is_loading_screen(stuck) is False
    assert rules.is_loading_screen([]) is True


# --- Vision 필요 판정 ----------------------------------------------------------


@pytest.mark.parametrize(
    "elements,expected",
    [
        ([_el(1, "검색"), _el(2, "친구")], 0.0),
        ([_el(i, None, cls="android.widget.ImageView") for i in range(1, 5)], 1.0),
    ],
)
def test_unlabeled_ratio(elements, expected) -> None:
    assert rules.unlabeled_ratio(elements) == expected

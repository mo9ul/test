"""규칙 기반 최적화 — LLM에 물어보지 않아도 답이 정해진 것을 결정론적으로 끝낸다.

화면 전환마다 1콜이 발생하는 구조라 **콜 수와 콜당 토큰이 곧 원가이자 지연**이다.
페이로드 압축은 services/prompt.py가 이미 하고 있으므로 여기서는 중복하지 않고,
그쪽에 없는 것(노드 필터링·화면 지문·로딩 감지·위치 힌트)만 담당한다.

이 모듈은 특정 앱을 알지 못한다 (CLAUDE.md §12).
"""

import hashlib

from backend.config import Settings
from backend.schemas.request import ElementDTO

# 텍스트 입력이 가능한 것으로 볼 클래스명 조각.
# 클라이언트가 editable 필드를 보내지 않는 구버전이어도 판별이 되게 하는 폴백이다.
_EDITABLE_CLASS_HINTS = ("EditText", "AutoCompleteTextView", "SearchView")


def is_editable(element: ElementDTO) -> bool:
    if element.editable:
        return True
    return any(hint in element.class_name for hint in _EDITABLE_CLASS_HINTS)


def _area(bounds: list[int]) -> int:
    return max(0, bounds[2] - bounds[0]) * max(0, bounds[3] - bounds[1])


def _is_actionable(element: ElementDTO) -> bool:
    return element.clickable or element.scrollable or is_editable(element)


def filter_elements(elements: list[ElementDTO], settings: Settings) -> list[ElementDTO]:
    """LLM에 보낼 가치가 있는 노드만 남긴다.

    - 면적 0 (접힌 뷰 / 화면 밖) 제거
    - 라벨도 없고 조작도 불가능한 순수 레이아웃 컨테이너 제거
    - 같은 라벨·같은 클래스로 중복된 노드 제거
      (래퍼가 자식과 같은 라벨을 갖는 패턴이 UI Tree에 흔하다)
    - **라벨 없는 clickable 노드는 남긴다** — 사진 그리드 셀처럼 이름은 없지만
      위치로 지목해야 하는 항목이다 (position_hints 참고)

    실측(dumps/s1.xml, 75노드): 28개로 63% 감소.
    """
    kept: list[ElementDTO] = []
    seen: set[tuple[str, str]] = set()

    for element in elements:
        if _area(element.bounds) <= 0:
            continue
        label = element.label
        if not label and not _is_actionable(element):
            continue
        if label:
            key = (label, element.class_name)
            if key in seen:
                continue
            seen.add(key)
        kept.append(element)

    # 읽기 순서(위→아래, 왼→오른쪽) 정렬. 위치 추론과 화면 지문 안정성 모두에 필요하다.
    kept.sort(key=lambda e: (e.bounds[1], e.bounds[0]))
    return kept[: settings.MAX_ELEMENTS_TO_LLM]


def position_hints(elements: list[ElementDTO]) -> dict[int, str]:
    """라벨 없는 clickable 노드에 읽기 순서 힌트를 붙인다.

    사진 그리드처럼 셀에 contentDescription이 없는 화면에서도 **노드 자체는 트리에 있다**
    — 익명일 뿐이다. "가장 최근 사진 = 1번째 항목"으로 지목할 수 있게 해서,
    접근성 라벨이 없다는 이유만으로 곧바로 Vision을 켜지 않아도 되게 한다.
    """
    anonymous = [e for e in elements if not e.label and e.clickable]
    return {e.id: f"이름 없는 {i + 1}번째 항목" for i, e in enumerate(anonymous)}


def screen_signature(app_package: str | None, elements: list[ElementDTO]) -> str:
    """같은 화면이면 같은 값이 나오는 안정적인 지문.

    id는 화면 덤프마다 재부여되므로 지문에 넣지 않는다 — 라벨과 클래스만 쓴다.
    """
    parts = sorted(f"{e.label}\x1f{e.class_name}" for e in elements)
    raw = f"{app_package or ''}\x1e" + "\x1e".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def is_loading_screen(filtered_elements: list[ElementDTO]) -> bool:
    """필터를 통과한 노드가 하나도 없는 화면 — 판단할 재료 자체가 없다.

    **filter_elements를 통과한 목록을 넣어야 한다.** 조작 가능한 요소가 없다는 것만으로는
    부족하다 — 글자는 보이는데 누를 게 없는 화면은 "로딩 중"이 아니라 "막힌 상태"이고,
    그건 LLM이 ASK_USER로 되물어야 할 상황이다. 여기서 가로채면 그 경로가 죽는다.
    """
    return not filtered_elements


def unlabeled_ratio(elements: list[ElementDTO]) -> float:
    actionable = [e for e in elements if e.clickable or is_editable(e)]
    if not actionable:
        return 0.0
    return sum(1 for e in actionable if not e.label) / len(actionable)

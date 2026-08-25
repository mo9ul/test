from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    CONFIDENCE_THRESHOLD: float = 0.6
    SESSION_TTL_MINUTES: int = 30

    # --- Gemini ---
    # 키가 없으면 MockAIClient로 자동 폴백한다(팀원이 키 없이도 서버를 띄울 수 있게).
    GEMINI_API_KEY: str | None = None
    # gemini-1.5-*/2.0-*는 서비스 종료되어 사용 불가.
    # 최신은 3.7-flash지만 2026-08-25 실측에서 응답이 오지 않아(45초 타임아웃/504)
    # 실제로 동작이 확인된 3.6-flash를 기본값으로 둔다. 실측: 3.6-flash 2.2초, 3.5-flash-lite 1.2초.
    GEMINI_MODEL: str = "gemini-3.6-flash"
    # 화면당 1콜 × 10~15콜로 30초 예산을 맞춰야 하므로 기본 low.
    # 정확도가 부족한 화면 유형이 나오면 medium으로 올린다(작업 B-3).
    GEMINI_THINKING_LEVEL: Literal["low", "medium", "high"] = "low"

    # HTTP deadline. Gemini가 10초 미만을 거부하므로(400 "Minimum allowed deadline is 10s")
    # 이보다 낮게 설정할 수 없다. 실측 응답은 2초대이므로 이 값은 예산이 아니라 안전망이다.
    GEMINI_TIMEOUT_SECONDS: float = 10.0
    # --- 되돌릴 수 없는 행동 게이트 ---
    # SENSITIVE_KEYWORDS(탐지·로깅용)와 목적이 다르다. 이쪽은 "누르면 되돌릴 수 없는 행동"만
    # 담는다 — 비밀번호/인증/계좌 같은 '민감 정보' 항목은 여기 넣지 않는다(그건 버튼이 아니다).
    IRREVERSIBLE_KEYWORDS: list[str] = [
        "전송",
        "보내기",
        "송금",
        "이체",
        "결제",
        "구매",
        "주문",
        "삭제",
        "탈퇴",
    ]
    # 확인 질문에 대한 사용자의 동의 표현. 이 중 하나가 user_speech에 있어야 게이트를 통과한다.
    AFFIRMATIVE_WORDS: list[str] = [
        "응",
        "어",
        "네",
        "예",
        "그래",
        "좋아",
        "해줘",
        "보내",
        "진행",
        "확인",
        "맞아",
        "오케이",
    ]

    # --- 규칙 기반 최적화 (services/rules.py) ---
    MAX_ELEMENTS_TO_LLM: int = 60
    ENABLE_RULE_APP_RESOLUTION: bool = True
    APP_MATCH_MIN_SCORE: int = 60
    # 같은 화면에서 연속 몇 스텝까지 허용할지. 입력→클릭처럼 한 화면에서 여러 스텝이
    # 정상인 경우가 있어 3은 너무 빡빡하다.
    MAX_REPEATED_SCREENS: int = 5

    SENSITIVE_KEYWORDS: list[str] = [
        "전송",
        "보내기",
        "송금",
        "이체",
        "결제",
        "계좌",
        "비밀번호",
        "인증",
        "삭제",
        "탈퇴",
        "주민번호",
        "카드번호",
    ]


@lru_cache
def get_settings() -> Settings:
    return Settings()

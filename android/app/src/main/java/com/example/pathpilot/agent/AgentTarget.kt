package com.example.pathpilot.agent

/**
 * 이번 세션이 "무엇을, 어느 앱에서" 하려는지에 대한 단일 소스.
 *
 * 발화를 받는 곳(MainActivity / 웨이크업 트리거), 앱을 실행하는 곳(WakeAndLaunchActivity),
 * 화면을 조작하는 곳(TestAccessibilityService)이 서로를 직접 참조할 수 없어서
 * — 시스템이 각각 따로 띄우기 때문에 — 프로세스 전역 상태로 이어 붙인다.
 *
 * **[packageName]은 코드가 정하지 않는다.** 서버가 `LAUNCH_APP`으로 지정한 값이 들어온다.
 * 그래서 이 파일에도, 이걸 쓰는 어느 파일에도 앱 이름이 등장하지 않는다 (CLAUDE.md §12).
 *
 * 화면 데이터는 담지 않는다 — 목표 문장과 패키지명뿐이다 (CLAUDE.md §4-1 비영속화).
 */
object AgentTarget {

    /** 사용자가 말한 목표. 세션이 없으면 null. */
    @Volatile
    var goal: String? = null

    /** 서버가 고른 대상 앱. 이 앱의 화면에서만 자동 조작이 일어난다. */
    @Volatile
    var packageName: String? = null

    /** 발화는 받았지만 아직 어느 앱인지 정해지지 않은 상태. */
    val isResolving: Boolean
        get() = goal != null && packageName == null

    fun clear() {
        goal = null
        packageName = null
    }
}

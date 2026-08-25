package com.example.pathpilot.wakeup

import android.app.Activity
import android.app.KeyguardManager
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.util.Log
import android.view.WindowManager
import com.example.pathpilot.agent.AgentTarget
import com.example.pathpilot.agent.InstalledApps
import com.example.pathpilot.model.ActionType
import com.example.pathpilot.model.DecideRequest
import com.example.pathpilot.model.DecideStatus
import com.example.pathpilot.network.RetrofitClient
import com.example.pathpilot.testkit.TestAccessibilityService
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.util.UUID

/**
 * 화면이 꺼져 있거나 잠겨 있는 상태에서 요청이 들어왔을 때 "화면을 깨우고 대상 앱으로 이동하는
 * 모션"을 담당하는, UI 없는 중계 Activity. 그 이후는
 * [TestAccessibilityService.onAccessibilityEvent]가 해당 앱 창을 감지해서 이어받는다.
 *
 * **어느 앱을 열지는 여기서 정하지 않는다.** `app_package=null`로 서버에 한 번 물어보고
 * `LAUNCH_APP`으로 받은 패키지를 실행한다 — 그래서 이 파일에 앱 이름이 없다 (CLAUDE.md §12).
 * 사용자가 "카톡으로"라고 말하지 않고 "손자한테 사진 보내줘"라고만 해도 서버가 판단한다.
 *
 * **잠금 화면 위에 띄우는 것까지만 된다.** 기기가 PIN/패턴/생체인증으로 잠겨 있으면 실제 잠금
 * 해제는 사용자가 직접 해야 한다 — Android는 앱이 보안 잠금을 코드로 우회하는 걸 허용하지 않는다
 * (CLAUDE.md §4-2 "보안 통제 우회 금지"와도 방향이 같다).
 */
class WakeAndLaunchActivity : Activity() {

    private var wakeLock: PowerManager.WakeLock? = null
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        acquireShortWakeLock()
        applyShowOverLockScreenFlags()

        val goal = intent.getStringExtra(EXTRA_GOAL) ?: AgentTarget.goal
        if (goal.isNullOrBlank()) {
            Log.w(TAG, "목표가 없어 시작할 수 없음")
            finish()
            return
        }

        AgentTarget.goal = goal
        // 세션 id는 앱 선택 요청부터 시작해서 이후 화면 조작까지 같은 값을 쓴다.
        TestAccessibilityService.pendingSessionId = UUID.randomUUID().toString()
        resolveTargetAppThenLaunch(goal)
    }

    override fun onDestroy() {
        super.onDestroy()
        scope.cancel()
        wakeLock?.let { if (it.isHeld) it.release() }
        wakeLock = null
    }

    /**
     * 서버에 "이 목표라면 어느 앱을 열어야 하나"를 묻고, 받은 패키지를 실행한다.
     *
     * 화면이 없는 첫 스텝이므로 elements는 비우고 설치된 앱 목록만 보낸다.
     */
    private fun resolveTargetAppThenLaunch(goal: String) {
        scope.launch {
            try {
                val request = DecideRequest(
                    session_id = TestAccessibilityService.pendingSessionId,
                    goal = goal,
                    app_package = null,
                    elements = emptyList(),
                    installed_apps = InstalledApps.list(this@WakeAndLaunchActivity),
                )
                val response = withContext(Dispatchers.IO) {
                    RetrofitClient.apiService.decide(request)
                }

                val isLaunch = response.status == DecideStatus.CONTINUE &&
                    response.action_type == ActionType.LAUNCH_APP
                val packageName = response.input_value
                if (isLaunch && !packageName.isNullOrBlank()) {
                    launchTargetApp(packageName)
                } else {
                    // 서버가 앱을 고르지 못한 경우(ASK_USER 등). 되묻기는 화면이 있는
                    // MainActivity에서 처리하는 것이 맞으므로 여기서는 조용히 멈춘다.
                    Log.w(TAG, "앱을 결정하지 못함: status=${response.status} reason=${response.reason}")
                    AgentTarget.clear()
                }
            } catch (e: Exception) {
                Log.e(TAG, "앱 선택 요청 실패", e)
                AgentTarget.clear()
            } finally {
                finish()
            }
        }
    }

    /** 화면을 켜는 동작과 앱 실행 사이에 CPU가 다시 잠들지 않도록 짧게만 잡아둔다. */
    private fun acquireShortWakeLock() {
        val powerManager = getSystemService(Context.POWER_SERVICE) as PowerManager
        wakeLock = powerManager.newWakeLock(
            PowerManager.PARTIAL_WAKE_LOCK,
            "$packageName:WakeAndLaunch",
        ).apply {
            setReferenceCounted(false)
            acquire(WAKE_LOCK_TIMEOUT_MS)
        }
    }

    private fun applyShowOverLockScreenFlags() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O_MR1) {
            setShowWhenLocked(true)
            setTurnScreenOn(true)
            val keyguardManager = getSystemService(Context.KEYGUARD_SERVICE) as KeyguardManager
            keyguardManager.requestDismissKeyguard(this, null)
        } else {
            @Suppress("DEPRECATION")
            window.addFlags(
                WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED or
                    WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON or
                    WindowManager.LayoutParams.FLAG_DISMISS_KEYGUARD or
                    WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON,
            )
        }
    }

    private fun launchTargetApp(targetPackage: String) {
        val launchIntent = packageManager.getLaunchIntentForPackage(targetPackage)
        if (launchIntent == null) {
            // <queries> 선언이 없거나 앱이 실제로 없을 때 여기로 온다.
            Log.w(TAG, "실행할 수 없는 패키지: $targetPackage")
            AgentTarget.clear()
            return
        }
        // 이 값이 세팅된 뒤부터 AccessibilityService가 해당 앱 화면에서 동작한다.
        AgentTarget.packageName = targetPackage
        launchIntent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
        startActivity(launchIntent)
    }

    companion object {
        private const val TAG = "WakeAndLaunch"
        private const val WAKE_LOCK_TIMEOUT_MS = 10_000L

        /** 새로 시작할 세션의 goal. 없으면 [AgentTarget.goal]을 쓴다. */
        const val EXTRA_GOAL = "extra_goal"
    }
}

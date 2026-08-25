package com.example.pathpilot

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.provider.Settings
import android.util.Log
import android.widget.Button
import android.widget.TextView
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.lifecycle.lifecycleScope
import com.example.pathpilot.agent.AgentSession
import com.example.pathpilot.agent.InstalledApps
import com.example.pathpilot.model.ActionType
import com.example.pathpilot.model.DecideRequest
import com.example.pathpilot.model.DecideResponse
import com.example.pathpilot.model.DecideStatus
import com.example.pathpilot.network.RetrofitClient
import com.example.pathpilot.testkit.TestAccessibilityService
import com.example.pathpilot.voice.VoiceInteractionManager
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * 음성 진입 화면. 사용자가 하는 일은 버튼 한 번 누르고 말하는 것뿐이다.
 *
 * 여기서 담당하는 구간은 **발화 -> 앱 실행**까지다.
 * 앱이 열린 뒤의 화면 조작 루프는 [TestAccessibilityService]가 이어받는다.
 * 둘 사이는 [AgentSession]으로 목표를 전달한다.
 *
 * 어떤 앱을 열지는 여기서 정하지 않는다 — `app_package=null`로 첫 요청을 보내면
 * 서버가 `LAUNCH_APP`으로 패키지를 지정해 준다. 앱 이름이 이 파일에 없는 이유다.
 */
class MainActivity : AppCompatActivity() {

    private lateinit var voice: VoiceInteractionManager
    private lateinit var statusText: TextView
    private lateinit var heardText: TextView
    private lateinit var micButton: Button

    private val requestMicPermission =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            if (granted) startListening() else statusText.setText(R.string.main_need_mic)
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContentView(R.layout.activity_main)
        ViewCompat.setOnApplyWindowInsetsListener(findViewById(R.id.main)) { v, insets ->
            val systemBars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            v.setPadding(systemBars.left, systemBars.top, systemBars.right, systemBars.bottom)
            insets
        }

        statusText = findViewById(R.id.statusText)
        heardText = findViewById(R.id.heardText)
        micButton = findViewById(R.id.micButton)

        voice = VoiceInteractionManager(this)
        micButton.setOnClickListener { onMicTapped() }
    }

    override fun onDestroy() {
        super.onDestroy()
        voice.shutdown()
    }

    // --- 발화 수집 ---------------------------------------------------------

    private fun onMicTapped() {
        if (!isAccessibilityServiceEnabled()) {
            // 접근성 서비스가 꺼져 있으면 화면을 읽을 수 없다. 설정으로 안내한다.
            statusText.setText(R.string.main_need_accessibility)
            startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
            return
        }

        val granted = ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) ==
            PackageManager.PERMISSION_GRANTED
        if (granted) startListening() else requestMicPermission.launch(Manifest.permission.RECORD_AUDIO)
    }

    private fun startListening() {
        statusText.setText(R.string.main_listening)
        heardText.text = ""
        voice.listenOnce(
            onResult = { spoken -> onGoalSpoken(spoken) },
            onError = { error ->
                statusText.text = getString(R.string.main_idle)
                heardText.text = "잘 못 들었어요 ($error). 다시 말씀해 주세요."
            },
        )
    }

    private fun onGoalSpoken(spokenGoal: String) {
        heardText.text = "\"$spokenGoal\""
        statusText.setText(R.string.main_thinking)
        AgentSession.start(spokenGoal)
        requestFirstStep(userSpeech = null)
    }

    // --- 첫 스텝: 어떤 앱을 열지 서버에 묻는다 -------------------------------

    private fun requestFirstStep(userSpeech: String?) {
        val goal = AgentSession.goal ?: return
        lifecycleScope.launch {
            try {
                val request = DecideRequest(
                    session_id = AgentSession.sessionId,
                    goal = goal,
                    app_package = null,          // 아직 아무 앱도 열지 않았다
                    elements = emptyList(),      // 읽을 화면이 없다
                    installed_apps = InstalledApps.list(this@MainActivity),
                    user_speech = userSpeech,
                )
                val response = withContext(Dispatchers.IO) {
                    RetrofitClient.apiService.decide(request)
                }
                handleFirstStep(response, goal)
            } catch (e: Exception) {
                Log.e(TAG, "첫 decide 호출 실패", e)
                statusText.text = getString(R.string.main_idle)
                heardText.text = "서버에 연결하지 못했어요. (${e.message})"
            }
        }
    }

    private fun handleFirstStep(response: DecideResponse, goal: String) {
        if (response.voice_message.isNotBlank()) voice.speak(response.voice_message)

        when (response.status) {
            DecideStatus.CONTINUE -> {
                if (response.action_type == ActionType.LAUNCH_APP && response.input_value != null) {
                    launchApp(response.input_value)
                } else {
                    // 앱을 열기 전 단계에서 다른 행동이 올 이유가 없다. 상태만 보여주고 멈춘다.
                    statusText.text = response.voice_message.ifBlank { getString(R.string.main_idle) }
                }
            }

            DecideStatus.ASK_USER -> {
                // 어떤 앱인지 등 정보가 부족한 경우. 답을 받아 같은 세션으로 다시 묻는다.
                statusText.text = response.voice_message
                voice.listenOnce(
                    onResult = { answer -> requestFirstStep(userSpeech = answer) },
                    onError = { statusText.text = getString(R.string.main_idle) },
                )
            }

            else -> {
                // DONE / UNSUPPORTED
                statusText.text = response.voice_message.ifBlank { getString(R.string.main_idle) }
                AgentSession.finish()
            }
        }
    }

    private fun launchApp(packageName: String) {
        val intent = packageManager.getLaunchIntentForPackage(packageName)
        if (intent == null) {
            // <queries> 선언이 없거나 앱이 실제로 없을 때 여기로 온다.
            Log.w(TAG, "실행할 수 없는 패키지: $packageName")
            statusText.text = getString(R.string.main_idle)
            heardText.text = "그 앱을 열 수 없어요."
            AgentSession.finish()
            return
        }
        // 이 시점부터 화면 조작은 AccessibilityService가 이어받는다.
        AgentSession.targetPackage = packageName
        startActivity(intent)
    }

    // --- 접근성 서비스 활성화 확인 ------------------------------------------

    private fun isAccessibilityServiceEnabled(): Boolean {
        val enabled = Settings.Secure.getString(
            contentResolver,
            Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES,
        ) ?: return false
        val target = "$packageName/${TestAccessibilityService::class.java.name}"
        return enabled.split(':').any { it.equals(target, ignoreCase = true) }
    }

    private companion object {
        const val TAG = "MainActivity"
    }
}

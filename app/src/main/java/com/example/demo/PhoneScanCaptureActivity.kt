package com.example.demo

import android.app.Activity
import android.graphics.Color
import android.os.Bundle
import android.view.Gravity
import android.view.ViewGroup
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.Toast
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import com.journeyapps.barcodescanner.CaptureActivity

class PhoneScanCaptureActivity : CaptureActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val controls = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setBackgroundColor(Color.argb(230, 30, 28, 38))
            addView(control("返回小银", PhoneOverlayDestination.Assistant), controlLayout())
            addView(control("任务历史", PhoneOverlayDestination.History), controlLayout())
        }
        ViewCompat.setOnApplyWindowInsetsListener(controls) { view, insets ->
            val protectedArea = insets.getInsets(
                WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout(),
            )
            view.setPadding(
                protectedArea.left + dp(8), protectedArea.top + dp(4), protectedArea.right + dp(8), dp(4),
            )
            insets
        }
        addContentView(
            controls,
            FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.TOP),
        )
        ViewCompat.requestApplyInsets(controls)
    }

    private fun control(label: String, destination: PhoneOverlayDestination) = Button(this).apply {
        text = label
        textSize = 18f
        minHeight = dp(48)
        setAllCaps(false)
        setOnClickListener {
            if (!isFinishing) {
                if (PhoneTaskOverlay.open(this@PhoneScanCaptureActivity, destination)) {
                    setResult(Activity.RESULT_CANCELED)
                    finish()
                } else {
                    Toast.makeText(context, "暂时打不开小银助手，请稍后再试。", Toast.LENGTH_SHORT).show()
                }
            }
        }
    }

    private fun controlLayout() = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
        marginStart = dp(4)
        marginEnd = dp(4)
    }

    private fun dp(value: Int) = (value * resources.displayMetrics.density).toInt()
}

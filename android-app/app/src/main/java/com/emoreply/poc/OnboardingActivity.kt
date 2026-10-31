package com.emoreply.poc

import android.content.res.ColorStateList
import android.graphics.Color
import android.graphics.drawable.ColorDrawable
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AppCompatActivity
import androidx.core.graphics.ColorUtils
import androidx.core.content.ContextCompat
import androidx.recyclerview.widget.RecyclerView
import androidx.viewpager2.widget.ViewPager2
import com.emoreply.poc.store.UxSettings
import com.google.android.material.color.MaterialColors

/** T03722：首启走马灯功能介绍页——四页轮播，看完或跳过即落盘，之后任何启动都不再出现。 */
class OnboardingActivity : AppCompatActivity() {

    private data class IntroPage(val iconRes: Int, val titleRes: Int, val bodyRes: Int)

    private val pages = listOf(
        IntroPage(R.drawable.ic_gen_eye, R.string.intro_p1_title, R.string.intro_p1_body),
        IntroPage(R.drawable.ic_gen_candidates, R.string.intro_p2_title, R.string.intro_p2_body),
        IntroPage(R.drawable.ic_gen_tone, R.string.intro_p3_title, R.string.intro_p3_body),
        IntroPage(R.drawable.ic_gen_brush, R.string.intro_p4_title, R.string.intro_p4_body),
    )

    private lateinit var pager: ViewPager2
    private lateinit var dots: LinearLayout
    private lateinit var btnNext: Button

    private val accent: Int by lazy {
        MaterialColors.getColor(this, com.google.android.material.R.attr.colorPrimary, Color.GRAY)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_onboarding)
        applyAmoledBlack()

        pager = findViewById(R.id.introPager)
        dots = findViewById(R.id.introDots)
        btnNext = findViewById(R.id.btnIntroNext)

        pager.adapter = IntroAdapter(pages)
        buildDots()
        renderPage(0)
        pager.registerOnPageChangeCallback(object : ViewPager2.OnPageChangeCallback() {
            override fun onPageSelected(position: Int) {
                renderPage(position)
            }
        })
        // 翻页动效：离屏页淡出并轻微缩小，滑动过程连续过渡
        pager.setPageTransformer { page, position ->
            val offset = Math.abs(position)
            page.alpha = 1f - offset * 0.75f
            val scale = 1f - offset * 0.08f
            page.scaleX = scale
            page.scaleY = scale
        }

        findViewById<Button>(R.id.btnIntroSkip).setOnClickListener { finishIntro() }
        btnNext.setOnClickListener {
            if (pager.currentItem >= pages.lastIndex) finishIntro() else pager.currentItem += 1
        }
        // 返回键等价「跳过」：不留「下次再弹」的尾巴
        onBackPressedDispatcher.addCallback(
            this,
            object : OnBackPressedCallback(true) {
                override fun handleOnBackPressed() = finishIntro()
            },
        )
    }

    private fun finishIntro() {
        UxSettings.setIntroSeen(this, true)
        finish()
    }

    private fun buildDots() {
        dots.removeAllViews()
        pages.forEach { _ ->
            val dot = View(this)
            dot.background = ContextCompat.getDrawable(this, R.drawable.bg_intro_dot)
            dots.addView(dot, dotIndexParams(dp(12)))
        }
    }

    private fun dotIndexParams(widthPx: Int): LinearLayout.LayoutParams {
        val params = LinearLayout.LayoutParams(widthPx, dp(12))
        params.marginStart = dp(4)
        params.marginEnd = dp(4)
        return params
    }

    private fun renderPage(position: Int) {
        for (i in 0 until dots.childCount) {
            val dot = dots.getChildAt(i)
            val active = i == position
            val params = dot.layoutParams as LinearLayout.LayoutParams
            params.width = dp(if (active) 26 else 12)
            dot.layoutParams = params
            dot.backgroundTintList = ColorStateList.valueOf(
                if (active) accent else ColorUtils.setAlphaComponent(accent, 72),
            )
        }
        btnNext.setText(if (position >= pages.lastIndex) R.string.intro_start else R.string.intro_next)
    }

    /** AMOLED 纯黑档位下窗口底色置黑（与 MainActivity 同口径，仅深色生效）。 */
    private fun applyAmoledBlack() {
        if (UxSettings.amoled(this) && UxSettings.isDarkActive(this)) {
            window.setBackgroundDrawable(ColorDrawable(Color.BLACK))
            findViewById<View>(android.R.id.content).setBackgroundColor(Color.BLACK)
        }
    }

    private fun dp(value: Int): Int = (value * resources.displayMetrics.density).toInt()

    private class IntroAdapter(private val pages: List<IntroPage>) : RecyclerView.Adapter<IntroAdapter.Holder>() {

        class Holder(val root: View) : RecyclerView.ViewHolder(root)

        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): Holder = Holder(
            LayoutInflater.from(parent.context).inflate(R.layout.item_onboarding_page, parent, false),
        )

        override fun getItemCount(): Int = pages.size

        override fun onBindViewHolder(holder: Holder, position: Int) {
            val page = pages[position]
            holder.root.findViewById<ImageView>(R.id.ivIntroIcon).setImageResource(page.iconRes)
            holder.root.findViewById<TextView>(R.id.tvIntroTitle).setText(page.titleRes)
            holder.root.findViewById<TextView>(R.id.tvIntroBody).setText(page.bodyRes)
        }
    }
}

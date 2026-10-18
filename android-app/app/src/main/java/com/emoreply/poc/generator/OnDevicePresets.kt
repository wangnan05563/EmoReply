package com.emoreply.poc.generator

/**
 * 端侧离线模型的**预制选项**（T01344）：把最常用的模型做成一个个可点击的按钮，
 * 点一下即自动填好模型路径，并给出配套的 PC 端放置命令。
 *
 * ## 为什么只预置「路径 + 官方下载页」，不预置直链
 *
 * 端侧模型动辄 1-3GB，直链写错的表现是**用户白等几十分钟才失败**，体验极差。
 * 而 MediaPipe / LiteRT 社区的模型直链（文件名、版本号、量化档位）会随上游更新变动，
 * 本环境无法逐一联网核验，因此**不把任何未经核验的直链写进代码**。改为：
 * - 路径：自动填写（见 [PUSH_TARGET_DIR]）；
 * - 文件：用户从官方下载页取得后，按 [pushCommands] 的三条命令放进应用私有目录。
 *
 * ⚠ **2026-10-07 实测更正（一）**：Kaggle 的模型文件**不存在匿名直链**——页面上「Download」
 * 按钮对应的 `…/1/download` 直连返回 404，API 形态 `…/api/v1/models/.../1/download` 返回
 * 403 `Permission denied`，两者都需要登录凭据。「跳官方页复制直链粘回来」对 Kaggle 源
 * **走不通**，应用内下载器也没有凭据位；自建镜像/带凭据参数的直链仍可走应用内下载。
 *
 * ⚠ **2026-10-08 实测更正（二）**：早期预置路径 `/data/local/tmp` 是**错的**——那是
 * MediaPipe 官方 **Linux 桌面**推理脚本（`run_llm_inference.sh`）的落地目录。Android 上
 * 应用进程受沙箱（SELinux 对 shell_data_file 的 denial）限制**读不了** `/data/local/tmp`，
 * 即使 adb push 成功、点了回填按钮，应用仍永远报「模型文件缺失」，预制按钮形同虚设。
 * 现改为 MediaPipe 官方 **Android** 示例的做法：模型放应用私有 cache 目录，应用对自家
 * `/data/data/<包名>/…` 天然可读，无需任何运行时权限。
 */
object OnDevicePresets {

    /** 应用包名：私有目录路径与回填命令均由它拼出。 */
    const val PACKAGE_NAME = "com.emoreply.poc"

    /**
     * 模型落地目录 = 应用私有 cache 目录（MediaPipe 官方 Android 示例采用的位置）。
     * 应用对其下文件天然有读权限；shell 侧借 `run-as`（debug 包可调试）放开写权限后
     * 才能 push 进来，故配合 [pushCommands] 使用。
     */
    const val PUSH_TARGET_DIR = "/data/data/$PACKAGE_NAME/cache"

    /**
     * 把一个模型文件放进 [PUSH_TARGET_DIR] 的三条 PC 端命令（官方示例流程）：
     * run-as 放开 cache → adb push 写入 → run-as 收回文件权限。
     * 返回多行文本，供 UI 展示后整段复制执行。
     */
    fun pushCommands(fileName: String): String = """
        |adb shell "run-as $PACKAGE_NAME chmod 777 $PUSH_TARGET_DIR"
        |adb push <电脑上的文件路径>/$fileName $PUSH_TARGET_DIR/$fileName
        |adb shell "run-as $PACKAGE_NAME chmod 755 $PUSH_TARGET_DIR/$fileName"
    """.trimMargin()

    /** 一个预制模型选项。 */
    data class ModelPreset(
        /** 按钮文案（短）。 */
        val label: String,
        /** 官方示例/文档使用的 .task 文件名。 */
        val fileName: String,
        /** 官方下载页（Kaggle LiteRT 版块），供获取文件版本与 CLI 指令。 */
        val sourcePage: String,
        /** 一句话说明（体积/量化/注意事项），填入后展示在状态行。 */
        val hint: String,
    )

    val PRESETS: List<ModelPreset> = listOf(
        ModelPreset(
            label = "Gemma 2 2B · int8",
            fileName = "gemma2-2b-it-cpu-int8.task",
            sourcePage = "https://www.kaggle.com/models/google/gemma-2/tfLite/gemma2-2b-it-cpu-int8",
            hint = "官方 MediaPipe 端侧示例默认模型；CPU 后端 int8，实测体积约 2.7GB，内存占用高",
        ),
        ModelPreset(
            label = "Gemma 1.1 2B · int4",
            fileName = "gemma-1.1-2b-it-cpu-int4.bin",
            sourcePage = "https://www.kaggle.com/models/google/gemma/tfLite/gemma-1.1-2b-it-cpu-int4",
            hint = "官方示例列出的更小备选（int4 量化），注意是 .bin 格式而非 .task",
        ),
        ModelPreset(
            label = "Gemma 3 270M · int8",
            fileName = "gemma-3-270m-it-int8.task",
            sourcePage = "https://www.kaggle.com/models/google/gemma-3n",
            hint = "参数量最小、最快的新模型；需登录 Kaggle 或用 CLI 下载，再按下方命令 adb push",
        ),
    )

    /** 预设对应的完整落地路径（[PUSH_TARGET_DIR] + 文件名）。 */
    fun pathOf(preset: ModelPreset): String = "$PUSH_TARGET_DIR/${preset.fileName}"

    /** 按已填路径反查命中的预设（用于展示对应官方页）；无命中返回 null。 */
    fun match(path: String): ModelPreset? {
        val name = path.trim().substringAfterLast('/')
        if (name.isBlank()) return null
        return PRESETS.firstOrNull { it.fileName == name }
    }
}

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.emoreply.poc"
    compileSdk = 35
    // 本机 build-tools 仅有 34.0.0（镜像无 r35），AGP 8.7 允许使用该版本
    buildToolsVersion = "34.0.0"

    defaultConfig {
        applicationId = "com.emoreply.poc"
        minSdk = 26
        targetSdk = 35
        versionCode = 247601
        versionName = "0.1.0-poc"
        // T01124：截图 OCR POC 仅 arm64 真机（三星 S 系列），控制 .so 体积
        ndk {
            abiFilters += listOf("arm64-v8a")
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    // tasks-genai 传导依赖（guava-android / protobuf / 基类库）会带入 META-INF 资源，
    // 不排除会在打包时因重复条目报错；LLM 引擎 .so 已由 abiFilters 收敛到 arm64-v8a。
    packagingOptions {
        resources.excludes += setOf(
            "META-INF/INDEX.LIST",
            "META-INF/DEPENDENCIES",
            "META-INF/LICENSE*",
            "META-INF/NOTICE*",
            "META-INF/*.SF",
            "META-INF/*.RSA",
            "META-INF/*.DSA",
        )
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }
}

// 构建产物统一落在工程外的 release 目录：删掉 release 即等于彻底清空构建残留，
// 无需再逐个清理工程内的 build / .gradle / .kotlin
layout.buildDirectory.set(rootProject.projectDir.parentFile.resolve("release/build/app"))

dependencies {
    implementation("androidx.core:core-ktx:${property("depCoreKtxVersion")}")
    implementation("androidx.appcompat:appcompat:${property("depAppcompatVersion")}")
    // T01088：Material3 组件体系（卡片 / TextInputLayout / Snackbar / MaterialSwitch / 对话框）
    implementation("com.google.android.material:material:${property("depMaterialVersion")}")
    // T01124：微信截图读屏 POC——端侧 OCR。
    // 注：tess-two（阿里云有包）在 Android 15 native 崩溃（旧 NDK 链接，FORTIFY mutex abort，真机实测）；
    // tesseract4android 未同步阿里云镜像。ML Kit bundled 版模型打进 APK、不依赖 GMS（国行可用）。
    implementation("com.google.mlkit:text-recognition-chinese:${property("depMlkitTextChineseVersion")}")
    // T01243 FR-8.1：端侧离线 LLM（MediaPipe LlmInference）。0.10.35 完整版 AAR 自带
    // arm64 .so（我们的 ndk abiFilters 目标），模型 .task 不打包、首次下载，故体积可控。
    implementation("com.google.mediapipe:tasks-genai:${property("depMediapipeGenaiVersion")}")
}
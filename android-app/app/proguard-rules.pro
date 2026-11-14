# EmoReply release 混淆规则（T05741 / SonarQube S7204）
#
# R8 默认规则已覆盖 AndroidX / Kotlin；此处只补依赖库 JNI 侧的兜底：
# MediaPipe Tasks GenAI 的 .so 通过固定类名回调 Java 推断接口，类名被重命名会在
# 真机上抛 UnsatisfiedLinkError / ClassNotFoundException。AAR 虽自带 consumer 规则，
# 仍显式保留一份，避免依赖升级后规则缺失导致发布包运行时崩溃。
-keep class com.google.mediapipe.** { *; }
-dontwarn com.google.mediapipe.**

# ML Kit bundled 中文识别模型通过反射加载内部实现类，保留其入口与内部包。
-keep class com.google.mlkit.** { *; }
-dontwarn com.google.mlkit.**

package com.emoreply.poc.util

import android.content.Context
import android.provider.Settings
import java.security.MessageDigest
import javax.crypto.Cipher
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

/**
 * 端侧 AES-256-GCM 加解密（T01053 决策：自实现，零新依赖）。
 *
 * 与 m1-poc `emoreply/persona.py` 同型方案：主密钥 = SHA256(设备指纹 || 可选口令)。
 * Android 指纹用 Settings.Secure.ANDROID_ID（恢复出厂才变更，满足端侧缓存加密的
 * 设备绑定语义）；密钥不落盘、不进仓库。
 */
object Crypto {

    private const val TRANSFORM = "AES/GCM/NoPadding"
    private const val IV_LEN = 12
    private const val TAG_BITS = 128

    fun masterKey(context: Context, passphrase: String? = null): ByteArray {
        val androidId = Settings.Secure.getString(context.contentResolver, Settings.Secure.ANDROID_ID)
            ?: "emoreply-local"
        val seed = if (passphrase.isNullOrBlank()) androidId else "$androidId|$passphrase"
        return MessageDigest.getInstance("SHA-256").digest(seed.toByteArray(Charsets.UTF_8))
    }

    /** 输出 `iv.ciphertext` 两段 hex；IV 每次随机。 */
    fun encrypt(key: ByteArray, plain: String): String {
        val iv = ByteArray(IV_LEN).also { java.security.SecureRandom().nextBytes(it) }
        val cipher = Cipher.getInstance(TRANSFORM)
        cipher.init(Cipher.ENCRYPT_MODE, SecretKeySpec(key, "AES"), GCMParameterSpec(TAG_BITS, iv))
        val ct = cipher.doFinal(plain.toByteArray(Charsets.UTF_8))
        return iv.joinToString("") { "%02x".format(it) } + "." +
            ct.joinToString("") { "%02x".format(it) }
    }

    /** 解密失败（密钥不匹配/数据损坏）抛 GeneralSecurityException，由调用方折算为明确错误。 */
    fun decrypt(key: ByteArray, payload: String): String {
        val (ivHex, ctHex) = payload.split(".", limit = 2)
        val cipher = Cipher.getInstance(TRANSFORM)
        cipher.init(
            Cipher.DECRYPT_MODE,
            SecretKeySpec(key, "AES"),
            GCMParameterSpec(TAG_BITS, ivHex.chunked(2).map { it.toInt(16).toByte() }.toByteArray()),
        )
        return String(cipher.doFinal(ctHex.chunked(2).map { it.toInt(16).toByte() }.toByteArray()), Charsets.UTF_8)
    }
}

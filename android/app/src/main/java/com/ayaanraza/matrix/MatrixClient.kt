package com.ayaanraza.matrix

import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/**
 * Minimal MATRIX client for the companion scaffold.
 *
 * Uses HttpURLConnection + org.json so the scaffold has no networking dependency beyond
 * Android's standard library. Every call carries the paired device token.
 */
class MatrixApiException(
    val status: Int,
    val code: String,
    message: String,
) : Exception(message)

class MatrixClient(var baseUrl: String) {
    var token: String? = null

    fun pair(code: String, deviceName: String): JSONObject {
        val body = JSONObject()
            .put("code", code)
            .put("device_name", deviceName)
            .put("platform", "android")
        return post("/api/auth/pair", body, requireAuth = false)
    }

    fun me(): JSONObject = get("/api/auth/me")

    fun sendChat(message: String): JSONObject = post("/api/chat", JSONObject().put("message", message))

    fun task(taskId: String): JSONObject = get("/api/tasks/$taskId")

    fun decide(taskId: String, approvalId: String, approve: Boolean): JSONObject {
        val action = if (approve) "approve" else "deny"
        return post("/api/tasks/$taskId/approvals/$approvalId/$action", JSONObject())
    }

    fun emergencyStop(active: Boolean): JSONObject =
        post("/api/security/emergency-stop", JSONObject().put("active", active))

    private fun get(path: String): JSONObject = request("GET", path, null, requireAuth = true)

    private fun post(path: String, body: JSONObject, requireAuth: Boolean = true): JSONObject =
        request("POST", path, body, requireAuth)

    private fun request(method: String, path: String, body: JSONObject?, requireAuth: Boolean): JSONObject {
        val endpoint = URL(baseUrl.trimEnd('/') + path)
        val connection = (endpoint.openConnection() as HttpURLConnection).apply {
            requestMethod = method
            connectTimeout = 15_000
            readTimeout = 30_000
            setRequestProperty("Content-Type", "application/json")
            setRequestProperty("Accept", "application/json")
            if (requireAuth) {
                val current = token ?: throw MatrixApiException(401, "unauthenticated", "Pair this device first")
                setRequestProperty("Authorization", "Bearer $current")
            }
        }

        try {
            if (body != null) {
                connection.doOutput = true
                connection.outputStream.use { stream ->
                    stream.write(body.toString().toByteArray(Charsets.UTF_8))
                }
            }
            val status = connection.responseCode
            val stream = if (status in 200..299) connection.inputStream else connection.errorStream
            val text = stream?.bufferedReader()?.use { it.readText() }.orEmpty()
            if (status !in 200..299) {
                val payload = runCatching { JSONObject(text) }.getOrNull()
                throw MatrixApiException(
                    status,
                    payload?.optString("code").orEmpty().ifEmpty { "http_error" },
                    payload?.optString("message").orEmpty().ifEmpty { "HTTP $status" },
                )
            }
            return JSONObject(text)
        } finally {
            connection.disconnect()
        }
    }
}

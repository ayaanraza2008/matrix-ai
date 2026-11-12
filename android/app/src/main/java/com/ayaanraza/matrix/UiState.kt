package com.ayaanraza.matrix

import org.json.JSONObject

/** A read-only snapshot of one MATRIX task, including any pending approval. */
data class TaskSnapshot(
    val id: String,
    val state: String,
    val summary: String,
    val pendingApprovalId: String?,
    val pendingApprovalTool: String?,
    val pendingApprovalRisk: String?,
) {
    companion object {
        fun from(json: JSONObject): TaskSnapshot {
            var approvalId: String? = null
            var approvalTool: String? = null
            var approvalRisk: String? = null
            val approvals = json.optJSONArray("approvals")
            if (approvals != null) {
                for (index in 0 until approvals.length()) {
                    val entry = approvals.optJSONObject(index) ?: continue
                    if (entry.optString("status") == "pending") {
                        approvalId = entry.optString("id")
                        approvalTool = entry.optString("tool_name")
                        approvalRisk = entry.optString("risk")
                        break
                    }
                }
            }
            return TaskSnapshot(
                id = json.optString("id"),
                state = json.optString("state", "UNKNOWN"),
                summary = json.optString("summary").ifEmpty { "(no summary yet)" },
                pendingApprovalId = approvalId,
                pendingApprovalTool = approvalTool,
                pendingApprovalRisk = approvalRisk,
            )
        }
    }
}

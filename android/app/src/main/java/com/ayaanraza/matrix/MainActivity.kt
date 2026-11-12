package com.ayaanraza.matrix

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.runtime.rememberCoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme(colorScheme = darkColorScheme()) {
                Surface(modifier = Modifier.fillMaxSize()) {
                    MatrixApp()
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MatrixApp() {
    val scope = rememberCoroutineScope()
    val context = LocalContext.current
    val tokenVault = remember(context) { TokenVault(context.applicationContext) }
    val client = remember { MatrixClient("") }

    var baseUrl by rememberSaveable { mutableStateOf("") }
    var code by rememberSaveable { mutableStateOf("") }
    var message by rememberSaveable { mutableStateOf("") }
    var token by remember { mutableStateOf(tokenVault.load()) }
    var lastTaskId by rememberSaveable { mutableStateOf<String?>(null) }
    var output by remember { mutableStateOf("Not connected. Start MATRIX on the PC and run: matrix pair-code") }
    var busy by remember { mutableStateOf(false) }

    fun perform(block: () -> String) {
        if (busy) return
        busy = true
        scope.launch {
            output = withContext(Dispatchers.IO) {
                try {
                    block()
                } catch (error: Exception) {
                    "Error: ${error.message}"
                }
            }
            busy = false
        }
    }

    fun pairDevice() = perform {
        client.baseUrl = baseUrl
        val response = client.pair(code.trim(), "Android companion")
        val newToken = response.optString("token")
        tokenVault.save(newToken)
        token = newToken
        client.token = newToken
        "Paired. device_id=${response.optString("device_id")} (expires ${response.optString("expires_at")})"
    }

    fun refreshTask(): String {
        val taskId = lastTaskId ?: return "No task yet. Send a message first."
        client.baseUrl = baseUrl
        client.token = token
        val snapshot = TaskSnapshot.from(client.task(taskId))
        val pending = snapshot.pendingApprovalId
        val approvalLine = if (pending != null) {
            "\nPending approval: ${snapshot.pendingApprovalTool} risk=${snapshot.pendingApprovalRisk} id=$pending"
        } else {
            ""
        }
        return "State: ${snapshot.state}\n${snapshot.summary}$approvalLine"
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(20.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("MATRIX", style = MaterialTheme.typography.headlineMedium)
        Text(
            "Local-first companion. Pair with a one-time code from 'matrix pair-code'.",
            style = MaterialTheme.typography.bodySmall,
        )

        OutlinedTextField(
            value = baseUrl,
            onValueChange = { baseUrl = it },
            label = { Text("PC base URL") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        OutlinedTextField(
            value = code,
            onValueChange = { code = it },
            label = { Text("Pairing code") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        Button(onClick = { pairDevice() }, enabled = !busy && code.isNotBlank()) {
            Text(if (token == null) "Pair device" else "Re-pair device")
        }
        Button(
            onClick = {
                perform {
                    tokenVault.clear()
                    token = null
                    client.token = null
                    "Device token cleared from this device."
                }
            },
            enabled = !busy && token != null,
        ) {
            Text("Unpair this device")
        }

        HorizontalDivider()

        OutlinedTextField(
            value = message,
            onValueChange = { message = it },
            label = { Text("Message") },
            modifier = Modifier.fillMaxWidth(),
        )
        Button(
            onClick = {
                perform {
                    client.baseUrl = baseUrl
                    client.token = token
                    val task = client.sendChat(message.trim())
                    lastTaskId = task.optString("id")
                    "Task ${task.optString("id")} queued. State: ${task.optString("state")}"
                }
            },
            enabled = !busy && token != null && message.isNotBlank(),
        ) {
            Text("Send to MATRIX")
        }

        Button(onClick = { perform { refreshTask() } }, enabled = !busy && token != null) {
            Text("Refresh last task")
        }

        Button(
            onClick = {
                perform {
                    val taskId = lastTaskId ?: return@perform "No task yet."
                    client.baseUrl = baseUrl
                    client.token = token
                    val snapshot = TaskSnapshot.from(client.task(taskId))
                    val approvalId = snapshot.pendingApprovalId
                        ?: return@perform "No pending approval for this task."
                    val result = client.decide(taskId, approvalId, approve = true)
                    "Approved. State: ${result.optString("state")}\n${result.optString("summary")}"
                }
            },
            enabled = !busy && token != null,
        ) {
            Text("Approve pending action")
        }

        Button(
            onClick = {
                perform {
                    val taskId = lastTaskId ?: return@perform "No task yet."
                    client.baseUrl = baseUrl
                    client.token = token
                    val snapshot = TaskSnapshot.from(client.task(taskId))
                    val approvalId = snapshot.pendingApprovalId
                        ?: return@perform "No pending approval for this task."
                    val result = client.decide(taskId, approvalId, approve = false)
                    "Denied. State: ${result.optString("state")}"
                }
            },
            enabled = !busy && token != null,
        ) {
            Text("Deny pending action")
        }

        HorizontalDivider()

        Button(
            onClick = {
                perform {
                    client.baseUrl = baseUrl
                    client.token = token
                    val me: JSONObject = client.me()
                    "Session ok. user=${me.optString("user_id")} device=${me.optString("device_id")}"
                }
            },
            enabled = !busy && token != null,
        ) {
            Text("Check session")
        }

        Text(output, style = MaterialTheme.typography.bodyMedium)
        Text(
            "Nothing here fakes success: unconfigured capabilities answer with an error, and high-risk actions need approval.",
            style = MaterialTheme.typography.bodySmall,
        )
    }
}

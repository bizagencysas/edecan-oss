@file:Suppress("DEPRECATION", "OVERRIDE_DEPRECATION")

package cc.edecan.app.notifications

import android.Manifest
import android.app.AlarmManager
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import cc.edecan.app.MainActivity
import cc.edecan.app.R
import cc.edecan.shared.DataStoreTokenStore
import cc.edecan.shared.EdecanApi
import cc.edecan.shared.Reminder
import com.google.firebase.FirebaseApp
import com.google.firebase.messaging.FirebaseMessaging
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import java.time.OffsetDateTime
import java.util.concurrent.atomic.AtomicInteger
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

const val EXTRA_NOTIFICATION_ROUTE = "cc.edecan.notification.route"
private const val EXTRA_TITLE = "cc.edecan.notification.title"
private const val EXTRA_BODY = "cc.edecan.notification.body"
private const val EXTRA_CHANNEL = "cc.edecan.notification.channel"

enum class NotificationRoute(val wireValue: String) {
    ASSISTANT("assistant"), ACTIVITY("activity"), SETTINGS("settings"), CREATE("create"), REMOTE("remote");

    companion object {
        fun parse(raw: String?): NotificationRoute = entries.firstOrNull {
            it.wireValue == raw?.lowercase()
        } ?: ASSISTANT
    }
}

object EdecanNotifications {
    const val GENERAL = "edecan_general"
    const val REMINDERS = "edecan_reminders"
    const val WORK = "edecan_work"
    const val CONTENT = "edecan_content"
    const val CALLS = "edecan_calls"
    private val nextNotificationId = AtomicInteger(7_000)

    fun initialize(context: Context) {
        val manager = context.getSystemService(NotificationManager::class.java)
        listOf(
            NotificationChannel(GENERAL, "Edecán", NotificationManager.IMPORTANCE_DEFAULT),
            NotificationChannel(REMINDERS, "Recordatorios", NotificationManager.IMPORTANCE_HIGH),
            NotificationChannel(WORK, "Trabajos", NotificationManager.IMPORTANCE_DEFAULT),
            NotificationChannel(CONTENT, "Contenido", NotificationManager.IMPORTANCE_DEFAULT),
            NotificationChannel(CALLS, "Llamadas", NotificationManager.IMPORTANCE_HIGH),
        ).forEach(manager::createNotificationChannel)
    }

    fun permissionGranted(context: Context): Boolean = Build.VERSION.SDK_INT < 33 ||
        ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED

    fun remoteConfigured(context: Context): Boolean = FirebaseApp.getApps(context).isNotEmpty()

    /** Obtiene el token vigente después de cada login/launch; FCM puede
     * rotarlo y [EdecanFirebaseMessagingService.onNewToken] cubre el cambio
     * mientras el proceso está vivo. Nunca se imprime ni se persiste. */
    fun refreshRemoteRegistration(context: Context) {
        if (!remoteConfigured(context)) return
        FirebaseMessaging.getInstance().token.addOnSuccessListener { token ->
            if (token.isNotBlank()) syncToken(context.applicationContext, token)
        }
    }

    fun syncToken(context: Context, token: String) {
        val appContext = context.applicationContext
        CoroutineScope(SupervisorJob() + Dispatchers.IO).launch {
            val store = DataStoreTokenStore(appContext)
            val server = store.getServerUrl() ?: return@launch
            val deviceId = store.getDeviceId() ?: return@launch
            runCatching { EdecanApi(server, store).registerPushToken(deviceId, token) }
        }
    }

    fun scheduleReminder(context: Context, reminder: Reminder) {
        val dueMillis = runCatching { OffsetDateTime.parse(reminder.dueAt).toInstant().toEpochMilli() }.getOrNull() ?: return
        val intent = Intent(context, LocalNotificationReceiver::class.java).apply {
            putExtra(EXTRA_TITLE, "Recordatorio de Edecán")
            putExtra(EXTRA_BODY, reminder.message)
            putExtra(EXTRA_CHANNEL, REMINDERS)
            putExtra(EXTRA_NOTIFICATION_ROUTE, NotificationRoute.ACTIVITY.wireValue)
        }
        val requestCode = reminder.id.hashCode()
        val pending = PendingIntent.getBroadcast(
            context,
            requestCode,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val alarm = context.getSystemService(AlarmManager::class.java)
        alarm.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, dueMillis.coerceAtLeast(System.currentTimeMillis() + 1_000), pending)
    }

    fun show(
        context: Context,
        title: String,
        body: String,
        channel: String = GENERAL,
        route: NotificationRoute = NotificationRoute.ASSISTANT,
        stableId: Int? = null,
    ) {
        initialize(context)
        if (!permissionGranted(context)) return
        val openIntent = Intent(context, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP
            putExtra(EXTRA_NOTIFICATION_ROUTE, route.wireValue)
        }
        val id = stableId ?: nextNotificationId.incrementAndGet()
        val pending = PendingIntent.getActivity(
            context,
            id,
            openIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val notification = NotificationCompat.Builder(context, channel)
            .setSmallIcon(R.mipmap.ic_launcher)
            .setContentTitle(title.take(120))
            .setContentText(body.take(500))
            .setStyle(NotificationCompat.BigTextStyle().bigText(body.take(1_500)))
            .setAutoCancel(true)
            .setContentIntent(pending)
            .setPriority(NotificationCompat.PRIORITY_DEFAULT)
            .build()
        NotificationManagerCompat.from(context).notify(id, notification)
    }
}

class EdecanFirebaseMessagingService : FirebaseMessagingService() {
    override fun onNewToken(token: String) {
        EdecanNotifications.syncToken(applicationContext, token)
    }

    override fun onMessageReceived(message: RemoteMessage) {
        val data = message.data
        EdecanNotifications.show(
            context = applicationContext,
            title = message.notification?.title ?: data["title"] ?: "Edecán",
            body = message.notification?.body ?: data["body"] ?: "Tienes una novedad.",
            channel = when (data["kind"]) {
                "reminder" -> EdecanNotifications.REMINDERS
                "job", "mission" -> EdecanNotifications.WORK
                "content" -> EdecanNotifications.CONTENT
                "call" -> EdecanNotifications.CALLS
                else -> EdecanNotifications.GENERAL
            },
            route = NotificationRoute.parse(data["route"] ?: data["screen"]),
            stableId = message.messageId?.hashCode(),
        )
    }
}

class LocalNotificationReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        EdecanNotifications.show(
            context = context,
            title = intent.getStringExtra(EXTRA_TITLE) ?: "Edecán",
            body = intent.getStringExtra(EXTRA_BODY) ?: "Tienes una novedad.",
            channel = intent.getStringExtra(EXTRA_CHANNEL) ?: EdecanNotifications.GENERAL,
            route = NotificationRoute.parse(intent.getStringExtra(EXTRA_NOTIFICATION_ROUTE)),
            stableId = intent.dataString?.hashCode(),
        )
    }
}

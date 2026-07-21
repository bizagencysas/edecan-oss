package cc.edecan.shared

import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue

/**
 * Tests de serialización de `Models.kt` contra ejemplos reales tomados de
 * `docs/api.md`/`ARCHITECTURE.md` §10.5/§10.12 — mismo criterio que
 * `ModelsTests.swift` en `EdecanKit` (iOS): el contrato es la API HTTP real,
 * así que estos tests fijan la FORMA exacta del JSON, no solo que
 * "decodifique algo".
 */
class ModelsTest {

    // -------------------------------------------------------------------
    // Autenticación
    // -------------------------------------------------------------------

    @Test
    fun tokenPair_decodifica_snake_case_del_backend() {
        val json = """{"access_token":"a1","refresh_token":"r1","token_type":"bearer"}"""
        val tokens = edecanJson.decodeFromString(TokenPair.serializer(), json)
        assertEquals("a1", tokens.accessToken)
        assertEquals("r1", tokens.refreshToken)
        assertEquals("bearer", tokens.tokenType)
    }

    @Test
    fun tokenPair_token_type_por_defecto_es_bearer_si_el_backend_lo_omite() {
        val json = """{"access_token":"a1","refresh_token":"r1"}"""
        val tokens = edecanJson.decodeFromString(TokenPair.serializer(), json)
        assertEquals("bearer", tokens.tokenType)
    }

    // -------------------------------------------------------------------
    // GET /v1/me — flags (booleanos y límites numéricos en el mismo dict)
    // -------------------------------------------------------------------

    @Test
    fun me_nombrePila_toma_lo_que_hay_antes_de_la_arroba() {
        val json = """
            {"user":{"id":"u1","email":"ana@acme.test","created_at":"2026-07-01T10:00:00Z"},
             "tenant":{"id":"t1","name":"Acme","slug":"acme","plan_key":"hosted_pro","status":"active","created_at":"2026-07-01T10:00:00Z"},
             "flags":{}}
        """.trimIndent()
        val me = edecanJson.decodeFromString(Me.serializer(), json)
        assertEquals("ana", me.nombrePila)
    }

    @Test
    fun flags_mezcla_booleanos_y_limites_numericos_bajo_el_mismo_dict() {
        val json = """
            {"user":{"id":"u1","email":"ana@acme.test","created_at":"2026-07-01T10:00:00Z"},
             "tenant":{"id":"t1","name":"Acme","slug":"acme","plan_key":"hosted_pro","status":"active","created_at":"2026-07-01T10:00:00Z"},
             "flags":{"voice.web":true,"voice.telephony":false,"limits.messages_per_day":600,"limits.voice_minutes_month":-1}}
        """.trimIndent()
        val me = edecanJson.decodeFromString(Me.serializer(), json)
        assertTrue(me.flags.boolFlag("voice.web"))
        assertFalse(me.flags.boolFlag("voice.telephony"))
        assertFalse(me.flags.boolFlag("connectors.social")) // ausente -> default false.
        assertEquals(600, me.flags.intFlag("limits.messages_per_day"))
        assertEquals(-1, me.flags.intFlag("limits.voice_minutes_month")) // -1 == ilimitado.
        assertEquals(0, me.flags.intFlag("limits.storage_mb")) // ausente -> default 0.
    }

    @Test
    fun liveProfile_decodifica_identidad_compartida() {
        val json = """
            {"resumen":"Construye productos con IA.","datos":{
              "identidad":{"nombre_preferido":"Isacc","nombre_completo":"Isacc Lara","pronombres":"él","fecha_nacimiento":"8 de enero de 1996","pais":"Venezuela","ciudad":"Medellín","zona_horaria":"America/Bogota","ocupacion":"Fundador","idioma_preferido":"Español de Venezuela","forma_de_trato":"Cercano y directo","biografia":"Construye startups."},
              "gustos":[],"proyectos":["Edecán"],"metas":[],"relaciones":[],"empresas":[],"habitos":[]},
              "version":4,"updated_at":"2026-07-21T18:00:00Z"}
        """.trimIndent()

        val perfil = edecanJson.decodeFromString(LiveProfile.serializer(), json)

        assertEquals("Isacc", perfil.datos.identidad.nombrePreferido)
        assertEquals("Cercano y directo", perfil.datos.identidad.formaDeTrato)
        assertEquals(listOf("Edecán"), perfil.datos.proyectos)
        assertEquals(4, perfil.version)
    }

    // -------------------------------------------------------------------
    // Conversaciones y mensajes
    // -------------------------------------------------------------------

    @Test
    fun message_texto_lee_content_text_y_tolera_content_nulo() {
        val conTexto = edecanJson.decodeFromString(
            Message.serializer(),
            """{"id":"m1","role":"assistant","content":{"text":"Mañana tienes dos eventos."}}""",
        )
        assertEquals("Mañana tienes dos eventos.", conTexto.texto)

        val sinContenido = edecanJson.decodeFromString(
            Message.serializer(),
            """{"id":"m2","role":"assistant","content":null}""",
        )
        assertEquals("", sinContenido.texto)
    }

    @Test
    fun conversation_channel_desconocido_no_rompe_la_decodificacion() {
        // ARCHITECTURE.md §10.12 documenta 4 canales (web/voice/phone/api);
        // `channel` se deja como String suelto justamente para que un canal
        // nuevo del backend no tumbe un cliente móvil desactualizado.
        val conversacion = edecanJson.decodeFromString(
            Conversation.serializer(),
            """{"id":"c1","title":"Viaje","channel":"whatsapp","created_at":"2026-07-01T10:00:00Z"}""",
        )
        assertEquals("whatsapp", conversacion.channel)
    }

    @Test
    fun conversation_restaura_confirmacion_publica_pendiente() {
        val conversacion = edecanJson.decodeFromString(
            Conversation.serializer(),
            """{"id":"c1","pending_confirmation":{"tool_call_id":"mail-1","name":"enviar_correo","args":{"to":"ana@example.com","subject":"Hola"}}}""",
        )

        assertEquals("mail-1", conversacion.pendingConfirmation?.toolCallId)
        assertEquals("enviar_correo", conversacion.pendingConfirmation?.name)
        assertEquals(
            "ana@example.com",
            (conversacion.pendingConfirmation?.args as? JsonObject)?.get("to")?.let { it as JsonPrimitive }?.content,
        )
    }

    @Test
    fun mensajePersistidoRecuperaAdjuntosPrivadosYElEnvioUsaSoloIds() {
        val message = edecanJson.decodeFromString(
            Message.serializer(),
            """{"id":"m1","role":"user","content":{"text":"Revisa esto","attachments":[{"file_id":"f1","filename":"brief.pdf","mime":"application/pdf"}]}}""",
        )
        assertEquals(listOf(MessageAttachment("f1", "brief.pdf", "application/pdf")), message.adjuntos)
        assertEquals(
            """{"text":"Revisa esto","attachments":["f1"]}""",
            edecanJson.encodeToString(ChatMessageIn("Revisa esto", listOf("f1"))),
        )
    }

    // -------------------------------------------------------------------
    // ChatEvent (SSE) — docs/api.md §"Conversaciones y chat (SSE)"
    // -------------------------------------------------------------------

    @Test
    fun chatEvent_decodifica_las_6_variantes_pinned_por_su_campo_type() {
        assertEquals(
            ChatEvent.TextDelta("Mañana "),
            edecanJson.decodeFromString(ChatEvent.serializer(), """{"type":"text_delta","text":"Mañana "}"""),
        )
        assertEquals(
            ChatEvent.ToolEnd("agenda_eventos", "2 eventos encontrados"),
            edecanJson.decodeFromString(
                ChatEvent.serializer(),
                """{"type":"tool_end","name":"agenda_eventos","result_preview":"2 eventos encontrados"}""",
            ),
        )
        assertEquals(
            ChatEvent.Done(Usage(812, 143)),
            edecanJson.decodeFromString(
                ChatEvent.serializer(),
                """{"type":"done","usage":{"input_tokens":812,"output_tokens":143}}""",
            ),
        )
        assertEquals(
            ChatEvent.ErrorEvent("El proveedor LLM no respondió a tiempo"),
            edecanJson.decodeFromString(
                ChatEvent.serializer(),
                """{"type":"error","message":"El proveedor LLM no respondió a tiempo"}""",
            ),
        )

        val toolStart = edecanJson.decodeFromString(
            ChatEvent.serializer(),
            """{"type":"tool_start","name":"agenda_eventos","args":{"dia":"2026-07-08"}}""",
        )
        check(toolStart is ChatEvent.ToolStart)
        assertEquals("agenda_eventos", toolStart.name)

        assertEquals(
            ChatEvent.ToolProgress("construir_app", 12, "Edecán sigue trabajando", "call-1"),
            edecanJson.decodeFromString(
                ChatEvent.serializer(),
                """{"type":"tool_progress","tool_call_id":"call-1","name":"construir_app","elapsed_seconds":12,"message":"Edecán sigue trabajando"}""",
            ),
        )
        val dia = (toolStart.args as? JsonObject)?.get("dia") as? JsonPrimitive
        assertEquals("2026-07-08", dia?.content)

        val confirmacion = edecanJson.decodeFromString(
            ChatEvent.serializer(),
            """{"type":"confirmation_required","tool_call_id":"call_abc123","name":"enviar_correo","args":{"para":"x@acme.test"}}""",
        )
        check(confirmacion is ChatEvent.ConfirmationRequired)
        assertEquals("call_abc123", confirmacion.toolCallId)
        assertEquals("enviar_correo", confirmacion.name)
    }

    @Test
    fun toolEnd_conserva_referencias_de_artefactos_descargables() {
        val evento = edecanJson.decodeFromString(
            ChatEvent.serializer(),
            """{"type":"tool_end","name":"crear_pdf","result_preview":"PDF listo","artifacts":[{"file_id":"018f7f4c-07f4-7ed0-93c8-cf0525d1092b","filename":"propuesta.pdf","mime":"application/pdf"}]}""",
        )

        assertEquals(
            ChatEvent.ToolEnd(
                name = "crear_pdf",
                resultPreview = "PDF listo",
                artifacts = listOf(
                    ArtifactRef(
                        fileId = "018f7f4c-07f4-7ed0-93c8-cf0525d1092b",
                        filename = "propuesta.pdf",
                        mime = "application/pdf",
                    ),
                ),
            ),
            evento,
        )
    }

    @Test
    fun toolEnd_vincula_mision_asincrona_al_mismo_chat() {
        val evento = edecanJson.decodeFromString(
            ChatEvent.serializer(),
            """{"type":"tool_end","name":"delegar_mision","result_preview":"Misión creada","mission_id":"22222222-2222-4222-a222-222222222222"}""",
        )
        check(evento is ChatEvent.ToolEnd)
        assertEquals("22222222-2222-4222-a222-222222222222", evento.missionId)
    }

    @Test
    fun toolEnd_v06_decodifica_ids_bloques_y_acciones_sin_ejecutarlas() {
        val evento = edecanJson.decodeFromString(
            ChatEvent.serializer(),
            """{
              "type":"tool_end","tool_call_id":"call-v06","name":"buscar_vuelos",
              "result_preview":"Opciones listas","blocks_version":1,"blocks":[
                {"type":"media","schema_version":1,"media_kind":"image",
                 "artifact":{"file_id":"018f7f4c-07f4-7ed0-93c8-cf0525d1092b","filename":"mapa.png","mime":"image/png"},
                 "alt":"Mapa de la ruta"},
                {"type":"link_preview","schema_version":1,"url":"https://example.com/oferta",
                 "title":"Oferta oficial","site_name":"Example","source_mode":"live","actions":[
                   {"id":"open","label":"Abrir","action":"open_url","url":"https://example.com/oferta"},
                   {"id":"prefill","label":"Preguntar","action":"prefill_message","message":"Compara esta oferta"},
                   {"id":"future","label":"Futuro","action":"accion_futura","payload":"ignorado"}
                 ]},
                {"type":"flight","schema_version":1,"offer_id":"f1","airline":"AV","origin":"BOG",
                 "destination":"MIA","stops":0,"price":"199.00","currency":"USD","source_mode":"demo",
                 "actions":[{"id":"travel","label":"Viajes","action":"open_screen","screen":"travel"}]},
                {"type":"hotel","schema_version":1,"offer_id":"h1","name":"Hotel Uno","city":"MIA",
                 "price":"89.00","currency":"USD","source_mode":"unknown"},
                {"type":"future_card","schema_version":2,"fallback_text":"Resultado disponible"},
                {"type":"link_preview","schema_version":2,"fallback_text":"Enlace de una versión futura",
                 "url":"https://example.com/future","title":"Futuro"}
              ]
            }""".trimIndent(),
        )

        check(evento is ChatEvent.ToolEnd)
        assertEquals("call-v06", evento.toolCallId)
        assertEquals(1, evento.blocksVersion)
        assertEquals(6, evento.blocks.size)
        val media = evento.blocks[0] as ChatBlock.Media
        assertEquals("image", media.mediaKind)
        val link = evento.blocks[1] as ChatBlock.LinkPreview
        assertTrue(link.actions[0] is ChatAction.OpenUrl)
        assertEquals("Compara esta oferta", (link.actions[1] as ChatAction.PrefillMessage).message)
        assertTrue(link.actions[2] is ChatAction.Unknown)
        assertEquals("demo", (evento.blocks[2] as ChatBlock.Flight).sourceMode)
        assertEquals("MIA", (evento.blocks[3] as ChatBlock.Hotel).city)
        assertEquals("Resultado disponible", (evento.blocks[4] as ChatBlock.Unknown).fallbackText)
        assertTrue(evento.blocks[5] is ChatBlock.Unknown)
    }

    @Test
    fun toolStart_v06_conserva_toolCallId_y_sigue_tolerando_su_ausencia() {
        val nuevo = edecanJson.decodeFromString(
            ChatEvent.serializer(),
            """{"type":"tool_start","tool_call_id":"call-1","name":"buscar_web","args":{}}""",
        ) as ChatEvent.ToolStart
        val anterior = edecanJson.decodeFromString(
            ChatEvent.serializer(),
            """{"type":"tool_start","name":"buscar_web","args":{}}""",
        ) as ChatEvent.ToolStart

        assertEquals("call-1", nuevo.toolCallId)
        assertNull(anterior.toolCallId)
    }

    @Test
    fun chatEvent_type_desconocido_cae_a_Unknown_en_vez_de_lanzar() {
        // Pedido explícito del work package original (ver KDoc de
        // `ChatEvent.Unknown`): un evento SSE nuevo que el backend agregue
        // mañana no debe tumbar el stream de una app que no se actualizó.
        val evento = edecanJson.decodeFromString(
            ChatEvent.serializer(),
            """{"type":"agent_thinking","detail":"algo nuevo del backend"}""",
        )
        assertTrue(evento is ChatEvent.Unknown)
    }

    @Test
    fun chatEvent_done_sin_usage_no_rompe() {
        val evento = edecanJson.decodeFromString(ChatEvent.serializer(), """{"type":"done"}""")
        check(evento is ChatEvent.Done)
        assertNull(evento.usage)
    }

    // -------------------------------------------------------------------
    // Negocios — Invoice.montoDouble() tolera número JSON o string
    // -------------------------------------------------------------------

    @Test
    fun montoDouble_lee_un_numero_json_real() {
        // El comportamiento real del backend (`fastapi.encoders.jsonable_encoder`
        // sobre un `Decimal` cuantizado a 2 decimales, ver docstring de
        // `Invoice`): SIEMPRE llega como número JSON, nunca como string.
        assertEquals(1234.5, JsonPrimitive(1234.5).montoDouble())
    }

    @Test
    fun montoDouble_tambien_tolera_un_string_por_si_cambia_el_backend() {
        assertEquals(100.0, JsonPrimitive("100.00").montoDouble())
    }

    @Test
    fun montoDouble_de_null_o_no_numerico_es_cero() {
        assertEquals(0.0, JsonNull.montoDouble())
        val elementoAusente: JsonElement? = null
        assertEquals(0.0, elementoAusente.montoDouble())
    }

    @Test
    fun invoice_decodifica_una_fila_real_de_v1_negocios_facturas() {
        val json = """
            {"id":"f1","tenant_id":"t1","user_id":"u1","numero":"F-2026-0001",
             "cliente_nombre":"Acme SA","cliente_email":null,"moneda":"USD",
             "subtotal":100.0,"impuestos":0.0,"total":100.0,"status":"draft",
             "due_date":null,"pdf_file_id":"file1","notas":"",
             "created_at":"2026-07-01T10:00:00Z","updated_at":"2026-07-01T10:00:00Z"}
        """.trimIndent()
        val factura = edecanJson.decodeFromString(Invoice.serializer(), json)
        assertEquals("F-2026-0001", factura.numero)
        assertEquals(100.0, factura.total.montoDouble())
        assertEquals("draft", factura.status)
    }

    // -------------------------------------------------------------------
    // Negocios — KPIs (siempre float del lado del servidor, kpis.py)
    // -------------------------------------------------------------------

    @Test
    fun negociosKpis_decodifica_por_canal_y_actividad() {
        val json = """
            {"mes":"2026-07","ingresos":1000.0,"gastos":200.0,"beneficio":800.0,
             "nuevos_clientes":2,"facturado":500.0,"cobrado":300.0,
             "por_canal":[{"canal":"web","total":1000.0}],
             "actividad":[{"tipo":"factura","id":"f1","fecha":"2026-07-01","descripcion":"Factura F-2026-0001 — Acme SA","monto":100.0,"moneda":"USD","status":"draft"}]}
        """.trimIndent()
        val kpis = edecanJson.decodeFromString(NegociosKpis.serializer(), json)
        assertEquals(1000.0, kpis.ingresos)
        assertEquals(800.0, kpis.beneficio)
        assertEquals(1, kpis.porCanal.size)
        assertEquals("web", kpis.porCanal.first().canal)
        assertEquals(1, kpis.actividad.size)
        assertEquals("factura", kpis.actividad.first().tipo)
    }

    // -------------------------------------------------------------------
    // Credenciales / setup — GET /v1/credentials, GET /v1/setup/status
    // -------------------------------------------------------------------

    @Test
    fun credentialsOut_bloques_no_conectados_son_null() {
        val json = """{"llm":null,"voice_stt":null,"voice_tts":null,"search":null}"""
        val credenciales = edecanJson.decodeFromString(CredentialsOut.serializer(), json)
        assertNull(credenciales.llm)
        assertNull(credenciales.voiceStt)
    }

    @Test
    fun credentialsOut_llm_conectado_trae_masked_no_el_secreto() {
        val json = """
            {"llm":{"kind":"claude_cli","model_principal":null,"model_rapido":null,"base_url":null,"masked":null},
             "voice_stt":{"provider":"deepgram","masked":"…9f2a"},"voice_tts":null,
             "search":{"provider":"brave","masked":"…7f3a"}}
        """.trimIndent()
        val credenciales = edecanJson.decodeFromString(CredentialsOut.serializer(), json)
        assertEquals("claude_cli", credenciales.llm?.kind)
        assertEquals("…9f2a", credenciales.voiceStt?.masked)
        assertEquals("brave", credenciales.search?.provider)
    }

    @Test
    fun setupStatus_usa_snake_case_local_mode_llm_configured() {
        val json = """{"local_mode":true,"llm_configured":false,"version":"0.4.0"}"""
        val estado = edecanJson.decodeFromString(SetupStatusOut.serializer(), json)
        assertTrue(estado.localMode)
        assertFalse(estado.llmConfigured)
        assertEquals("0.4.0", estado.version)
    }

    @Test
    fun llmCredentialsIn_serializa_validate_sin_guion_bajo() {
        // El body de PUT /v1/credentials/llm usa literalmente "validate"
        // (no un alias) — ver credentials.py `LLMCredentialsIn.validate_`
        // con `alias="validate"`.
        val payload = LlmCredentialsIn(kind = "anthropic", apiKey = "sk-ant-x")
        val json = edecanJson.encodeToString(payload)
        assertTrue(json.contains("\"validate\":true"))
        assertTrue(json.contains("\"kind\":\"anthropic\""))
        assertTrue(json.contains("\"api_key\":\"sk-ant-x\""))
    }

    @Test
    fun llmModels_catalogo_y_seleccion_manual_respetan_el_contrato() {
        val catalogo = edecanJson.decodeFromString(
            LlmModelsOut.serializer(),
            """{"kind":"ollama","model_principal":"qwen3:32b","model_rapido":"qwen3:8b","models":["qwen3:32b","qwen3:8b"],"manual_allowed":true,"capabilities_managed_by_edecan":true,"discovery_error":null}""",
        )
        assertEquals(listOf("qwen3:32b", "qwen3:8b"), catalogo.models)
        assertTrue(catalogo.capabilitiesManagedByEdecan)

        val payload = edecanJson.encodeToString(
            LlmModelsIn(modelPrincipal = "modelo-futuro", modelRapido = "modelo-rapido"),
        )
        assertTrue(payload.contains("\"model_principal\":\"modelo-futuro\""))
        assertTrue(payload.contains("\"model_rapido\":\"modelo-rapido\""))
    }

    // -------------------------------------------------------------------
    // IDE — GET /v1/ide/tree (árbol anidado)
    // -------------------------------------------------------------------

    @Test
    fun ideTreeOut_decodifica_nodos_anidados() {
        val json = """
            {"path":".","truncated":false,
             "entries":[
               {"name":"src","is_dir":true,"children":[
                 {"name":"main.py","is_dir":false,"size_bytes":120}
               ]},
               {"name":"README.md","is_dir":false,"size_bytes":40}
             ]}
        """.trimIndent()
        val arbol = edecanJson.decodeFromString(IdeTreeOut.serializer(), json)
        assertEquals(2, arbol.entries.size)
        val carpetaSrc = arbol.entries.first()
        assertTrue(carpetaSrc.isDir)
        assertEquals(1, carpetaSrc.children?.size)
        assertEquals("main.py", carpetaSrc.children?.first()?.name)
    }

    @Test
    fun ideFileOut_esBinario_solo_cuando_encoding_es_base64() {
        val texto = edecanJson.decodeFromString(
            IdeFileOut.serializer(),
            """{"path":"a.txt","content":"hola","encoding":"utf-8","size_bytes":4}""",
        )
        assertFalse(texto.esBinario)

        val binario = edecanJson.decodeFromString(
            IdeFileOut.serializer(),
            """{"path":"a.png","content":"aGVsbG8=","encoding":"base64","size_bytes":5}""",
        )
        assertTrue(binario.esBinario)
    }
}

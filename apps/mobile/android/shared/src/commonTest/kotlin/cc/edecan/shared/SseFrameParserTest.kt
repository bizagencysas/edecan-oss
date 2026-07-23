package cc.edecan.shared

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

/**
 * Tests del framing SSE puro ([SseFrameParser]) — mismo stream crudo de
 * ejemplo que documenta `docs/api.md` §"Conversaciones y chat (SSE)", línea
 * por línea, SIN abrir ningún canal/conexión real: [SseFrameParser] no
 * depende de red ni de coroutines, así que alimentarlo con `String`s sueltos
 * alcanza para fijar su contrato.
 */
class SseFrameParserTest {

    /** Alimenta [SseFrameParser] con `lineas` (una por elemento, SIN el
     * `\n` final) y, al terminar, llama [SseFrameParser.finalizar] — mismo
     * orden que sigue `SseClient.leerBloques` sobre un canal real. */
    private fun eventosDe(lineas: List<String>): List<ChatEvent> {
        val parser = SseFrameParser()
        val eventos = mutableListOf<ChatEvent>()
        for (linea in lineas) parser.procesarLinea(linea)?.let { eventos.add(it) }
        parser.finalizar()?.let { eventos.add(it) }
        return eventos
    }

    @Test
    fun parsea_el_stream_de_ejemplo_completo_de_docs_api_md() {
        val lineas = listOf(
            "event: message.delta",
            """data: {"type":"text_delta","text":"Mañana "}""",
            "",
            "event: message.delta",
            """data: {"type":"text_delta","text":"tienes dos eventos: "}""",
            "",
            "event: tool.start",
            """data: {"type":"tool_start","name":"agenda_eventos","args":{"dia":"2026-07-08"}}""",
            "",
            "event: tool.end",
            """data: {"type":"tool_end","name":"agenda_eventos","result_preview":"2 eventos encontrados"}""",
            "",
            "event: message.done",
            """data: {"type":"done","usage":{"input_tokens":812,"output_tokens":143}}""",
            "",
        )

        val eventos = eventosDe(lineas)

        assertEquals(5, eventos.size)
        assertEquals(ChatEvent.TextDelta("Mañana "), eventos[0])
        assertEquals(ChatEvent.TextDelta("tienes dos eventos: "), eventos[1])
        assertTrue(eventos[2] is ChatEvent.ToolStart)
        assertEquals("agenda_eventos", (eventos[2] as ChatEvent.ToolStart).name)
        assertEquals(
            ChatEvent.ToolEnd("agenda_eventos", "2 eventos encontrados"),
            eventos[3],
        )
        assertEquals(ChatEvent.Done(Usage(812, 143)), eventos[4])
    }

    @Test
    fun ignora_comentarios_keep_alive_que_empiezan_con_dos_puntos() {
        val eventos = eventosDe(
            listOf(
                ": keep-alive",
                "event: message.delta",
                """data: {"type":"text_delta","text":"hola"}""",
                ": otro comentario",
                "",
            ),
        )
        assertEquals(listOf(ChatEvent.TextDelta("hola")), eventos)
    }

    @Test
    fun sentinel_DONE_estilo_openai_cierra_el_turno() {
        val eventos = eventosDe(listOf("data: [DONE]", ""))
        assertEquals(listOf(ChatEvent.Done()), eventos)
    }

    @Test
    fun un_nuevo_event_cierra_el_bloque_anterior_si_el_relay_perdio_la_linea_vacia() {
        val eventos = eventosDe(
            listOf(
                "event: message.delta",
                """data: {"type":"text_delta","text":"hola"}""",
                "event: message.done",
                """data: {"type":"done"}""",
                "",
            ),
        )

        assertEquals(listOf(ChatEvent.TextDelta("hola"), ChatEvent.Done()), eventos)
    }

    @Test
    fun message_done_malformado_no_invalida_una_respuesta_ya_recibida() {
        val eventos = eventosDe(listOf("event: message.done\r", "data: cuerpo-legacy", "\r"))
        assertEquals(listOf(ChatEvent.Done()), eventos)
    }

    @Test
    fun ignora_lineas_sin_dos_puntos_sin_lanzar() {
        val eventos = eventosDe(
            listOf(
                "esto no tiene dos puntos y debe ignorarse",
                "event: message.delta",
                """data: {"type":"text_delta","text":"ok"}""",
                "",
            ),
        )
        assertEquals(listOf(ChatEvent.TextDelta("ok")), eventos)
    }

    @Test
    fun una_linea_en_blanco_sin_bloque_data_acumulado_no_emite_nada() {
        // Dos líneas en blanco seguidas (o una línea en blanco sin ningún
        // `data:` antes) no deben producir un evento fantasma.
        val eventos = eventosDe(listOf("", "event: message.delta", ""))
        assertTrue(eventos.isEmpty())
    }

    @Test
    fun type_desconocido_cae_a_Unknown_en_vez_de_lanzar() {
        val eventos = eventosDe(
            listOf(
                "event: agent.thinking",
                """data: {"type":"agent_thinking","detail":"algo nuevo"}""",
                "",
            ),
        )
        assertEquals(1, eventos.size)
        assertTrue(eventos.first() is ChatEvent.Unknown)
    }

    @Test
    fun payload_json_invalido_lanza_EventoInvalido_con_el_nombre_del_evento() {
        val parser = SseFrameParser()
        parser.procesarLinea("event: message.delta")
        parser.procesarLinea("data: esto no es JSON válido")
        val excepcion = assertFailsWith<SseClient.SseException.EventoInvalido> {
            parser.procesarLinea("")
        }
        assertTrue(excepcion.message?.contains("message.delta") == true)
    }

    @Test
    fun finalizar_cierra_un_bloque_que_nunca_tuvo_linea_en_blanco_final() {
        // El backend real siempre cierra con una línea en blanco (ver
        // `_format_sse` en `edecan_api.routers.conversations`), pero
        // `SseClient.leerBloques` igual llama `finalizar()` por si el
        // stream se corta antes de esa última línea — mismo caso que ya
        // documentaba el KDoc original de `leerBloques`.
        val parser = SseFrameParser()
        assertTrue(parser.procesarLinea("event: message.delta") == null)
        assertTrue(parser.procesarLinea("""data: {"type":"text_delta","text":"sin cierre"}""") == null)

        val evento = parser.finalizar()

        assertEquals(ChatEvent.TextDelta("sin cierre"), evento)
    }

    @Test
    fun estado_terminal_acepta_un_solo_done_y_rechaza_eventos_posteriores() {
        val terminal = SseTerminalState()

        assertTrue(terminal.aceptar(ChatEvent.TextDelta("hola")))
        assertTrue(terminal.aceptar(ChatEvent.Done()))
        assertTrue(terminal.finalizado)
        assertTrue(!terminal.aceptar(ChatEvent.TextDelta("duplicado")))
        terminal.validarCierre()
    }

    @Test
    fun error_del_agente_tambien_es_un_cierre_terminal_valido() {
        val terminal = SseTerminalState()

        assertTrue(terminal.aceptar(ChatEvent.ErrorEvent("Proveedor no disponible")))
        assertTrue(terminal.finalizado)
        terminal.validarCierre()
    }
}

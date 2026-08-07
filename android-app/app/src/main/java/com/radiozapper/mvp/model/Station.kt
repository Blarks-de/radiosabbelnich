package com.radiozapper.mvp.model

/**
 * Hartcodierte Senderliste fuer das MVP (siehe Scope: keine Settings-UI).
 * URLs sind Platzhalter oeffentlicher Streams - bei Bedarf einfach ersetzen.
 */
data class Station(val id: String, val name: String, val url: String)

object Stations {
    val ALL = listOf(
        Station(
            id = "dlf",
            name = "Deutschlandfunk (viel Sprache)",
            url = "https://st01.sslstream.dlf.de/dlf/01/128/mp3/stream.mp3"
        ),
        Station(
            id = "1live",
            name = "1LIVE (WDR, viel Musik)",
            url = "https://wdr-1live-live.icecastssl.wdr.de/wdr/1live/live/mp3/128/stream.mp3"
        ),
        Station(
            id = "swr3",
            name = "SWR3 (Musik/Moderation gemischt)",
            url = "https://liveradio.swr.de/sw282p3/swr3/play.mp3"
        ),
    )
}

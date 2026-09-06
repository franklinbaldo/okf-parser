---
type: NewsItem
uri: https://example.org/news/complete-profile
ninjs_type: text
representationType: full
profile: okf-journalism-ninjs-3.2
version: "1"
firstCreated: 2026-09-06T10:00:00Z
versionCreated: 2026-09-06T10:05:00Z
contentCreated: 2026-09-06T09:55:00Z
embargoedUntil: 2026-09-06T10:30:00Z
pubStatus: usable
urgency: 5
copyrightHolder: O Vigia
copyrightNotice: Copyright 2026 O Vigia
usageTerms: Attribution required.
edNote: Internal editorial note.
language: pt-BR
descriptions:
  - role: summary
    contentType: text/plain
    value: Complete top-level ninjs 3.2 coverage fixture.
headlines:
  - role: main
    contentType: text/plain
    value: Complete ninjs profile fixture
people:
  - name: Maria Exemplo
    rel: mentioned
    uri: https://example.org/people/maria
    literal: maria-exemplo
    contactInfo:
      - type: email
        role: office
        lang: pt-BR
        name: E-mail
        value: maria@example.org
organisations:
  - name: Organização Exemplo
    rel: mentioned
    uri: https://example.org/organisations/example
    literal: org-example
    symbols:
      - exchange: EXAMPLE
        symbolType: https://cv.iptc.org/newscodes/financialinstrumentsymboltype/Ticker
        symbol: EXM
    contactInfo:
      - type: address
        role: office
        lang: pt-BR
        name: Sede
        address:
          lines:
            - Avenida Exemplo, 100
          locality: Porto Velho
          area: RO
          postalCode: 76800-000
          country: BR
places:
  - name: Porto Velho
    rel: dateline
    uri: https://www.wikidata.org/entity/Q172143
    literal: porto-velho
    contactInfo:
      - type: web
        role: public
        value: https://www.portovelho.ro.gov.br/
    geoJSON:
      type: Point
      coordinates:
        - -63.9039
        - -8.7612
subjects:
  - name: Jornalismo local
    rel: about
    uri: https://example.org/subjects/local-journalism
    literal: local-journalism
    creator: O Vigia
    relevance: 100
    confidence: 100
events:
  - id: event-1
    name: Coletiva de imprensa
    rel: covers
    uri: https://example.org/events/1
    literal: coletiva-1
eventDetails:
  eventStatus: https://cv.iptc.org/newscodes/eventstatus/eStat1
  plannedCoverageStatus: https://cv.iptc.org/newscodes/newscoveragestatus/int
  dates:
    startDate: 2026-09-07T14:00:00Z
    endDate: 2026-09-07T15:00:00Z
  organiser:
    name: Organização Exemplo
    uri: https://example.org/organisations/example
plannedCoverage:
  - uri: https://example.org/planned-coverage/1
    title: Cobertura planejada
    pubStatus: usable
    type: text
    commissioned:
      by: Editor de plantão
      on: 2026-09-06T09:00:00Z
      references:
        - name: pauta
          value: pauta-123
    dates:
      expectedStartDate: 2026-09-07T14:00:00Z
      expectedEndDate: 2026-09-07T16:00:00Z
    audiences:
      - audience: local
        significance: 9
    exclAudience:
      - embargo-partners
    edNote: Confirmar horário antes da publicação.
    urgency: 4
    language: pt-BR
    itemCount:
      rangeFrom: 1
      rangeTo: 2
    wordCount: 800
    renditions:
      - name: planned-main
        contentType: text/html
        title: Planned web rendition
objects:
  - name: Documento público
    rel: mentioned
    uri: https://example.org/objects/document-1
    literal: document-1
infoSources:
  - name: Tribunal de Contas do Estado de Rondônia
    role: source
    uri: https://www.tce.ro.gov.br/
    literal: tce-ro
    contactInfo:
      - type: web
        role: public
        value: https://www.tce.ro.gov.br/
title: Complete ninjs profile fixture
by: Redação O Vigia
slugline: COMPLETE-NINJS
located: Porto Velho
renditions:
  - name: main-video
    href: https://example.org/media/video.mp4
    contentType: video/mp4
    title: Main video
    width: 1920
    height: 1080
    sizeInBytes: 123456
    duration: 42.5
    format: H.264
    aspectRatio: "16:9"
    videoCodec: H264
    frameRate: 29.97
    poi:
      x: 960
      y: 540
    transportProtocol: https
    scanType: progressive
    bitrate: 4 Mbps
    resources:
      - role: subtitles
        title: Portuguese subtitles
        language: pt-BR
        contentType: text/vtt
associations:
  - uri: https://example.org/news/associated
    name: Associated item
    rel: related
    type: text
    title: Related news item
altIds:
  - role: newsroom
    value: ovigia-123
trustIndicators:
  - role: https://cv.iptc.org/newscodes/trustindicator/transparency
    title: Transparency page
    href: https://example.org/trust
standard:
  name: ninjs
  version: "3.2"
  schema: http://www.iptc.org/std/ninjs/ninjs-schema_3.2.json#
genres:
  - name: News
    uri: https://cv.iptc.org/newscodes/genre/Current
    literal: current-news
expires: 2026-09-30T23:59:59Z
rightsInfo:
  langId: https://example.org/rights-language
  linkedRights: https://example.org/rights/complete-profile
digitalSourceType:
  name: Digital capture
  uri: https://cv.iptc.org/newscodes/digitalsourcetype/digitalCapture
  literal: digitalCapture
---

Este corpo Markdown é a representação autoral canônica do conteúdo textual. O perfil o projeta para `bodies` ao exportar ninjs.

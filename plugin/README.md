# IA Pediatria — plugin de referência do WebGuardião Core

Este diretório é a **preparação inicial** do plugin `ia.pediatria` para o
modelo de plugin/runtime descrito em *"Manual padrão para criação de IAs
como plugins do WebGuardião Core"* (Confluence). Ele **não** é ainda um
`.wgplugin` instalável — é o contrato declarado (manifest + schemas) que o
código deste repositório já implementa parcialmente e para o qual o resto
evolui.

## O que já existe vs. o que é só contrato

| Item do manifest | Estado real hoje |
|---|---|
| `runtime.type = local_service` | ✅ Implementado: API FastAPI local (`tools/run_pediatria_service_api.py`) + sessão headless (`src/jutta_ped/service/runtime.py`) |
| Descoberta do plugin pelo Core | ✅ Implementado: `GET /plugin/manifest` retorna o conteúdo deste `manifest.json` |
| Contrato de instância (`/instances`) | ✅ Implementado: `POST /instances`, `GET /instances`, `GET /instances/{id}`, `POST /instances/{id}/stop`, `GET /instances/{id}/summary` — reaproveitam `PediatriaSessionManager` por baixo (`instance_id` == `session_id` hoje); `/sessions/*` e `/cameras/*` continuam funcionando sem alteração |
| `outputs: risk_events, health, telemetry, evidence_refs` | ✅ Implementado, via `GET /instances/{id}` (status padronizado: `state`/`health`/`analysis_state`/`telemetry`), `GET /instances/{id}/events`, `session_summary.json` |
| `outputs: overlay_bboxes` | ⚠️ Parcial: `GET /instances/{id}/overlay/latest` expõe o **último** frame conhecido (polling), no formato de `schemas/overlay.schema.json`. Ainda não é um stream/push separado — para isso o cliente precisa consultar em loop |
| `optional_outputs: annotated_preview_stream` | ✅ Implementado como debug (vídeo com bbox desenhado), é o caminho atual, não o principal |
| `inputs: video_stream_ref` | ✅ Aceito de verdade em `POST /instances` (`stream_ref` obrigatório) e em `POST /cameras/enable`/`POST /sessions/start` (`stream_ref` preferido sobre `source`) — o serviço ainda não resolve `stream_ref` para um transporte próprio, mas o campo já é o caminho oficial de entrada |
| `ui.mode = optional_viewer` | ✅ Implementado: `src/jutta_ped/ui/` é só apresentação, fala com o serviço via `service_client.py` (hoje em processo; caminho futuro documentado nesse arquivo é falar com `/instances`) |
| `signing`, `lifecycle` (install/migration/rollback) | ❌ Não implementado — campos só declarados/documentados nesta rodada |

## Estrutura

```text
plugin/
  manifest.json              # identidade, inputs/outputs, runtime, schemas
  schemas/
    config.schema.json       # config operacional por instancia (stream_ref, camera_id, ...)
    ui.schema.json            # formulario que o Core deveria renderizar (checkbox + campos basicos)
    event.schema.json         # risk_events / target_context
    overlay.schema.json       # overlay/bbox — contrato PRINCIPAL de saida visual
  README.md                   # este arquivo
```

## `stream_ref` como modelo mental

O objetivo de longo prazo é o plugin **nunca** cadastrar câmera própria: ele
recebe uma `stream_ref` do mainframe e processa. Hoje o MVP ainda aceita
`source` (caminho de arquivo, URL RTSP/HTTP, índice USB) diretamente via
`POST /cameras/enable` — isso **continua funcionando sem quebrar nada**.
`schemas/config.schema.json` já modela os dois campos lado a lado
(`stream_ref` preferido, `source` como compatibilidade) para que a migração
futura seja só trocar quem preenche o campo, não mudar o contrato.

## Overlay/bbox como contrato principal

A decisão desta rodada: o vídeo anotado (bbox desenhado em cima do frame)
continua existindo, mas só como **debug/demo** — é o que o Demo Viewer usa
hoje. O contrato de integração real é `schemas/overlay.schema.json`: o Core
fornece o vídeo (plano de mídia), o plugin fornece só as bboxes/estado por
frame, e quem desenha por cima é o cliente (UI do Core ou o próprio Demo
Viewer). Isso evita duplicar codec de vídeo dentro do plugin e mantém o
barramento de eventos leve (vídeo nunca trafega por ele, conforme princípio
5 do AGENTS.md do Core).

## Como o Core ativaria esta IA (futuro)

1. Core lista plugins instalados/catalogados chamando `GET /plugin/manifest`
   de cada um; usuário marca o checkbox **"IA Pediatria"**
   (`manifest.ui.checkbox_label`) numa câmera.
2. Core renderiza o formulário a partir de `schemas/ui.schema.json`
   (só `enabled`, `camera_id` e alguns avançados — nunca thresholds/modelos).
3. Core resolve a câmera escolhida numa `stream_ref` e chama
   `POST /instances` com `{camera_id, stream_id, stream_ref, lease_id, config}`
   (endpoint do Node Agent/scheduler no futuro, mas o contrato HTTP já é
   este). O plugin responde com o status padronizado de instância
   (`plugin_id`, `instance_id`, `state`, `health`, `analysis_state`, ...).
4. Plugin processa frames e o Core consulta, por polling:
   `GET /instances/{id}` (saúde/estado), `GET /instances/{id}/overlay/latest`
   (`schemas/overlay.schema.json`, bbox do último frame) e
   `GET /instances/{id}/events` (`schemas/event.schema.json`, eventos
   recentes) — o Core não sabe nem precisa saber como a IA decide, só
   consome os contratos.
5. Desmarcar o checkbox chama `POST /instances/{id}/stop` (equivalente hoje
   a `POST /cameras/{id}/disable`, que continua funcionando para quem ainda
   fala o contrato antigo de câmera).

## O que falta para integração real com o Core

- Node Agent / heartbeat / leases (WG Runtime Contract) — `lease_id` já é
  aceito em `POST /instances` e devolvido no status, mas nada valida lease
  expirado/roubado ainda; o serviço roda standalone, sem se anunciar a
  nenhum scheduler.
- `overlay_bboxes` e `risk_events` são hoje só *pull* (`GET .../overlay/latest`,
  `GET .../events`), não um stream/push separado do vídeo anotado de debug.
- Estados `stopping`/`degraded` do contrato de instância estão previstos
  (`plugin_id`, `state` aceita os dois valores) mas não são emitidos: `stop()`
  é síncrono hoje (bloqueia até a thread da sessão terminar), então nunca
  fica observável em `stopping`; `degraded` não tem heurística ligada ainda.
- Empacotamento `.wgplugin` (assinatura, hash de artefato de modelo,
  instalação/rollback) — todos os campos relacionados no manifest estão
  como `not_implemented`.
- IDs globais e estáveis (site, nó, stream, instância) — hoje `instance_id`
  é local ao processo (== `session_id`), não federado.
- Demo Viewer (`src/jutta_ped/ui/`) ainda fala com o serviço em processo
  (`PediatriaService` direto), não via `/instances` — ver nota em
  `service_client.py`.

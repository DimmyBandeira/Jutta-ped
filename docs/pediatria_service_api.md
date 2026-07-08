# Serviço Local Jutta Ped

Rodada 2: API local para controlar sessões de pediatria sem depender da UI Qt.

Rodada 3: controle por câmera (ativar/desativar/consultar) independente da UI,
com uma sessão ativa por câmera e status operacional padronizado — base para
outro sistema plugar sem passar pela interface Qt.

## Subir localmente

Instale as dependências do MVP:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-mvp.txt
```

Suba a API:

```powershell
.\.venv\Scripts\python.exe tools\run_pediatria_service_api.py --host 127.0.0.1 --port 8765
```

Healthcheck:

```powershell
curl http://127.0.0.1:8765/health
```

## Endpoints

Camada por câmera (contrato recomendado para integração externa):

- `GET /cameras`
- `POST /cameras/enable`
- `POST /cameras/{camera_id}/disable`
- `GET /cameras/{camera_id}/status`

Camada por sessão (controle mais cru, usado internamente pela camada de câmera):

- `GET /health`
- `GET /sessions`
- `POST /sessions/start`
- `POST /sessions/{session_id}/stop`
- `GET /sessions/{session_id}/status`
- `GET /sessions/{session_id}/summary`

## Modelo câmera / sessão

Cada câmera tem no máximo **uma sessão ativa por vez**: uma câmera → uma
sessão → um worker (thread) de captura/inferência → evidências daquela
sessão. `PediatriaSessionManager` (`src/jutta_ped/service/runtime.py`) guarda
o mapa `camera_id -> session_id` e cria/reaproveita sessões:

- `POST /cameras/enable` é **idempotente**: se a câmera já tem uma sessão
  `running`, devolve o status dela em vez de abrir um worker duplicado. Se não
  tem, cria uma sessão nova (`PediatriaHeadlessSession`) e inicia a thread de
  captura.
- `POST /cameras/{camera_id}/disable` para a sessão associada àquela câmera
  (se existir) e grava o resumo final. Desativar uma câmera nunca ativa não é
  erro — devolve status `STOPPED`.
- `GET /cameras/{camera_id}/status` e `GET /cameras` nunca lançam 404: uma
  câmera desconhecida simplesmente aparece como `STOPPED`/`active: false`.
  Isso permite que outro sistema faça polling sem precisar rastrear estado
  próprio de "câmeras existentes".

Múltiplas câmeras rodam em paralelo (cada uma com sua própria thread e seu
próprio `PediatriaService`/modelo carregado); não há orquestração
distribuída — é tudo em processo único, mas isolado por câmera.

## Ativar/desativar uma câmera

Exemplo de `POST /cameras/enable`:

```json
{
  "source": "C:/videos/demo.mp4",
  "camera_id": "entrada_social",
  "force_cpu": true,
  "modo_coleta": false,
  "requested_device": "auto",
  "diagnostic_log_interval_frames": 60,
  "cooldown_seconds": 120
}
```

Resposta (formato igual em `enable`, `disable`, `status` e cada item de
`GET /cameras`):

```json
{
  "camera_id": "entrada_social",
  "session_id": "20260707_101500_ab12cd",
  "active": true,
  "session_status": "running",
  "operational_status": "ANALISANDO",
  "frames_processed": 128,
  "popup_count": 1,
  "suppressed_count": 0,
  "evidence_dir": "pediatria_results/service_api/evidence/sessions/20260707_101500_ab12cd",
  "summary_path": ".../session_summary.json",
  "last_error": null,
  "started_at": "2026-07-07T10:15:00.000",
  "stopped_at": null
}
```

`POST /cameras/{camera_id}/disable` não recebe corpo — só o `camera_id` na
URL.

A API recebe somente configuração operacional. Caminhos reais dos modelos,
thresholds finos e heurísticas permanecem na configuração interna do serviço.

## Status operacional padronizado

`operational_status` (calculado em `operational_status_from_session`) é um
dos seguintes valores, únicos e estáveis para qualquer sistema externo
consumir:

- `ANALISANDO` — sessão rodando, sem alerta ativo no momento
- `CRIANCA_ACOMPANHADA`
- `CRIANCA_DESACOMPANHADA`
- `SEM_PESSOA`
- `UNCERTAIN`
- `NO_CHILD`
- `STOPPED` — sessão parada/finalizada ou câmera nunca ativada
- `ERROR` — sessão encerrou com exceção (ver `last_error`)

`active` é um booleano derivado (`session_status == "running"`) para quem só
precisa saber "está processando ou não" sem interpretar o enum.

## Evidências

Cada sessão grava em:

```text
pediatria_results/service_api/evidence/sessions/<session_id>/
  frames/
  annotated/
  crops/
  metadata/
  events.jsonl
  session_summary.json
  session_summary.csv
```

## UI

A UI (`tools/run_pediatria_popup_mvp.py`) ainda instancia `PediatriaService`
diretamente em processo — ela não fala HTTP com esta API ainda. Ela já foi
reduzida (Rodada 1) para não expor configuração fina e apenas operar
start/stop e mostrar popup/estado. Ela não decide lógica de IA: só consome o
que `PediatriaService`/`PediatriaHeadlessSession` produzem.

Para a UI virar de fato um "viewer opcional" da API (Rodada 4 em diante),
falta:

1. Trocar a chamada direta a `PediatriaService` por um cliente HTTP para
   `/cameras/*` (`enable`/`disable`/`status`), sem tocar em `runtime.py`.
2. Um jeito de acompanhar eventos "ao vivo" sem polling pesado — hoje só há
   `events.jsonl` em disco e `GET /cameras/{id}/status` por polling; um
   endpoint de stream (SSE/WebSocket) é o que falta para um viewer reativo.
3. Rodar a API como processo separado da UI (hoje ambos podem estar no mesmo
   processo Python quando a UI é usada localmente).

## O que falta para plugar em outro sistema maior

- Autenticação/autorização nos endpoints (hoje é serviço local, sem auth).
- Persistência de configuração de câmeras (hoje o cadastro de câmera é
  implícito: só existe enquanto há sessão ativa ou já esteve ativa nesta
  execução do processo; reiniciar o serviço limpa `_camera_sessions`).
- Stream de eventos (item acima) para quem quiser reagir em tempo real em vez
  de fazer polling em `/cameras/{id}/status`.
- Rodar múltiplas câmeras com isolamento de processo (hoje é tudo em um único
  processo Python; câmeras adicionais competem por CPU/GPU do mesmo host).

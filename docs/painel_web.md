# Painel Web Jutta Ped

Painel de monitoramento (FastAPI + Jinja2 + HTMX) acoplado ao serviço local de
pediatria. Este documento é o contrato de implementação: os templates em
`web/templates/` já seguem os contextos descritos aqui; o backend será escrito
para satisfazê-los.

## 1. Decisões

| Tema | Decisão |
|---|---|
| Acesso | Login com sessão. Papéis `admin` e `comum`. |
| Banco | SQLite + SQLAlchemy 2.x (arquivo em `runtime/painel.db`). Troca para Postgres possível depois sem mudar as regras. |
| Vídeo ao vivo | go2rtc (player em iframe; WebRTC/MSE). Roda em Windows agora e em VM Linux depois. |
| Câmeras | O **banco é a fonte da verdade**; o go2rtc é reconciliado a partir dele (§5). O operador só cadastra a câmera. |
| Alertas | Popups por SSE + HTMX, com ciência (ack) persistida. |
| Integrações | Destinos (GPS Indoor, WifiPTT, Webhook) despachados por um *dispatcher* que o admin **habilita à mão** na UI (§7). |
| Offline | Sem CDN. HTMX e a extensão SSE são servidos de `web/static/vendor/`. |
| Layout | Código Python em `src/jutta_ped/`; templates e estáticos em `web/`. Rotas do painel sob `/painel/*`, sem colidir com a API JSON existente (`/cameras`, `/sessions`, `/instances`). |

## 2. Origem dos templates e como foram mesclados

Duas fontes foram unificadas em `web/templates/` + `web/static/`:

- **`templates/` (base adotada)**: veio do projeto "Conector GPS Indoor". Trouxe
  CSS próprio sem CDN, HTMX local, CRUD de câmeras/destinos/usuários com
  formulários HTMX, papéis admin/comum e a tela de configuração do go2rtc.
- **`web/` original (Tailwind/WebGuardião Core)**: só as ideias que faltavam foram
  aproveitadas: card de câmera com iframe go2rtc + canvas de overlay, desenho de
  bounding boxes e painel de eventos. O Tailwind por CDN foi descartado.

O material antigo foi arquivado, sem alteração, em `web/_referencia/` (pode ser
apagado quando o painel estiver completo).

Adaptações feitas sobre a base:

| Base | Mudança |
|---|---|
| Prefixo `/admin/*` | Trocado por `/painel/*` (evita colisão com a API). |
| `auditoria.html` | Virou `relatorio.html` (câmeras, destinos e alertas pediátricos). |
| Câmera | Removidos "térmica" e "duração do clipe" (não se aplicam); adicionados `ia_pediatria` e o fabricante `rtsp_generico`. |
| Destinos | Adicionado tipo `webhook` e o estado habilitado/desabilitado + "Enviar teste". |
| Configurações | Trocado o TTL de link de vídeo por: go2rtc (API, player público, RTSP), cooldown, retenção de evidências. |
| CSS | Extraído de `<style>` para `static/css/app.css` e ampliado (grade de câmeras, popups). |
| Novos | `login.html`, `monitoramento.html`, `partials/{camera_card,ia_estado,popup,alertas_tabela,eventos_recentes}.html`, `static/js/{live,popup}.js`. |

## 3. Mapa de rotas

Autenticação/CSRF: toda rota exige sessão, exceto `/login`. Requisições que
alteram estado exigem o token CSRF (`X-CSRF-Token` via `hx-headers` no `<body>`,
ou o campo `csrf_token` em formulários comuns).

| Rota | Papel | Descrição |
|---|---|---|
| `GET/POST /login`, `POST /logout` | público / logado | Sessão. |
| `GET /` | logado | Redireciona para `/painel/monitoramento`. |
| `GET /painel/monitoramento` | comum+ | Grade de câmeras + alertas recentes. |
| `GET /painel/stream` | comum+ | SSE: `event: popup` com o HTML de `partials/popup.html`. |
| `POST /painel/popups/{event_id}/ack` | comum+ | Dá ciência; devolve corpo vazio (remove o popup). |
| `GET /painel/cameras/{id}/overlay` | comum+ | Overlay da câmera (JSON do contrato de overlay + `frame_w`/`frame_h`). |
| `GET /painel/cameras/{id}/ia-estado` | comum+ | Fragmento `partials/ia_estado.html`. |
| `GET /painel/evidence/{event_id}/{kind}` | comum+ | Imagem de evidência (`crop`, `annotated`, `frame`). |
| `GET /painel/eventos/recentes` | comum+ | Fragmento `partials/eventos_recentes.html`. |
| `GET /painel/relatorio`, `GET /painel/relatorio/alertas` | comum+ | Relatório e tabela filtrável de alertas. |
| `/painel/cameras[...]` (listar, novo, editar, salvar, remover, campos-fonte, streams-do-yaml) | comum+ | CRUD de câmeras. |
| `/painel/destinos[...]` (CRUD, `POST .../{id}/teste`) | admin | Destinos e dispatcher. |
| `/painel/configuracoes` | admin | Configurações do sistema. |
| `/painel/usuarios[...]` | admin | CRUD de usuários. |

## 4. Papéis

| Recurso | comum | admin |
|---|---|---|
| Monitoramento, popups (ciência), relatório de alertas | ✔ | ✔ |
| Câmeras: listar, cadastrar, editar, remover, ligar/desligar IA | ✔ | ✔ |
| Status dos destinos no relatório | — | ✔ |
| Destinos (dispatcher) | — | ✔ |
| Configurações | — | ✔ |
| Usuários | — | ✔ |

Regras: senha com hash `scrypt` (stdlib); cookie de sessão assinado, `HttpOnly`,
`SameSite=Lax`; o último admin ativo não pode ser removido nem desativado;
login/logout, CRUD e ciência de alertas vão para a tabela de auditoria.

A API JSON existente (`/instances`, `/cameras/enable`…) é para máquina e hoje não
tem autenticação: deve ficar em `127.0.0.1` (ou protegida por token) — o painel
usa sessão e fala com o runtime em processo.

## 5. Câmeras e go2rtc

```
Operador salva câmera ─▶ banco (câmera + senha cifrada)
                              │
                        Go2rtcSync.reconcile()
                              ├─ gera runtime/go2rtc/streams.yaml (atômico)
                              └─ aplica a quente: PUT /api/streams (go2rtc)
```

- `go2rtc.yaml` estático (portas, API em localhost, WebRTC) fica separado do
  `streams.yaml` **gerado**; o go2rtc é iniciado com os dois (a suportar mais de
  um `-config` deve ser confirmado na versão adotada; alternativa: gerar o
  arquivo único).
- `reconcile()` é idempotente e roda a cada CRUD, no boot do painel e num retry
  periódico. O estado fica em `camera.go2rtc_sync` (`ok` / `pendente` / `erro`) e
  aparece na UI ("sincronizando go2rtc…"). Se o go2rtc estiver fora, salvar a
  câmera **não falha**.
- Nome do stream no go2rtc: `cam_<id>` (estável; independe do nome exibido).
- Senhas cifradas no banco (chave em `runtime/segredo.key`, permissão restrita);
  só o `streams.yaml` gerado contém a URL completa.
- **A IA lê o restream do go2rtc** (`rtsp://127.0.0.1:8554/cam_<id>`), sem abrir
  segunda conexão na câmera. Esse valor é o `stream_ref` de `POST /instances`.
  Ligar/desligar `ia_pediatria` chama `POST /instances` / `POST /instances/{id}/stop`.
- Player no navegador: `{go2rtc_public_url}/stream.html?src=cam_<id>`. O endereço
  público não pode ser `127.0.0.1` quando o painel é acessado de outra máquina.
- Windows agora / Linux depois: caminhos via `pathlib`, sem shell; o launcher
  inicia o go2rtc como subprocesso (Windows) ou o systemd o mantém (Linux). A
  reconciliação não depende de quem sobe o processo.

## 6. Fluxo de alertas (popup)

```
worker (thread) ── popup_alert ──▶ EventBus.publish()
                                       ├─ grava em eventos_pediatria (banco)
                                       ├─ ring buffer p/ replay (Last-Event-ID)
                                       ├─ SSE /painel/stream ─▶ HTML do popup ─▶ HTMX (beforeend)
                                       └─ Dispatcher (§7)
```

- O worker chama o barramento por um callback opcional da sessão
  (`_append_event`), sem reler o `events.jsonl`. Só `popup_alert` gera popup;
  `popup_suppressed` vai para o relatório como "suprimido".
- `event_id = "<session_id>_<frame_id>"` (estável, seguro em URL).
- Ciência (`ack`) grava usuário e hora; popups sem ciência reaparecem ao
  recarregar/navegar (`popups_pendentes`) e após reconexão do SSE.
- Evidências são servidas por `/painel/evidence/...`, resolvidas a partir do
  evento guardado — **nunca** por caminho vindo do cliente.
- O overlay agora traz `frame_w`/`frame_h` (feito na fase 3) para o canvas escalar corretamente.

Contexto de `partials/popup.html` (`ev`): `event_id`, `estado` (`CHILD_ALONE` |
`CHILD_SEPARATED`), `camera_nome`, `quando_fmt`, `confianca` (0–1 ou `None`),
`motivo`, `crop_url`, `annotated_url`.

## 7. Dispatcher de destinos

- Interface única `Destino.enviar(risk_event) -> Resultado`, uma implementação por
  tipo (`gpsindoor`, `wifiptt`, `webhook`). O evento enviado segue
  `plugin/schemas/event.schema.json`.
- Só destinos **habilitados** (`ativo=true`) recebem alertas. Novos destinos
  nascem desabilitados; o admin usa "Enviar teste" e depois habilita.
- Uma câmera escolhe quais destinos a recebem (vínculo N:N).
- Envio fora da thread de inferência e do SSE: fila com worker próprio e retry
  limitado; cada tentativa vira uma linha em `entregas` (sucesso, detalhe) e
  aparece no relatório.
- Credenciais dos destinos (senhas, tokens) cifradas como as das câmeras.

## 8. Modelo de dados (resumo)

`usuarios(id, login, senha_hash, papel, ativo)` ·
`cameras(id, nome, fabricante, ip, porta, canal, stream_tipo, usuario, senha_enc, rtsp_url_enc, go2rtc_stream_name, identificador_externo, ia_pediatria, go2rtc_sync, go2rtc_sync_erro)` ·
`destinos(id, nome, tipo, ativo, config_json_enc)` ·
`camera_destinos(camera_id, destino_id)` ·
`eventos_pediatria(id, event_id, camera_id, session_id, estado, confianca, suprimido, evidencias_json, criado_em, reconhecido_em, reconhecido_por)` ·
`entregas(id, evento_id, destino_id, destino_nome, sucesso, detalhe, criado_em)` ·
`configuracoes(chave, valor)` ·
`auditoria(id, usuario_id, acao, alvo, criado_em)`.

## 9. Templates: mapa e contexto

Todos compilam com Jinja2 (verificado). Variáveis globais em `base.html`:
`usuario_logado`, `eh_admin_logado`, `csrf_token`, `popups_pendentes`, `ativo`.

| Template | Contexto além dos globais |
|---|---|
| `monitoramento.html` | `cameras` (cada uma com `player_url`, `ia_ativa`, `ia_estado`, `go2rtc_sync`) |
| `partials/camera_card.html` | `c` |
| `partials/ia_estado.html` | `c.ia_ativa`, `c.ia_estado` |
| `relatorio.html` | `cameras` (+ `stream_online`, `ultimo_frame_em`, `ultimo_alerta_em`), `destinos_status` (só admin) |
| `partials/alertas_tabela.html` | `alertas` (com `camera_nome`, `estado`, `confianca`, `suprimido`, `reconhecido_*`, `entregas`, `crop_url`, `annotated_url`) |
| `cameras/*` | `cameras`, `camera`, `fabricante`, `destinos`, `destinos_vinculados_ids`, `streams_disponiveis` |
| `destinos/*` | `destinos`, `destino`, `tipo` |
| `usuarios/*` | `usuarios`, `usuario` |
| `configuracoes.html` | `config`, `salvo`, `sync_status` |

## 10. Fases de implementação

0. **Documento e fusão de templates** (esta etapa) — concluída.
1. Banco, login, sessão/CSRF, papéis e CRUD de usuários — **concluída** (§13).
2. CRUD de câmeras + `Go2rtcSync` + launcher do go2rtc (§11: partida assíncrona/escalonada e limite de câmeras com IA) — **concluída** (§14).
3. Monitoramento: player go2rtc e overlay (polling) + estado da IA por câmera — **concluída** (§15).
4. `EventBus`, SSE, popups, ciência persistida — **concluída** (§17).
5. Relatório e dispatcher de destinos (GPS Indoor, WifiPTT, Webhook).
6. Build Nuitka (incluir `web/templates` e `web/static`), limpeza de `web/_referencia/`.

## 11. Várias câmeras, threads separadas

Requisito: o painel opera **várias câmeras em paralelo**, cada uma isolada.

Estado atual do runtime (`PediatriaSessionManager`): uma thread por câmera, com
`PediatriaService` (e portanto modelos) próprio; uma falha numa câmera não derruba
as outras. Consequências e regras adotadas:

- **Isolamento de falhas:** cada câmera tem thread, reconexão e estado próprios.
  O painel nunca executa inferência nem I/O de câmera na thread da requisição.
- **Partida assíncrona:** hoje `start_session` carrega modelo e abre a fonte
  dentro da requisição HTTP. O painel passa a iniciar a IA em segundo plano
  (estado `starting` visível no card) e **escalonado**, para não travar a UI nem
  saturar CPU/GPU quando várias câmeras sobem juntas (boot do serviço).
- **Custo por câmera:** N câmeras = N cópias de modelo em memória. Medir RAM/VRAM
  por instância e definir um **limite configurável de câmeras com IA ativa**
  (recusa clara na UI ao exceder). Compartilhar um pool de inferência entre
  câmeras é uma otimização posterior, só se a medição justificar.
- **Barramento e dispatcher:** `EventBus` é thread-safe (chamado por N threads de
  worker). Os envios aos destinos rodam em fila/worker próprios, nunca na thread
  de inferência nem no loop do SSE.
- **Banco:** uma sessão SQLAlchemy por unidade de trabalho (nunca compartilhada
  entre threads); SQLite em modo WAL.
- **go2rtc:** `Go2rtcSync` tem um único escritor (lock) — CRUD simultâneo de várias
  câmeras não corrompe o `streams.yaml`.
- **Observabilidade:** a tela de Monitoramento/Relatório mostra por câmera o
  estado da thread (`starting`/`running`/`reconnecting`/`error`) e a latência.

## 12. Riscos e pontos em aberto

- Formato exato de chamada dos destinos GPS Indoor e WifiPTT: os templates só têm
  os campos de configuração; a lógica de envio virá do projeto de origem e deve
  ser trazida na fase 5.
- Confirmar na versão do go2rtc: múltiplos `-config` e `PUT /api/streams`.
- WebRTC exige UDP acessível ao navegador; se houver NAT/firewall, o fallback é MSE
  por TCP (configurar no `stream.html?mode=`).
- Overlay por polling (500 ms) pode ficar defasado do vídeo; aceitável na v1,
  com descarte de overlay velho (`frame_age_ms`).

## 13. Fase 1 — o que existe

Código em `src/jutta_ped/painel/` (`db`, `models`, `security`, `deps`, `routes_auth`,
`routes_usuarios`, `panel`); testes em `tests/test_painel_auth_usuarios.py`.

- **Entrypoint:** `src/jutta_ped/api/server.py` = API JSON existente + `mount_panel(app)`.
  `tools/run_pediatria_service_api.py` sobe esse app. `api/app.py` continua sem
  efeitos colaterais (importá-lo não cria banco).
- **Primeiro acesso:** sem usuários, cria `admin` com a senha de `JUTTA_ADMIN_PASSWORD`
  ou, se ausente, uma senha aleatória impressa **uma vez** no log do serviço.
  Variáveis: `JUTTA_ADMIN_LOGIN`, `JUTTA_ADMIN_PASSWORD`, `JUTTA_PANEL_DB`.
- **Arquivos gerados** (em `runtime/`, ignorado pelo git): `painel.db`,
  `painel_secret.key` (assinatura da sessão).
- **Segurança:** senha `scrypt`; sessão assinada (12 h, `HttpOnly`, `SameSite=Lax`),
  regenerada no login; CSRF em toda requisição que altera estado; bloqueio de
  60 s após 5 falhas seguidas (login+IP); resposta idêntica para login inexistente
  ou senha errada; usuário desativado perde a sessão na hora.
- **Regras de usuário:** login `[a-z0-9._-]{3,32}`, senha ≥ 8; não remove a si mesmo
  nem o último admin ativo; tudo auditado.
- **Páginas ainda em construção** (exibem aviso; permissões já valem): Monitoramento,
  Câmeras, Relatório, Destinos, Configurações.
- **Pendente de produção:** ligar `https_only` do cookie quando houver TLS/proxy.

## 14. Fase 2 — o que existe

Código novo em `src/jutta_ped/painel/`: `crypto`, `go2rtc`, `servicos`, `config_service`,
`routes_cameras`, `routes_config`; testes em `tests/test_painel_cameras_go2rtc.py`.

- **Câmeras** (`/painel/cameras`, qualquer usuário logado): criar, editar, remover,
  fabricantes Intelbras, Hikvision, RTSP genérico e stream de teste do go2rtc. A URL RTSP é
  montada a partir de IP/porta RTSP (padrão 554)/canal/stream principal ou extra, com
  usuário e senha codificados para URL. Senhas cifradas (Fernet, chave em
  `runtime/painel_cifra.key`) e **nunca** devolvidas ao navegador; a URL do RTSP genérico
  aparece mascarada ao editar e é mantida se voltar mascarada.
- **`Go2rtcSync`:** a cada CRUD (na hora, dentro da requisição), no boot e a cada 15 s,
  reescreve `runtime/go2rtc/streams.yaml` de forma atômica e aplica na API do go2rtc
  (`PUT`/`DELETE /api/streams`). Um hash da origem por câmera evita recriar streams
  inalterados; streams `cam_*` sem câmera no banco são removidos; streams de fora do painel
  (nome sem `cam_`) nunca são tocados. go2rtc fora do ar → a câmera é salva e fica
  `pendente` (recupera sozinha). Mensagens de erro nunca incluem a URL/credencial.
- **Processo do go2rtc** (`Go2rtcProcesso`): se o executável existir em
  `runtime/go2rtc/` (ou no caminho de Configurações, ou no PATH) o painel o mantém vivo
  com `-config go2rtc.yaml -config streams.yaml`, reiniciando se cair (intervalo mínimo
  10 s). Se a API já responde, assume que é externo e não interfere. O `go2rtc.yaml` base
  é criado só se não existir (API e RTSP em `127.0.0.1`, WebRTC em `:8555`) e nunca é
  sobrescrito.
- **IA em segundo plano** (`IaOrquestrador`): alinha as sessões de IA com
  `camera.ia_pediatria`, sem travar a UI. Só inicia com o stream `ok` no go2rtc e lê o
  restream (`rtsp://127.0.0.1:8554/cam_<id>`). Partidas escalonadas (3 s), limite de
  câmeras com IA (Configurações, padrão 4) e retry com backoff (5 s → 5 min) isolado por
  câmera. Editar/remover câmera reinicia/para a IA dela.
- **Configurações** (`/painel/configuracoes`, admin): endereços do go2rtc (API, player
  público, base RTSP), executável, cooldown, retenção, limite de IA, URL pública; mostra
  o estado da sincronização e do processo.
- **Ciclo de vida:** as threads (`painel-go2rtc`, `painel-ia`) sobem e param junto com o
  app (lifespan). `mount_panel(..., background=False)` desliga isso nos testes.

**Pendências / a confirmar com o go2rtc real:** múltiplos `-config` e o formato exato de
`PUT/DELETE /api/streams` foram implementados conforme a documentação, mas só foram
exercitados contra um go2rtc simulado — validar assim que o executável estiver disponível.
O go2rtc escuta só em `127.0.0.1`: para operadores em outras máquinas o vídeo deve passar
por um proxy autenticado do próprio painel (fase 3), em vez de expor a API do go2rtc.

## 15. Fase 3 — o que existe

Código: `src/jutta_ped/painel/routes_monitor.py`, `web/static/js/live.js`, templates
`monitoramento.html`, `partials/{camera_card,ia_estado}.html`; testes em
`tests/test_painel_monitoramento.py` (helpers compartilhados em `tests/painel_helpers.py`).

- **Monitoramento** (`/painel/monitoramento`, qualquer usuário logado): grade com um card
  por câmera. Player = iframe do `stream.html` do go2rtc (`src=cam_<id>` ou o stream de
  teste). O player só é oferecido quando o stream está `ok` no go2rtc. Botões Play/Pause por
  câmera e "Tocar todas"/"Pausar todas".
- **Host do player:** se `go2rtc_public_url` está em loopback (padrão) mas o operador abriu o
  painel por outro host, o painel troca o host pelo do navegador (mantém porta e esquema).
- **Overlay:** `GET /painel/cameras/{id}/overlay` devolve o último overlay da IA (contrato
  `plugin/schemas/overlay.schema.json`, agora com `frame_w`/`frame_h`). `live.js` consulta a
  cada 200 ms só as câmeras em Play, mapeia as coordenadas para a área real do vídeo
  (letterbox) e descarta overlay com mais de 2 s.
- **Estado da IA por card** (`/painel/cameras/{id}/ia-estado`, atualizado a cada 3 s):
  Analisando, Criança acompanhada/desacompanhada, Incerto, Iniciando, Aguardando stream,
  Erro (com o motivo no tooltip), Limite atingido, IA desligada.
- **SSE** (`/painel/stream`): já conecta e mantém a conexão com heartbeat; os popups saem
  por ele na fase 4. `run_pediatria_service_api.py` usa `timeout_graceful_shutdown=5`
  para o serviço encerrar mesmo com o painel aberto.
- **Correção encontrada na validação real:** a escrita de `streams.yaml` falhava no
  Windows/OneDrive (`Acesso negado` no rename). Agora tenta de novo, cai para gravação direta
  e, se ainda assim falhar, apenas registra aviso — a API a quente continua valendo.

**Validado contra o go2rtc real (1.9.14) e os modelos reais:** `PUT/DELETE /api/streams`
funcionam como implementado (o go2rtc persiste no próprio YAML e o `DELETE` reverte); uma
câmera apontando para um vídeo de demo foi cadastrada pelo painel, a IA subiu sozinha lendo
o restream `rtsp://127.0.0.1:8554/cam_N`, o estado alternou entre Analisando / Criança
acompanhada / desacompanhada / Incerto e o overlay chegou com `960x720` e várias caixas por
frame. Uma câmera com IP inalcançável falhou com backoff sem afetar a outra. Ao remover as
câmeras, os streams `cam_*` sumiram do go2rtc e o YAML do gateway ficou idêntico ao original.

**Navegador:** validado na seção 16. **Segurança:** a API do go2rtc
(`:1984`) devolve as URLs com senha para qualquer cliente que a alcance — restringir por
firewall/bind ao `127.0.0.1` em produção; um proxy autenticado do painel segue como opção
futura.

## 16. Testes de navegador (pytest-qt + Qt WebEngine)

`tests/test_painel_navegador_qt.py` sobe o painel real (uvicorn em thread, banco temporário,
IA falsa, go2rtc simulado no servidor) e o abre num Chromium de verdade (`QWebEngineView`).
Dependência de teste: `requirements-dev.txt` (`PyQt6-WebEngine`, não entra no release).
Flags do Chromium/Qt e `QT_QPA_PLATFORM=offscreen` ficam em `tests/conftest.py`
(precisam existir antes de qualquer `QApplication`). `JUTTA_TEST_SCREENSHOT_DIR=<pasta>`
grava PNGs das telas para inspeção visual.

Cobertura: login inválido/válido pelo formulário; htmx local carregado e **zero erros de
JS**; menu por papel; tabela de câmeras com colunas alinhadas; badge da IA atualizando
sozinho via htmx; overlay desenhado no canvas **respeitando o letterbox** (cor, bordas
esquerda/direita e áreas fora do vídeo); overlay velho descartado; Pause limpando iframe e
canvas; iframe do card carregando a página real do go2rtc; e um frame JPEG real do seu
go2rtc (960x720) decodificado no navegador.

**Limite do Qt WebEngine e como foi coberto:** ele não traz decodificador H.264 (nem WebRTC nem
MSE) e os streams de demo são H.264; por isso o teste de reprodução ali se pula (com o motivo).
A reprodução é provada em `tests/test_painel_navegador_edge.py`, que usa **Edge/Chrome reais**
via Playwright (`channel=msedge|chrome`, sem baixar navegador; `requirements-dev.txt`):
o vídeo H.264 toca no card (960x720, relógio do vídeo avançando), Pause para o player, e a
bbox do overlay coincide com a área real do `<video>` do go2rtc (`object-fit: contain`) com
diferença ≤ 1 px. Ruído conhecido filtrado no Qt: o `EventSource` do SSE gera um
`[object Event]` do htmx ao descartar a página na navegação.

**Defeitos achados só pelo navegador e corrigidos:** colunas da lista de câmeras trocadas
(badge de sync sob "Destinos"), coluna "Status" sempre "sem sinal" (placeholder removido) e
rótulo "admin · admin" no cabeçalho e, vistos no Edge real, o badge da IA e a barra de Play
cobrindo o rótulo e os controles nativos do player (agora nome, badge e Play ficam num rodapé
fora do vídeo). Também corrigido: câmeras `go2rtc_teste` nunca ficavam
com sync `ok`, então a IA não subia para elas.

## 17. Fase 4 — alertas em tempo real

Código: `src/jutta_ped/painel/eventos.py` (`PainelEventos`), `routes_alertas.py`; modelos
`EventoPediatria` e `Entrega` (esta última preenchida pelo dispatcher, fase 5); `popup.js`;
testes em `tests/test_painel_alertas.py` e nos dois módulos de navegador.

**Caminho do alerta:** thread de inferência → `PediatriaHeadlessSession._append_event` →
ouvinte da sessão → `PediatriaSessionManager._dispatch_event` → `PainelEventos.on_runtime_event`
→ (1) grava `eventos_pediatria` (idempotente por `event_id = <session_id>_<frame_id>`) e
(2) publica nas filas dos navegadores conectados (`loop.call_soon_threadsafe`). Ouvinte que falha
é registrado e isolado: nunca derruba a análise nem os demais ouvintes. `popup_suppressed`
(cooldown) é gravado para o relatório, mas **não** vira popup.

- **SSE `/painel/stream`:** `event: popup` (HTML pronto de `partials/popup.html`, com `id:` = id
  do banco) e `event: ack` (event_id reconhecido por outro operador). Heartbeat a cada 15 s.
  Reconexão com `Last-Event-ID` reenvia os popups sem ciência perdidos; a inscrição é feita
  antes do replay para nada se perder no intervalo.
- **Ciência:** `POST /painel/popups/{event_id}/ack` (qualquer usuário logado) grava quem e quando
  (só o primeiro ack vale), audita e avisa os demais navegadores, que removem o popup. O botão usa
  `hx-swap="none"` e remove o popup só após sucesso: o `ack` do SSE chega antes da resposta do POST
  e trocar um alvo já removido quebrava o htmx (achado pelos testes de navegador).
- **Pendentes:** popup sem ciência (e com menos de 12 h) reaparece ao recarregar/navegar em
  qualquer página completa, no máximo 10; `popup.js` descarta duplicatas (pendente da página +
  SSE) pelo `id` do elemento. Fragmentos HTMX não consultam o banco.
- **Evidências:** `GET /painel/evidence/{event_id}/{crop|annotated|frame}`. O caminho vem do
  **banco**, nunca do cliente, e só é servido se estiver dentro das pastas configuradas
  (`evidence_roots`; no serviço, `pediatria_results/service_api`), for imagem e existir; senão 404.
- **Alertas recentes** (Monitoramento): últimos 15, incluindo suprimidos; atualiza na hora quando um
  popup entra/sai (e a cada 10 s). Histórico sobrevive à remoção da câmera (guarda o nome).
- **Som:** `popup.js` toca dois bipes quando chega popup novo (o navegador só libera áudio após o
  primeiro clique/tecla na sessão).
- **UI:** a pilha de popups fica abaixo do cabeçalho (não cobre "Sair").

**Validado em navegador:** popup aparece ao vivo com a imagem de evidência decodificada; ciência
em um operador remove o popup no outro (Edge, dois contextos); recarregar não duplica; ciência
persistida não volta; console sem erros.

**Pendente (fase 5):** relatório completo com filtros, dispatcher de destinos e a coluna "Envio".

## 18. Testes operacionais (ponta a ponta, sistema real)

`tools/teste_operacional_painel.py` sobe o serviço real (modelos de IA reais, go2rtc real, várias câmeras, SSE, falhas induzidas,
reinício e encerramento) e gera `docs/testes_operacionais/AAAA-MM-DD_HHMM.md`. Seguro com o go2rtc do gateway: só cria/remove
streams `cam_*` e confere ao final que o YAML ficou idêntico. Uso: `.venv\Scripts\python.exe tools\teste_operacional_painel.py [--duracao 150]`.

Cobre: boot, IA em segundo plano, fps/CPU/RAM por câmera, duas câmeras em paralelo, isolamento de câmera com falha, alerta real da IA por
SSE (dois operadores, evidência JPEG, ciência), limite de evidências de suprimidos, auto-cura do go2rtc, edição (nova sessão de IA),
encerramento gracioso, reinício (sessão e câmeras preservadas) e limpeza.

Última execução: **15/15 passos aprovados** (`docs/testes_operacionais/2026-10-03_1352.md`, com a análise e as pendências). A primeira
execução (`..._1338.md`) está guardada como referência do estado antes das correções (1 falha: alertas recentes cobertos por suprimidos).

Mudanças que vieram dos achados: evidência de suprimidos limitada (`SessionStartConfig.suppressed_evidence_interval_seconds`, 10 s),
suprimidos fora das recentes e no máximo 1 por câmera/30 s no banco, SSE que termina com o serviço (`painel/encerramento.py` +
`tools/run_pediatria_service_api.py` usando `uvicorn.Server`), parada de todas as sessões de IA no encerramento.

## 19. Uso real: popup e caixas do overlay

Dois problemas relatados na primeira utilização.

**Popup difícil de usar.** Ficava sobre os botões "Tocar/Pausar todas" e não tinha como ser dispensado.
- Botão **×** em cada popup. Dispensar **não é dar ciência**: esconde o popup neste navegador (`sessionStorage`, até fechar a aba);
  o alerta continua *pendente* em "Alertas recentes", agora com botão **Ciente** na própria linha.
- **"Fechar todos os alertas"** aparece quando há 2 ou mais popups.
- A pilha foi para o **canto inferior direito** (não cobre cabeçalho, botões nem o Play do card) e o popup ficou compacto
  (miniatura 72 px; o código interno do motivo deixou de ser exibido).
- Achados pelos testes de navegador: o atributo `hidden` perdia para o `display` da classe `.btn` (o botão "fechar todos" ficava
  sempre visível e interceptava cliques); agora há `[hidden] { display:none !important }`.

**Caixas "perdidas": a IA analisava imagens antigas.** Medido com `tools/medir_defasagem_overlay.py` (correspondência de imagem entre os
frames da IA, o `<video>` do Edge e o arquivo de origem, tudo no mesmo instante):

| Situação | Atraso da IA em relação ao stream ao vivo |
|---|---|
| 1 câmera (antes) | 0,8 s |
| **3 câmeras (antes)** | **~12 s**, estável: o atraso acumulado na subida nunca era recuperado |
| 3 câmeras (depois) | 0,85–0,93 s (0,25 s atrás da imagem exibida no Edge, que tem ~0,55 s de atraso próprio) |

Causa: o runtime lia o stream em sequência (`read()` → inferir → `read()`), sem descartar frames. **Correção:** `LatestFrameCapture`
(`runtime.py`) — uma thread lê continuamente e a IA sempre pega o frame mais recente (descartando os atrasados, contados em
`dropped`; exposto em `telemetry.capture.frames_dropped`). Vale só para fontes de rede e dispositivo; arquivos de vídeo continuam
processando todos os frames. Se nenhum frame novo chega em 10 s, `read()` falha e a reconexão existente assume. Também se definiu
`OPENCV_FFMPEG_CAPTURE_OPTIONS` (TCP, `nobuffer`, `low_delay`); medir variações das opções do FFmpeg mostrou o mesmo piso (0,64 s)
em todas, ou seja, a latência restante não vem do buffer do leitor. O overlay passou a ser consultado a cada 200 ms, e uma resposta
que chega depois do Pause não redesenha mais as caixas.

**Efeito colateral a conhecer:** se a IA for mais lenta que o stream, ela agora pula frames (antes processava todos, mas cada vez mais
atrasada). As regras de companhia/identidade operam por frame processado; vale validar com cenas rápidas.


## 20. Especialistas (administração)

Tela `/painel/especialistas` (**somente administrador**; item "Especialistas" no menu). Instala, ativa, reverte e remove
pacotes declarativos de especialista. Contrato do pacote e do instalador: [manual do especialista](manual_especialista.md)
(seções 12 e 13). O painel **não** lê nem escreve o registro de instalação: tudo passa pelo serviço `Instalador`
(`app.state.especialistas`, armazenamento em `runtime/especialistas/`).

| Rota | Efeito |
|---|---|
| `GET /painel/especialistas` | Lista especialistas, versões, estado, versão ativa e operações recentes |
| `POST /painel/especialistas/instalar` | Upload `.zip` (campo `pacote`): valida, extrai isolado e registra; fica **inativo** |
| `POST /painel/especialistas/{id}/{versao}/ativar` | Confere integridade e torna a versão ativa |
| `POST /painel/especialistas/{id}/reverter` | Volta à última versão anterior válida |
| `POST /painel/especialistas/{id}/{versao}/remover` | Remove uma versão inativa |
| `POST /painel/especialistas/recuperar` | Conclui ou desfaz operações interrompidas |

Segurança: token CSRF obrigatório em todo POST (formulário comum, campo `csrf_token`); o teto de upload (600 MiB, `app.state.especialistas_upload_max`) é aplicado por um middleware ASGI
(`painel/limite_corpo.py`) **antes do parser multipart**: `Content-Length` acima do teto responde 413 sem ler o corpo, e sem
`Content-Length` (ou com cabeçalho falso) os bytes recebidos são contados e a leitura é interrompida ao passar do teto
(o FastAPI lê o corpo antes de resolver dependências, por isso não é uma dependência); o nome do arquivo enviado nunca é
usado como caminho (o upload vai para `uploads/<uuid>.zip` e é apagado ao final); texto vindo do pacote é escapado na
tela. Toda ação grava auditoria (`especialista_instalado`, `_instalacao_recusada`, `_ativado`, `_revertido`, `_removido`,
`especialistas_recuperados`, e `*_falhou`). Pacote reprovado devolve 422 com a tabela de problemas do validador; operação
concorrente devolve 409.

**O que a tela NÃO faz:** ativar uma versão apenas a registra como vigente; o motor de análise ainda não consome
especialistas. A validação é estática (não carrega nem aprova o modelo). Receitas e vínculo com câmeras não existem ainda.

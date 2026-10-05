# Etapa 05e — Dashboard do sistema-mãe (lote de interface da etapa 05)

Estado: **em andamento** (este lote). Prompt de origem: [`prompt_dashboard_sistema_mae.md`](../prompt_dashboard_sistema_mae.md). A etapa 03 permanece em andamento; as etapas 02 e 05 inteiras **não** são marcadas como concluídas. Alterações deste lote ficam **sem commit** para revisão.

Mockup de referência (somente leitura, fora do repositório): `003 - WEBALERTAS/VM-alertaRedeDor/maindash.html`. Foi usado só como referência visual; seus dados, textos e controles fictícios não são requisitos.

## Mockup → adaptação → fonte real

| Elemento do mockup | Adaptação | Fonte real dos dados |
|---|---|---|
| Menu lateral escuro | Monitoramento · Câmeras · Alertas (relatório) · grupo *Administração* (admin): Especialistas, Receitas, Processamento, Configurações, Usuários. **Destinos fica fora do menu** (a página ainda é "em construção") | papel do usuário (`eh_admin_logado`) |
| Indicadores (4 cartões) | Câmeras cadastradas · Streams sincronizados (configuração no go2rtc; não prova vídeo) · Análise em execução · Alertas pendentes de ciência | `cameras`, `go2rtc_sync`, sondagem em cache da API do go2rtc, `IaOrquestrador.estado()`/`camera_status`, `eventos_pediatria` (sem ciência, não suprimidos) |
| Gráfico acessos × visitantes | **Alertas por período/câmera** (24 h por hora, 7 e 30 dias por dia); zero real e "sem alertas" quando vazio | `eventos_pediatria` agrupado por hora/dia (não suprimidos) |
| Grade de câmeras | Player go2rtc, overlay e Play existentes; selos de conexão, análise, **receita associada** e **sombra** | `visao_camera` + associação câmera→receita |
| Alertas em tempo real | Coluna de alertas (lista) com filtro por câmera e "só pendentes", botão **Ciente**; popups SSE e `alertas-mudaram` reaproveitados (uma única conexão SSE, a de `base.html`) | `PainelEventos.recentes/reconhecer` |
| Presets de IA | **Receitas**: nome, versão, especialistas (versão ativa), câmeras e validade atual. Sem presets de temperatura/acesso/facial | tabelas `receitas`/`receita_versoes` + contratos dos especialistas ativos |
| Processamento | Página com saúde do serviço, go2rtc, sessões de IA, executor local/especialistas e sombra; **nós remotos: "não implementado"** | serviços reais; nada simulado |
| *(sem equivalente)* | Área **Sombra (diagnóstico — não são alertas)**: veredito, atributo, idade, reutilização, falha recente, incompatibilidade, versão/config | `GerenteSombra` (buffer em memória + rastro `.jsonl`), nunca o dispatcher de alertas |

Fora do lote (registrado): módulo Visitantes/WebAcesso, temperatura, reconhecimento facial, nós remotos, térmico, ativação operacional de receitas novas, regras entre trilhas/persistência (próximo lote de arquitetura: mestre/`Cena` genéricos → relações entre trilhas → adaptação do classificador atual).

## Contrato de integração (backend ⇄ interface)

Rotas sob `/painel` (CSRF em toda mutação via `verifica_csrf`; autorização no servidor; auditoria das mutações). `ativo` = chave do menu. Formulários usam `<input type="hidden" name="csrf_token" value="{{ csrf_token }}">`; HTMX já envia `X-CSRF-Token` pelo `<body>`.

| Rota | Papel | Resposta / contexto |
|---|---|---|
| `GET /monitoramento` | logado | `monitoramento.html`: `cameras` (lista de `c`: `id, nome, player_url, ia_ativa, ia_estado, ia_erro, sync_texto, receita_label` (str ou None), `sombra_ativa` (bool), `sombra_estado` (`desligada`/`aguardando`/`ativa`/`erro`), `sombra_erro`), `cameras_opcoes` (id, nome), `ativo="monitoramento"` |
| `GET /indicadores` | logado | fragmento `partials/indicadores.html`, `ind`: `cameras_total, streams_sincronizados, go2rtc` (`ok`/`inacessivel`/`desatualizado`/`desconhecido`)`, go2rtc_verificado_ha_s, analise_em_execucao, alertas_pendentes, ia_disponivel` (bool; False = sem gerente de IA → "indisponível") |
| `GET /alertas/historico?periodo=24h\|7d\|30d&camera=<id>` | logado | JSON `{periodo, unidade: "hora"\|"dia", camera: id\|null, total, vazio, pontos: [{inicio: ISO, rotulo, total}]}`; 422 com `{detail}` se parâmetro inválido |
| `GET /eventos/recentes?camera=<id>&pendentes=1` | logado | fragmento `partials/eventos_recentes.html` (lista de cartões): `alertas` (visões de `eventos.para_visao`), `filtro_camera`, `so_pendentes` |
| `POST /popups/{event_id}/ack` | logado | existente (ciência) |
| `GET /receitas-resumo` | logado | fragmento `partials/receitas_resumo.html`: `receitas` = lista de `{id, nome, versao, descricao, especialistas: [{id, versao_ativa (str\|None)}], cameras: [nome], valida (bool), motivos: [str]}` |
| `GET /sombra/feed?camera=<id>` | logado | fragmento `partials/sombra_feed.html`: `sombra` = `{eventos: [{camera_nome, trilha_id, quadro_id, veredito, idade_max_ms, condicoes: [{especialista, atributo, igual, resultado, motivo, idade_ms, reutilizado, falha_recente}], incompatibilidades: {pid: [str]}, receita: "nome vN", config_hash, versoes: [{id, versao}], quando}], cameras: [{id, nome, estado, erro, enfileirados, descartados, processados}]}` |
| `GET /receitas` | admin | `receitas.html`: `receitas` (cada uma com `versoes`: `[{id, versao, config_hash, criado_em, criado_por, resumo}]`), `especialistas_ativos` (`[{id, nome, versao, atributos: [{nome, tipo, categorias}]}]`), `cameras_receita` (`[{id, nome, receita_versao_id, receita_label, valida, motivos, sombra_ativa, sombra_estado, sombra_erro}]`), `opcoes_versao` (`[{id, label}]`), `flash`, `erro` |
| `GET /receitas/nova`, `GET /receitas/{rid}/editar` | admin | `receitas_form.html`: `form` (`{rid, nome, descricao, combinacao, vinculos: {pid: {usar, orcamento_ms, margem_recorte}}, condicoes: [{especialista, atributo, igual, validade_ms}]}` — 4 linhas de condição), `especialistas_ativos`, `erro`, `problemas` ([str]), `editando` (bool) |
| `POST /receitas` · `POST /receitas/{rid}/versoes` | admin | cria receita / nova versão **imutável**. Campos: `nome, descricao, combinacao, vinc_usar` (lista de pid), `vinc_orcamento__<pid>`, `vinc_margem__<pid>`, listas alinhadas `cond_esp, cond_attr, cond_igual, cond_validade`. Sucesso: 303 → `/painel/receitas` com flash. Inválido: 422 re-renderiza o formulário com `erro`/`problemas` e os valores digitados; **nada é gravado** |
| `POST /cameras/{id}/receita` | admin | campo `receita_versao_id` (vazio = remover). Valida contra especialistas ativos; inválido = flash de erro e associação anterior preservada. Remover a associação desliga a sombra |
| `POST /cameras/{id}/sombra` | admin | campo `acao=ligar\|desligar`. Ligar exige associação válida; sombra **nasce desligada** |
| `GET /processamento` | admin | `processamento.html`, `proc`: `servico` (`iniciado_em, ia_disponivel, max_cameras_ia`), `go2rtc` (`api_ok, streams, sincronizadas`), `sessoes` (`[{camera, estado, frames, popups, dispositivo, ultimo_erro}]`), `especialistas` (`[{id, versao, identidade_curta}]`), `sombra` (`{ativas, cameras: [...]}` como em `sombra/feed`), `nos_remotos` (`{implementado: False, texto}`) |
| `GET /relatorio` · `GET /relatorio/alertas?camera_id=&situacao=&de=&ate=` | logado | página e fragmento (`partials/alertas_tabela.html`) do histórico de alertas; substitui o "em construção" |

Garantias de projeto (cada uma com teste):
1. Consultar página **não** reinicia/reconfigura análise; só mutações explícitas alteram estado.
2. A sombra por câmera usa uma **porta** (`PortaSombra`) entregue à sessão no início e **nunca** trocada: ligar/desligar/reconfigurar altera o alvo da porta, não reinicia a análise. Ao vivo, apenas `DespachoSombra`.
3. Configuração atômica: receita ou associação inválida preserva a anterior e aparece ao usuário.
4. Eventos de sombra **não** chegam a `PainelEventos`/dispatcher de destinos nem ao `events.jsonl` operacional.
5. Ciclo de vida: criar, parar sessão, reiniciar, reconfigurar e desligar o serviço não acumulam threads de despacho; itens de geração obsoleta não são processados.

## Resultado do lote (2026-10-04, sem commit)

**Pronto (ponta a ponta, validado em navegador):** novo layout; indicadores reais; histórico de alertas por período/câmera; grade com selos de vídeo/análise/receita/sombra; lista de alertas com filtros e Ciente; criar receita e **nova versão imutável** por formulário, validada contra especialistas ativos (erro não grava); associar câmera→versão; ligar/desligar sombra por câmera sem reiniciar a análise; página Processamento; Relatório de alertas (deixou de ser "em construção").
**Somente sombra:** execução das receitas (diagnóstico; nunca alerta, popup ou envio). Ativação operacional de receitas **não** existe.
**Não implementado (e dito na tela):** nós remotos; Destinos (fora do menu); relação entre trilhas e persistência temporal; política de alerta já ativo diante de indeterminado; `invalidar_ao_errar`.

Correções do motor conferidas por código e regressões: revalidação da receita após troca de contrato; fila limitada por câmera e recusa do síncrono ao vivo; timestamp de recepção; cache `manter_ate_expirar` com reutilização e falha recente no evento. Defeitos de ciclo de vida do `DespachoSombra` corrigidos (parar sem juntar a thread; pendentes após parar; geração obsoleta; `aguardar_ociosa`), com testes de criar/parar repetido sem threads vivas. A frase antiga do registro 03 que sugeria "validade curta" como invalidação foi corrigida. O teste de não bloqueio é **estrutural**, não benchmark ao vivo.

**Decisões técnicas:** migração 0003 não usa `batch_alter_table` no SQLite (recriar `cameras` com `foreign_keys=ON` apagaria em cascata `camera_destinos`); versões de receita imutáveis também no ORM; o executor compartilhado carrega modelos sob demanda na thread de reconciliação da sombra.

**Testes:** suíte completa **837 passaram, 6 puladas** (as 6: navegador de vídeo que exige go2rtc real). Novos: `test_painel_receitas`, `test_painel_sombra`, `test_painel_dashboard`, `test_painel_dashboard_templates`, acréscimos a `test_motor_sombra_despacho` e à migração. Asserções antigas de menu/título atualizadas (`test_painel_auth_usuarios`, `test_painel_especialistas_navegador`, `test_painel_navegador_qt`). Não rodei a bateria de capacidade; **fumaça com modelo real não foi repetida** (mudanças não tocaram o caminho do modelo).

**Navegador:** Edge real (Playwright, headless), painel real com pacotes sintéticos, backend falso e porta de sombra alimentada com quadros sintéticos. Admin e usuário comum; desktop 1280 e estreito 390 (menu por teclado, Esc); 403 nas rotas e mutações de admin para o comum; sem rolagem horizontal; sem erro de JS (os 422/403 do console são os testes negativos; um 404 é o favicon). Vídeo go2rtc **não** foi exibido (sem go2rtc real: o player aparece vazio); overlay e SSE real do runtime não foram exercitados além do popup/ciência. Capturas locais (não versionadas): `%TEMP%\jutta_dashboard_shots\` (dados fictícios, sem imagens de pessoas).

**Pendências:** revisão visual com vídeo go2rtc e câmera real; dispositivo da sessão no Processamento (gerente de IA não o expõe); rotas de câmera existentes seguem com `usuario_atual` (não `exige_admin`), fora do escopo; gráfico sem filtro por tipo/severidade; etapas 02, 03 e 05 seguem abertas.

## Correções da revisão do Codex (`revisao_dashboard_05e.md`, 2026-10-05)

| Achado | Correção | Regressão (barreiras determinísticas) |
|---|---|---|
| 1. Reconciliação com snapshot antigo religava a sombra desligada | `GerenteSombra` ganhou uma **revisão** (`_rev`), incrementada por `desligar_agora` e por `PainelServicos.acordar()` (chamado **depois do commit** de qualquer mutação: receita, associação, sombra, câmera). O ciclo captura `rev0` antes de ler o banco e **só publica** (montar, erro, desmontar, remover) se `_rev == rev0`; senão abandona e refaz (até 3×; o ciclo periódico cobre o resto). Carga de modelo em andamento também é descartada se a revisão mudou | desligar antes de `_reconciliar_camera`; desligar durante a carga do modelo; trocar a receita durante a reconciliação (a última decisão prevalece); remover a câmera durante a reconciliação |
| 2. Resultado de montagem aposentada voltava ao feed | Cada montagem recebe uma **identidade**; o callback `destino` só publica no feed se `s.montagem == identidade vigente`. Os demais vão **apenas** ao rastro `.jsonl` com `aposentado: true` e são contados em `tardios`; não vão ao feed nem aos alertas | desligar com trabalho em andamento (feed vazio antes e depois de liberar); trocar de receita com trabalho antigo em andamento (feed só com a receita nova); reinício de sessão (geração obsoleta descartada) |
| 3. "Stream disponível" afirmava mais que a fonte | Renomeado para **Streams sincronizados**: é a configuração sincronizada no go2rtc, **não** vídeo chegando. O estado da API do go2rtc vem de uma **sondagem em cache** feita no laço de fundo (`ok`/`inacessivel`/`desatualizado` se mais velha que 3 ciclos/`desconhecido`); a renderização **não sonda**. O selo do card diz "stream configurado" ou "go2rtc inacessível". Vídeo só é confirmado ao tocar | indicador sem sondagem = `desconhecido`; API fora do ar com configuração `ok` = `inacessivel`; sondagem antiga = `desatualizado`; renderizar não chama `sondar`; selo do card |

As duas regressões de corrida foram verificadas por **mutação**: com a trava de revisão (ou a identidade de montagem) desativada, os testes correspondentes falham reproduzindo o defeito (sombra "ativa" depois de desligada; evento antigo no feed).

**Validação com vídeo/overlay reais: não rodou.** Não há go2rtc respondendo em `127.0.0.1:1984` nem executável disponível neste ambiente; os testes de navegador que dependem dele continuam puladas. Pendente: revisão com vídeo/overlay go2rtc real e usabilidade dos popups com vários alertas (em tela estreita, vários popups ocupam grande parte da tela; há "Fechar todos").

# Etapa 05f — Configuração simples por câmera e validação em dois níveis

**Atualização de fechamento da frente go2rtc:** a dependência foi instalada e a
cadeia MediaMTX → go2rtc → runtime/player foi validada, com overlay e popups
em tela estreita. O relato anterior de “executável ausente” abaixo é histórico.
Veja [`fechamento_integracao_go2rtc.md`](../fechamento_integracao_go2rtc.md) para
evidências e limites; o 05f não está automaticamente encerrado por esse ensaio.

Estado: **em andamento** (sublote da etapa 05; plano em [`plano_motor_plugins.md`](../plano_motor_plugins.md#05f--configuração-simples-por-câmera-e-validação-em-dois-níveis)). Apoia-se no motor/Cena ([03](03_motor_minimo.md), [03b](03b_cena_regras.md)) e no dashboard ([05e](05e_dashboard_sistema_mae.md)). Tudo em **sombra**, **sem commit**. As etapas 02, 03 e 05 **não** são encerradas por este sublote.

Origem: [`revisao_03b_proximo_lote.md`](../revisao_03b_proximo_lote.md). **Antes de 05f**, o achado temporal de 03b foi corrigido (indeterminado não é creditado retrospectivamente; ver 03b, "Pausa, não ponte").

## 1. Contratos mestre / crop (conferidos contra o código)

| Camada | Estrutura | Fica na mãe | Chega ao especialista |
|---|---|---|---|
| Cena | `Cena(camera_ref, geracao, quadro_id, recebido_em, largura, altura, deteccoes, quadro)`; `Deteccao(trilha_id, classe, bbox_xyxy, confianca, na_roi)` | tudo | nada diretamente |
| Observação | `Observacao(camera_ref, geracao, quadro_id, trilha_id, classe, bbox_xyxy, capturado_em, confianca)` | identidade, geração, instante de recepção | — |
| Recorte | `Recorte(imagem, origem_xy, bbox_quadro, bbox_no_recorte, quadro_wh)` (só translação; nunca redimensionado na mãe) | origem e transformação quadro↔crop, margens efetivas | a **imagem** |
| Pedido (executor) | `Pedido(pedido_id, recorte, camera_ref, geracao, trilha_id, quadro_id, fonte, altura_pessoa_px, restante_ms, parametros, config_hash)` | `pedido_id` codifica câmera/geração/quadro/trilha/especialista; orçamento = o da vinculação − idade desde a **recepção** | o executor devolve geração/trilha/quadro/versão, conferidos pela mãe (incoerente = descartado) |

* O envelope documental completo do manual **não** é transmitido ao especialista: só imagem + (`altura_pessoa_px`, `restante_ms`, `parametros`). ROI, bbox no quadro, transformação, identidade e geração **não saem** da mãe.
* A bbox pública é sempre em **pixels do quadro original**.

## 2. ROI, crop, execução e perfis (`src/jutta_ped/motor/ajustes.py`)

**ROI** — retângulo normalizado (frações do quadro, 0–1; convertido para pixels pela dimensão de cada Cena, portanto imune a mudança de resolução). Critério de pertencimento de uma detecção (`criterio`): `ponto_de_apoio` (padrão: centro da borda inferior da caixa dentro da ROI), `centro` ou `intersecao` (fração da **área da caixa** dentro da ROI ≥ `intersecao_min`, padrão 0,5). **Detecção fora da ROI não é descartada da Cena**: fica com `na_roi=False` para preservar o contexto espacial das relações. Só sujeitos `na_roi` recebem recorte/especialista; fora da ROI o papel é `indeterminado` (motivo `fora_da_roi`) — assim um candidato próximo mas fora da ROI impede um "nenhum próximo" falso (conservador), sem gastar especialista nele. Sem ROI = quadro inteiro.

**Crop** — `margem_h` (fração da **largura** da caixa, por lado), `margem_v` (fração da **altura**, por lado) e `minimo_px` (folga mínima em pixels por lado). Folga por lado = `max(minimo_px, int(fração × dimensão))` (truncamento, como no legado). `margem` única é **atalho compatível** (define h e v; caminho antigo `extrair_recorte(margem=…)` mantém o arredondamento e o comportamento). Precedência **sem somar** margens: campo explícito da câmera > perfil escolhido na câmera > `margem_recorte` da receita > padrão (0). Recorte sempre ajustado aos limites do quadro, com transformação preservada. O **tamanho mínimo do alvo** exigido pelo pacote (`altura_minima_px`) é outra coisa e continua valendo.

**Execução** — `orcamento_ms` (substitui o da vinculação; ≤ 60 000), `validade_max_ms` (só **aperta**: nunca acima da validade do atributo no manifesto nem da receita) e `intervalo_min_s` (cadência: Cenas mais próximas que isso, por `recebido_em`, não são enviadas à sombra). A câmera não ampliar validade nem forçar formato.

**Perfis** (dados, não condicionais; persistidos em código): `generico` (margem única 0,10) e `pediatria_legado` (**12 % horizontal, 8 % vertical, mínimo 8 px**, conferidos em `modulo/pediatria/crop_pipeline.crop_frame`: `pad_x = max(8, int(w·0.12))`, `pad_y = max(8, int(h·0.08))`). São **sugestões** que a câmera escolhe e pode sobrescrever; não são padrão universal e **não** entram no manifesto (protocolo 1 inalterado).

**Configuração efetiva** = perfil + ajustes explícitos + receita/especialista, normalizada e com hash (`hash_config`); cada alteração gera nova **versão** imutável (histórico por câmera).

## 3. Modo simples (regras do servidor)

* **Mestre:** `person` (rastreador atual). Outra classe mestre só com compatibilidade validada — recusada neste modo.
* **Um especialista por câmera** (`fonte` `visivel`, `classe_mestre` `person`) e **uma versão de receita**, que use **somente** esse especialista (vários atributos/relações continuam permitidos). O mesmo pacote pode servir várias câmeras, com ajustes distintos.
* **Sem especialista nem receita** é um estado válido: **diagnóstico só do mestre/crops** (nenhum veredito fictício).
* Receita existente com **mais de um especialista** é identificada como **fora do modo simples**: não é convertida nem apagada; câmeras já associadas **continuam como estão** (legado) até uma transição **explícita** (escolher uma receita simples ou remover a receita); novas associações a ela são **recusadas no servidor**, inclusive por importação/API.
* Salvar é **atômico**: falha preserva a configuração anterior; sucesso invalida atributos/estado temporal da câmera, descarta a montagem antiga (sem publicação tardia) e **não reinicia** a análise operacional.
* Admin no servidor, CSRF, auditoria. Habilitar sombra, ativar pacote e alertas operacionais continuam operações distintas.

## 4. Contrato de integração (backend ⇄ interface ⇄ motor)

**Motor** (`motor/ajustes.py`, `motor/diagnostico.py`, `PortaSombra`): `AjustesCamera`/`ajustes_de_dict`/`ajustes_para_dict`/`efetivos`/`PERFIS`; `selecionar_roi`; `receita_efetiva(definicao, ajustes, contratos)`; `DiagnosticoMestre` (último estado em memória, **nunca em disco**).
`DiagnosticoMestre.resumo()` → `{"ativo": bool, "recebido_ha_s": float|None, "quadro_id", "geracao", "largura", "altura", "roi": {...}|None, "ajustes": {…efetivos…}, "sujeitos": [{"trilha_id", "classe", "bbox_xyxy", "confianca", "na_roi": bool, "recorte": {"origem_xy", "tamanho": [w, h], "bbox_no_recorte", "folgas_px": {"esq", "dir", "topo", "base"}} | None, "motivo_sem_recorte": str|None}], "contadores": {"cenas", "vazias", "recusadas"}}`; `jpeg_quadro()` e `jpeg_recorte(trilha_id)` → `bytes | None`.

**Persistência** (migração 0004): tabela `camera_config_versoes(id, camera_id FK CASCADE, versao, especialista_id, receita_versao_id, ajustes_json, config_hash, criado_por, criado_em)` (imutável; vigente = maior `versao`); coluna `cameras.diagnostico_ativo` (padrão falso). `cameras.receita_versao_id` continua sendo a associação viva (mantida em sincronia, na mesma transação).

**Rotas** (`/painel`, admin, CSRF, auditoria):

| Rota | Efeito |
|---|---|
| `GET /cameras/{id}/analise` | página de configuração (`cameras/analise.html`) |
| `POST /cameras/{id}/analise` | salva (atômico). Campos: `especialista`, `receita_versao_id`, `perfil`, `roi_ativa`, `roi_x1`,`roi_y1`,`roi_x2`,`roi_y2` (**%**, 0–100), `roi_criterio`, `roi_intersecao_min` (%), `crop_margem`, `crop_margem_h`, `crop_margem_v` (**%**), `crop_minimo_px`, `exec_orcamento_ms`, `exec_validade_max_ms`, `exec_intervalo_min_s`. Sucesso: 303 + flash; inválido: **422** re-renderiza com `erro`, `problemas` e os valores digitados; nada é gravado |
| `POST /cameras/{id}/diagnostico` | `acao=ligar\|desligar` (independe da sombra e de receita) |
| `GET /cameras/{id}/diagnostico` | página de diagnóstico do mestre (`cameras/diagnostico.html`) |
| `GET /cameras/{id}/diagnostico/resumo` | fragmento `partials/diagnostico_mestre.html` (`diag` = `resumo()` + `estado`: `desligado`/`aguardando`/`ativo`) |
| `GET /cameras/{id}/diagnostico/quadro.jpg`, `.../recorte/{trilha}.jpg` | JPEG gerado na hora a partir da memória (`Cache-Control: no-store`; 404 sem dado) |
| `POST /cameras/{id}/receita` (existente) | passa a aplicar o **modo simples** no servidor |

**Contexto de `cameras/analise.html`:** `cam` (`id`, `nome`), `especialistas_compat` (`[{id, nome, versao, atributos:[{nome,tipo,categorias}]}]`), `receitas_compat` (`[{versao_id, label, especialista, valida, motivos}]`), `receitas_fora_modo` (`[{label, motivo}]`), `cfg` (valores do formulário, já em % onde aplicável: `especialista`, `receita_versao_id`, `perfil`, `roi_ativa`, `roi_x1…`, `roi_criterio`, `roi_intersecao_min`, `crop_margem`, `crop_margem_h`, `crop_margem_v`, `crop_minimo_px`, `exec_orcamento_ms`, `exec_validade_max_ms`, `exec_intervalo_min_s`), `efetivo` (`{versao, config_hash, ajustes, especialista, receita_label}` ou `None`), `perfis` (`[{nome, descricao, ajustes}]`), `legado` (`None` ou texto: câmera com receita fora do modo simples), `diagnostico_ativo`, `sombra_estado`, `flash`, `erro`, `problemas`.

## 5. Resultado

### Conferência de retomada em 05/10/2026

As alterações da chamada interrompida estavam gravadas: `diagnostico.js` consulta
`diag-tem-quadro` antes de pedir JPEG; a página não emite `src` antes da primeira
cena; as miniaturas usam caminho absoluto e `?q=<quadro_id>`. A retenção limitada
de cenas serve o recorte do quadro que o resumo mostrou. Não foi necessário
reescrever essas correções.

**Suíte completa nesta retomada:** 1.310 passaram, 9 pulados, 1 aviso de depreciação
Starlette/httpx, em 215,28 s. A coleta começou antes de criar o novo teste de
navegador abaixo; ele foi executado depois e passou nas duas modalidades.
Não atribuir os 9 pulados aos mesmos 6 do relato anterior sem conferir os motivos.

Foi acrescentado `tests/test_painel_diagnostico_navegador.py`, ensaio curto no Edge
headless a 390 × 844: dois ciclos sem cena não pedem JPEG; após a primeira cena,
quadro e miniaturas carregam sem respostas HTTP de erro, exceção JavaScript ou
rolagem horizontal. Passou com cena sintética e, separadamente, com
`JUTTA_DIAG_RTSP=rtsp://127.0.0.1:8554/001_bebe_colo_bebe_no_be_01`:
quadro real 1280 × 720, mestre OpenVINO CPU, **3 pessoas detectadas**, imagens
carregadas no navegador. Não foram gravadas imagens de pessoas.

**Limite da evidência nova:** servidor HTTP, RTSP, detector, diagnóstico e navegador
reais; o gerente de sessões e o go2rtc são simulados pela fixture isolada. O teste
injeta a Cena no diagnóstico; não reproduz o runtime completo nem a inferência
dos especialistas. A execução longa de três câmeras relatada anteriormente não
foi repetida. O log local `real05f_d.txt` registra eventos nas três câmeras, erros
de prazo e um 404; esse relato anterior não foi convertido em aprovação integral.

### Pendências de aceite

- Vídeo contínuo/overlay pela cadeia normal do **go2rtc da mãe**, sem desvio direto
  para MediaMTX, e popups em tela estreita com esse vídeo. O teste curto de JPEG
  não fecha essa pendência.
- Calibrar cadência/orçamento para a carga pretendida, sem ampliar prazos apenas
  para esconder `E_PRAZO_ESGOTADO`. Indeterminado por prazo é comportamento esperado.
- Resultados quase sempre negativos do modelo de bebê **não demonstram** que a
  causa é o crop. A incompatibilidade com a imagem de treino/uso é hipótese a
  verificar comparando entradas; não habilitar alvo `quadro` sem contrato e teste.
- Cena vazia diante de pessoa visível exige examinar entrada, limiar e configuração
  do mestre antes de atribuir a causa exclusivamente ao modelo.
- A etapa 02 e a migração operacional continuam abertas; nenhum resultado desta
  retomada constitui métrica de qualidade ou autoriza alertas reais do motor novo.

O sublote permanece **em andamento**, sem commit e sem arquivamento prematuro.

### Retomada após o checkpoint `f48369b` — go2rtc da mãe e importação SimulaterDSA

**Estado verificado em 05/10/2026 (consulta nova, não herdada do relato anterior):**
- O que **está** em execução: o simulador (MediaMTX + FFmpeg, pid único) em `127.0.0.1:8554` (RTSP) e `:9997` (API), com **10 caminhos prontos**.
- O que **não** está: **go2rtc**. Nada responde em `127.0.0.1:1984`; não há `go2rtc.exe` em `runtime/go2rtc`, no `PATH` nem no perfil do usuário (`runtime/go2rtc` só tem `streams.yaml`). A dependência que falta é, precisamente, o **executável do go2rtc**. O operador indicou que o RTSP em `8554` é o que está disponível para consumo; não foi instalado nem baixado nada.

**Defeito de integração corrigido (porta):** o YAML base do go2rtc fixava RTSP em `127.0.0.1:8554`, a mesma porta do MediaMTX. Agora (a) o YAML base **gerado na primeira execução** usa as portas das URLs de Configurações (`go2rtc_api_url`, `go2rtc_rtsp_url`; padrões idênticos aos anteriores); (b) antes de subir o go2rtc o painel **recusa** com mensagem precisa se a porta RTSP já está em uso (estado `erro`, "mude a Base RTSP para outra porta, ex.: `rtsp://127.0.0.1:8556`"), em vez de falhar em silêncio; (c) a página **Processamento** passou a mostrar o estado do processo (`iniciado pelo painel` / `externo` / `executável não encontrado` / `erro ao iniciar`) e o motivo. Convivência na mesma máquina: simulador em `8554`, go2rtc em `8556` (`go2rtc_rtsp_url = rtsp://127.0.0.1:8556`). Não se alterou nem parou o simulador. Testes: `test_painel_cameras_go2rtc.py` (YAML por porta, recusa por porta ocupada, detecção real de porta) e `test_painel_dashboard_templates.py`.

**Importação SimulaterDSA validada pelo painel em banco isolado, YAML real (`perinatal.yaml`), go2rtc real ausente:** 10 câmeras cadastradas (RTSP genérico, IA desligada, URL cifrada), reimportação idempotente (continuam 10), sincronização `pendente` exibida como tal, indicador "Streams sincronizados = 0" com nota "Estado do go2rtc ainda não verificado… não comprova vídeo". Sem erro de JavaScript (um 404 de favicon). Isto prova o **cadastro**, não vídeo, inferência nem a cadeia pelo go2rtc.

**Suíte completa após estas mudanças:** 1315 passaram, 9 puladas, 1 aviso (216 s). Não repetida a bateria de capacidade nem os roteiros longos.

**Continua bloqueado por dependência:** a cadeia **MediaMTX → go2rtc da mãe → análise/player** e, com ela, vídeo contínuo/overlay e popups em tela estreita sobre vídeo real. Para destravar: colocar `go2rtc.exe` em `runtime/go2rtc` (ou informar o caminho em Configurações), ajustar a Base RTSP para uma porta livre (`8556`) e importar o cenário pelo painel; nada mais é necessário no código.

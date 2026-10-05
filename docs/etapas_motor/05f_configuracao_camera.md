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

## Validação funcional do motor sobre RTSP real e três câmeras (05/10/2026, sem commit)

Escopo: evidência de execução correta do caminho **câmera → mestre → crops → especialista empacotado → atributos → receita → evento de sombra** com configurações independentes por câmera. **Não** mede qualidade clínica, **não** migra alertas operacionais, **não** encerra 02, 03 ou 05.

### O que foi executado

* **Inventário (verificado no código e no registro do Instalador):** pacote real = `br.jutta.papel-pessoa` 1.0.0 (detector criança/adulto v6 OpenVINO empacotado por `tools/pacote_papel.py`; entrada: recorte de `person`, fonte visível, altura mínima 24 px; atributos numéricos `confianca_crianca`/`confianca_adulto`). Outros pacotes reais compatíveis com recorte de pessoa **não existem**: o `Bebe-no-colo.pt` não foi usado (adequação a crops não estabelecida). Pacote de **conformidade** = `br.jutta.conformidade-porte` 1.0.0, declarativo, backend **determinístico** (classifica pela altura do recorte; sem pesos; só a câmera C). Situações distintas no ensaio: *pacote instalado e ativo* (Instalador) ≠ *receita associada à câmera* ≠ *sombra ligada* (evento `modo=sombra`, `alerta_real=false`) ≠ *alerta operacional* (não existe para o motor novo; o ensaio contou 0 no banco). Já rodam pelo executor genérico: mestre → Cena → recortes → executor → receita contrato 1 e 2. Ainda dependem do caminho pediátrico antigo: o alerta operacional, o classificador do pipeline de recortes (`detect_and_classify`) e o início da **sessão** operacional (`PediatriaService.start_session` sempre carrega o classificador).
* **Roteiro** `tools/validar_motor_especialistas.py` (ponto de entrada separado do painel): o painel real roda em processo (`mount_panel` + `TestClient`, sem servidor HTTP, go2rtc nem navegador) e importa o cenário SimulaterDSA, cria receitas, salva a configuração por câmera e liga a sombra **pelas rotas**. `Instalador`, `Executor`, `GerenteSombra`/`PortaSombra`, `MotorSombra` e `PediatriaSessionManager` (sessão headless) são os de produção. A fonte é o RTSP do simulador lido **direto** (`rtsp://127.0.0.1:8554/<nome>`). O roteiro não copia o motor; o `Executor` é decorado só para medir. Não prova o fluxo pelo navegador nem a cadeia go2rtc (esta já está em `fechamento_integracao_go2rtc.md`).
* **Testes determinísticos** `tests/test_motor_validacao_multicamera.py` (11): três câmeras pelo painel real (A e B = mesmo pacote com crops diferentes; C = outro pacote), backend de inferência sintético, corridas por barreiras e relógio de captura explícito.

### Comando de reprodução

```
# requer o simulador SimulaterDSA já no ar (não é iniciado nem alterado) e o modelo criança/adulto local (src/models/..._v6_jutta_openvino_model)
.venv\Scripts\python.exe tools\validar_motor_especialistas.py --fase tudo --duracao 60 --quadros 40 --dispositivo cpu
.venv\Scripts\python.exe tools\validar_motor_especialistas.py --fase especialistas --cameras A --duracao 40
.venv\Scripts\python.exe -m pytest tests\test_motor_validacao_multicamera.py tests\test_backend_ultralytics_dispositivo.py -q
```

Saída em `runtime/validacao_motor_<data>/resultado.json` (ignorado pelo Git; o roteiro não grava imagens de pessoas). Fontes: A=`002_normal_01`, B=`001_bebe_colo_bebe_no_be_01`, C=`003_bebecolo_01` (cenário `perinatal.yaml`). As sessões rodaram com `--dispositivo cpu`; o Executor escolheu `intel:gpu` para o pacote real e `cpu` para o determinístico (campo `dispositivo_executor` do JSON).

### Defeitos encontrados e corrigidos

1. **Especialista real não carregava em CPU no mesmo processo do detector (novo; bloqueava o critério).** Depois que o detector de pessoas rodava com `device="cpu"`, o Ultralytics ocultava a GPU e `modelo.names` de um modelo OpenVINO montava o preditor com `device=None`, escolhia CUDA e falhava com `Invalid device id`; o executor reportava "sem versão ativa"/"modelo não carregou". Corrigido em `especialistas/backend_ultralytics.py` (o dispositivo escolhido pelo executor é definido antes de ler as classes).
2. **Mesmo defeito no caminho operacional** (`modulo/pediatria/detector_mvp.py`, leitura das classes ao iniciar uma sessão): reproduzido isoladamente (inferência em CPU e depois `PediatricsDetectorMvpRunner` OpenVINO falhava). Correção mínima: a leitura das classes usa CPU de forma temporária; o dispositivo de inferência não muda. Em máquinas só-CPU, uma câmera iniciada **depois** da primeira inferência de outra podia não subir. Testes: `tests/test_backend_ultralytics_dispositivo.py` (3).
3. **Feed da sombra mantinha o último evento da configuração anterior** após trocar ROI/crop/receita da câmera (`sombra_service._montar`). O estado (atributos/regras) já era invalidado; o feed passou a mostrar só a configuração vigente (o histórico fica no rastro `.jsonl`, como já ocorre na troca de geração).
4. Do roteiro (não do produto): a janela de medição sem aquecimento explícito misturava compilação do dispositivo e subida das sessões com o regime estável; passou a haver aquecimento explícito e janela estável separados.

### Matriz de critérios

| Critério | Evidência | Resultado | Pendência |
|---|---|---|---|
| Especialista com modelo real pelo ciclo normal de pacote | Pacote `papel-pessoa` instalado e ativado pelo `Instalador`, executado em A e B sobre RTSP (438 e 159 recortes inferidos na janela estável; `intel:gpu`) | **Comprovado** (execução; não é qualidade) | Segundo modelo real não pediátrico: **não é bloqueio** (o determinístico foi aceito para conformidade e isolamento) |
| Mestre/crops sem classificação pediátrica | Fase 1: `PersonCropSpecialistPipeline(specialist_model=None)` sobre A e B, 6 configurações × 40 quadros; **0** chamadas ao classificador (sentinela em `_classify_crop`) | **Comprovado** | A **sessão** operacional ainda carrega o classificador ao iniciar (não alterado, para preservar o caminho operacional) |
| Identidade câmera/sessão/trilha, bbox e transformação quadro–crop | Por quadro: `quadro_id` crescente, trilhas únicas, bbox no quadro, `origem + bbox_no_recorte = bbox`, pixels do crop = recorte do quadro | **Comprovado** (vídeos reais com várias pessoas) | — |
| ROI, margens h/v, mínimo em px, bordas | Folga por lado = `min(max(mínimo, int(fração·dim)), distância à borda)` conferida em todo recorte; bordas e mínimo dominante exercitados (contagens no JSON) | **Comprovado** | ROI por `intersecao`/`centro` só em teste unitário |
| Cena vazia / alvo fora da ROI não vira negativo inventado | Motor real (quadro sintético): cena vazia sem sujeito; alvo fora da ROI → papéis `indeterminado` (`fora_da_roi`) e **0** chamadas ao especialista; nos vídeos, sujeitos fora da ROI ficam sem recorte com motivo; teste multicâmera: ROI exclui o alvo → 0 evento | **Comprovado** | — |
| Três câmeras com configuração independente (A, B mesmo pacote com ajustes distintos; C outro pacote) | Salvas pelas rotas do painel; hashes de configuração distintos; recortes distintos por câmera (teste); sombra ligada nas três; rodada real com as três simultâneas | **Comprovado** (C é **determinístico**) | Operação pela interface/navegador não exercitada |
| Isolamento de atributos, estados e resultados tardios | Testes: alterar crop/ROI/receita de A zera só A (motor, montagem, cache e feed de B e C intactos); resultado em andamento da configuração anterior vira `aposentado` no rastro e **não** entra no feed; desligar/religar cria motor novo, cache e feed vazios | **Comprovado** (sintético, determinístico) | — |
| Erro, expiração e prazo → indeterminado | Testes: erro do executor, prazo esgotado antes de enviar (executor nem chamado), atributo expirado (validade apertada pela câmera), resultado devolvido depois do orçamento; nenhum vira `nao`. Na rodada real há `atrasado`/`E_PRAZO_ESGOTADO` ocasionais | **Comprovado** (sintético) | — |
| Eventos de sombra fora dos alertas operacionais | Todos os eventos `modo=sombra`, `alerta_real=false`; 0 `EventoPediatria` no banco; rastro próprio | **Comprovado** | Os popups da sessão (3, 3 e 1 na rodada) vêm do pipeline operacional antigo, não da sombra |
| Sem regressão no caminho operacional | Suíte completa (abaixo); `process_frame` seguiu rodando nas sessões, 0 erros de sombra | **Comprovado nos testes**; a correção 2 altera a leitura de classes | — |
| Roteiro reproduzível; real × determinístico separados | Comando acima; cada câmera traz `natureza` no JSON | **Comprovado** | — |
| Qualidade clínica / equivalência da migração | — | **Não afirmada** | Etapas 02 e 03 |

### Medições (rodada `--fase tudo --duracao 60`, três câmeras simultâneas)

> **Ressalva da revisão de 05/10/2026 ([`revisao_validacao_multicamera.md`](../revisao_validacao_multicamera.md)).** Esta rodada permanece como **evidência funcional** (os crops passaram pelos pacotes, o isolamento foi demonstrado). Os números abaixo têm **limitações quantitativas** e **não servem para decidir capacidade nem para justificar a etapa 04**: (1) a janela só tinha limite inferior (resultados concluídos durante a parada das sessões entraram nas estatísticas); (2) o início da janela não esperava o aquecimento dos modelos próprios das sessões; (3) contadores acumulados desde a partida foram apresentados ao lado de métricas da janela; (4) o dispositivo foi registrado pelo **pedido**, não pelo executado: onde esta seção diz "sessões em CPU" e `intel:gpu`, leia "pedido" — a verificação posterior mostrou que o hardware efetivo pode divergir do pedido (ver "Correção da instrumentação"). A rodada não pode ser rotulada como CPU nem como GPU Intel comprovada.

Definições. **Janela estável**: do instante em que as três sessões subiram até o fim da espera de 60 s (exclui carga de modelos, compilação e o aquecimento explícito de 8 chamadas por pacote; o primeiro recorte real custou ~746 ms e os seguintes ~33–46 ms). **Idade na avaliação**: `avaliado_em − capturado_em` (captura = recepção local do quadro; inclui a espera na fila da câmera). **Especialista (ms)**: tempo de parede de `Executor.executar` (um recorte por pessoa, lote 1), exceto a primeira chamada do pacote. **Estouro**: erro `E_PRAZO_ESGOTADO` ou `atrasado` listado em um evento. Orçamento mantido em 500 ms desde a recepção.

| | A (real, `intel:gpu`) | B (real, `intel:gpu`) | C (determinístico) |
|---|---|---|---|
| Fonte | 002_normal_01 | 001_bebe_colo_… | 003_bebecolo_01 |
| Configuração | margens 12/8 %, mín. 8, cadência ≥ 0,4 s | 25/15 %, mín. 16, cadência ≥ 1 s, ROI (quadro todo) | 5/5 %, mín. 8, cadência ≥ 0,4 s |
| Pacote / receita | papel-pessoa 1.0.0 / contrato 2 v1 | idem | conformidade-porte 1.0.0 / contrato 1 v1 |
| Quadros lidos / descartados pela captura / processados pela sessão | 2186 / 1853 / 332 | 1043 / 755 / 287 | 913 / 699 / 213 |
| Enfileirados / processados / descartados no despacho | 147 / 147 / 0 | 58 / 58 / 0 | 97 / 97 / 0 |
| Eventos na janela (eventos/s) | 128 (2,13) | 54 (0,90) | 184 (3,07; um por pessoa) |
| Idade na avaliação p50 / p95 / máx (ms) | 156 / 265 / 563 | 156 / 250 / 718 | 47 / 125 / 2375 |
| Especialista p50 / p95 / máx (ms) | 116,6 / 221,7 / 529,5 | 108,0 / 194,7 / 693,5 | 0,8 / 1,8 / 2,8 |
| Inferência reportada p50 / p95 (ms por recorte) | 29,9 / 55,0 | 30,9 / 66,7 | ≈ 0 |
| Estouros de prazo | 2 | 3 | 5 |

Com a janela contaminada por aquecimento e subida das sessões (execuções anteriores do mesmo roteiro, **não preservadas**), apareciam dezenas de estouros por câmera. O orçamento de 500 ms **não** foi ampliado: a diferença veio de carga/compilação, não de falta de capacidade em regime estável. Uma câmera sozinha, com o mesmo aquecimento, deu p50 ≈ 74 ms e p95 ≈ 129 ms de especialista (execução intermediária não preservada).

### Limitações

* Um vídeo em laço por câmera e nenhum rótulo: nada aqui mede acerto de modelo; o que cada pessoa "é" não foi avaliado.
* Três câmeras na **mesma CPU/GPU Intel** de uma máquina de desenvolvimento, com as sessões operacionais completas (detector + classificador) ao lado; não é benchmark nem projeção de carga. Executor compartilhado e serial: justiça e filas são da etapa 04.
* A câmera C é **determinística de propósito**; seus tempos mostram o custo do caminho (porta, fila, receita), não de um modelo.
* Fonte lida direto do MediaMTX; painel em processo, sem navegador; a configuração pela interface não foi exercitada neste lote.
* ROI testada com critério `ponto_de_apoio` no real; `centro`/`intersecao` só em testes unitários.
* Corridas, expiração e prazo são provadas com backend sintético, não com o modelo real.

### Próxima etapa recomendada

1. **Etapa 02:** confirmação humana/procedência (a referência de qualidade segue bloqueada); só então comparar, em sombra, o especialista real com a bateria de referência (migração pediátrica, etapa 03).
2. Repetir a rodada real com cadência declarada por câmera e decidir com dados se `intervalo_min_s` por câmera basta ou se a etapa 04 (executor compartilhado/fila) é necessária. Proposta fundamentada: partir de `intervalo_min_s ≥ p95 do especialista × nº de pessoas` antes de mexer no orçamento.
3. Exercitar a mesma configuração pelo navegador com o go2rtc da mãe (segundo nível do plano 05f).

### Suíte completa ao final deste lote

**1.329 passaram, 9 pulados**, 1 aviso de depreciação Starlette/httpx, em 265,6 s (`.venv`, Python 3.12). Eram 1.315 + 9 antes; os 14 testes novos são os 11 multicâmera e os 3 do dispositivo. Pulados, com o motivo reportado pelo pytest: 3 de fumaça dependem de vídeo/modelo locais ausentes (`test_bateria_referencia`, `test_especialistas_executor`, `test_motor_sombra`); 6 de navegador (`test_painel_navegador_edge` ×4 e `test_painel_navegador_qt` ×2) exigem o stream `entrada_social_vis` no go2rtc real, ausente. Nenhum alias foi inventado para fazê-los rodar. Rodar os testes com o `python` do sistema (3.14, sem `alembic`) falha na coleta; use o `.venv`.

### Correção da instrumentação e verificação curta (revisão de 05/10/2026)

Corrigido em `tools/validar_motor_especialistas.py`, com as funções de medição extraídas para `tools/medicao_janela.py` (puras e testadas em `tests/test_medicao_janela.py`, 8 testes):

1. **Janela com dois limites e população por conclusão.** `[t_ini, t_fim)` no relógio monotônico; evento pertence à janela por `avaliado_em`, chamada ao executor por `t + ms`. Itens concluídos depois de `t_fim` (durante a parada sequencial das sessões) vão para `drenagem`, contados à parte e fora das estatísticas; os anteriores são `antes`. O JSON registra `t_ini`, `t_fim`, o fim da parada e as três contagens por câmera.
2. **Prontidão observável antes da janela.** Cada sessão precisa ter processado ≥ 30 quadros (detector + classificador da própria sessão) e cada câmera concluído ≥ 3 avaliações na sombra, com prazo de 120 s e **falha explícita** (as sessões são paradas e o roteiro aborta). Isto comprova atividade, **não** estado térmico/estacionário do hardware.
3. **Deltas, não totais.** Snapshots de captura, sessão, porta, despacho e motor no início e no fim da janela; taxas por segundo saem só dos deltas. Os totais acumulados continuam no JSON, rotulados "NAO usar para taxas da janela". **Cenas ≠ eventos:** `cenas_avaliadas` (contrato 2, um evento por Cena) e `avaliacoes_por_sujeito` (contrato 1, um por pessoa) são medidas separadas e não comparáveis entre contratos.
4. **Dispositivo efetivo.** Depois de inferir, lê-se `EXECUTION_DEVICES` do OpenVINO compilado de cada modelo (especialistas do executor, detector de pessoas e classificador de **cada sessão**), junto ao pedido, ao `device` do backend e a um sinal `divergente_do_solicitado`; sem inferência prévia o campo diz "indisponível" em vez de supor.

**Verificação curta** (`--fase especialistas --duracao 25 --dispositivo cpu`; uma rodada de 25 s só para conferir a instrumentação, **não** é medição de capacidade): a prontidão passou; janela de 25,02 s; população por conclusão registrada (p. ex. C: 49 antes / 113 dentro / 1 depois nos eventos); taxas por delta (A ≈ 2,1 cenas/s avaliadas, B ≈ 0,8, C ≈ 4,5 avaliações por sujeito/s com 1,8 cenas aceitas/s); estouros 0, 0 e 3,5 %; idade p95 281, 344 e 344 ms. **Achado sobre hardware:** apesar de as sessões pedirem `cpu`, o OpenVINO compilou o detector de pessoas e o classificador de **todas** as sessões em `GPU.0`, e o especialista real (pedido `intel:gpu`) também em `GPU.0`; só o backend determinístico (C) não usa modelo. Ou seja, **`force_cpu`/`device="cpu"` não garantiu CPU neste ambiente** (dispositivos disponíveis: `CPU`, `GPU.0`, `GPU.1`). A seleção de dispositivo via Ultralytics/OpenVINO não é controlada pelo pedido e **não foi alterada neste lote**; passa a ser registrada em toda rodada.

**Hipótese explícita** da próxima rodada (registrada no JSON como `hipotese`, com `veredito_da_hipotese` calculado). **A meta de 5 % e de 500 ms é uma hipótese proposta pelo ensaio, não um critério de aceite aprovado:** *com as cadências declaradas (A = 0,4 s; B = 1,0 s; C = 0,4 s), três câmeras simultâneas e janela fechada após a prontidão, a fração de avaliações com estouro de prazo é ≤ 5 % e a idade p95 da observação < 500 ms em cada câmera; refutada se qualquer câmera ultrapassar um dos dois.* O orçamento de 500 ms continua intocado. Uma rodada não decide capacidade nem a etapa 04; repetir só com hardware fixado e declarado.

### Critérios restantes do 05f (situação em 05/10/2026)

| Critério do plano | Situação | O que falta |
|---|---|---|
| Configuração básica inteiramente operável pelo painel | Rotas, validações, CSRF, auditoria, testes de servidor e **operação ponta a ponta no navegador** (ver "Fechamento do 05f") | — |
| Restrições aplicadas no backend | **Cumprido** (modo simples, importação/API) | — |
| Receitas anteriores preservadas | **Cumprido** (legado identificado, não convertido) | — |
| Teste do mestre/crop independente do especialista | **Cumprido** com RTSP real | — |
| Especialista real pelo caminho normal | **Cumprido** em A/B (RTSP direto) e, depois, pela orquestração do painel sobre o go2rtc (ver "Fechamento do 05f") | — (o pacote determinístico foi aceito para conformidade e isolamento; segundo modelo real **não** é bloqueio) |
| Isolamento entre câmeras e configurações | **Cumprido** (testes determinísticos + rodada real) | — |
| Ausência de eventos antigos após alteração | **Cumprido** (feed corrigido neste lote) | — |
| Vídeo/overlay/crops conferidos em navegador, sem versionar imagens | **Cumprido**: vídeo/overlay no fechamento go2rtc; crops conferidos na página de diagnóstico (ver "Fechamento do 05f") | — |
| Medição confiável para calibrar cadência | Instrumentação corrigida e CPU explícita honrada; **nenhuma rodada de capacidade válida** | **Não é critério do 05f** (movida para calibração/etapa 04); rodada futura com hardware fixado e declarado |

(Situação anterior ao fechamento abaixo.) A etapa 02 (referência de qualidade pediátrica) **não** bloqueia nenhum critério acima nem o desenvolvimento do sistema-mãe genérico e de especialistas externos; ela só condiciona avaliar qualidade/equivalência da migração pediátrica (etapa 03).

### O que falta para desenvolver um especialista em workspace separado (só contrato público)

O que **já existe** e basta para começar: manual (`docs/manual_especialista.md`), molde (`especialistas_molde/`), schema (`manifest.v1.schema.json`), validador estático, hash canônico, `executar` e as obrigações do host (seções 4, 5, 14 e 15). Lacunas verificadas no código, em ordem de impacto:

1. **Ferramental não é distribuível.** Validador/hash/executar só rodam como `python -m src.jutta_ped.especialistas` dentro do repositório da mãe (importam `src.jutta_ped...`; dependem de `jsonschema` e `yaml`). Falta um pacote/zipapp versionado só com validador, schema e hash, para o workspace separado não depender de clonar a mãe.
2. **As amostras não são reproduzidas.** O validador declara que `amostras/*.esperado.json` não são conferidas (`validador.py`, lista de não verificados). Falta um comando que execute o pacote sobre as amostras e compare com os esperados.
3. **`executar` exige instalar e ativar na mãe.** O autor não consegue rodar um pacote em pasta/zip, com seus próprios recortes, sem instalá-lo no registro. Falta "executar sem instalar", reutilizando o mesmo `Executor` (contrato único, sem caminho paralelo).
4. **Conferência real de classes só ao carregar.** Para `.pt`/`.onnx` o validador não inspeciona classes (questão aberta 8 do manual); o erro só aparece ao executar. O comando do item 3 fecharia isso.
5. **Dispositivo executado não é reportado.** `dispositivos` do manifesto é preferência; o executor não informa o hardware efetivo (achado acima). O autor não consegue medir `custo` honestamente. Falta o executor devolver o dispositivo efetivo por modelo.
6. **Crops representativos.** O autor precisa saber que recortes a mãe enviará (margens por câmera, altura mínima, sem redimensionar). Falta um guia curto, apoiado no diagnóstico do mestre (`recorte/{trilha}.jpg`), para obter amostras reais e uma nota de que a mãe nunca envia o quadro inteiro (alvo `quadro` não habilitado).
7. **Teste de receita fora do repositório.** Receitas contrato 2 só têm um exemplo; o autor não precisa escrevê-las, mas precisa de um caminho documentado para ver os atributos do seu pacote numa receita mínima na mãe (importação por JSON) — hoje só o exemplo pediátrico.
8. **Aceite da etapa 06 ainda não executado** (itens 1–8 foram transferidos para [`06_workspace_especialistas.md`](06_workspace_especialistas.md)). O critério é um pacote **produzido fora** do repositório, validado e instalado na mãe. Nenhum pacote não pediátrico com modelo real foi produzido; é o teste que de fato prova o contrato público (autoria/assinatura segue questão aberta 6, relevante só para terceiros).

Fora do que falta: nós, treinamento, executor compartilhado e térmico, que continuam fora de escopo e **não** foram iniciados.

## Contrato de dispositivo e fechamento do 05f (05/10/2026, sem commit)

### Defeito corrigido: dispositivo explícito não era honrado

**Causa.** O Ultralytics só interpreta `intel:<NOME>` como dispositivo OpenVINO; qualquer outro valor — inclusive `device="cpu"` — vira `AUTO`, e o OpenVINO compilou os modelos em `GPU.0`. Um pedido explícito de CPU (`force_cpu`, `requested_device=cpu`, manifesto) rodava na GPU integrada; `intel:gpu` sem o nome exato também caía em `AUTO` com um aviso. O `placement.py` prometia "OpenVINO → CPU", o que não se cumpria. Descoberto pela leitura do `EXECUTION_DEVICES` (ver revisão); confirmado no código do backend OpenVINO do Ultralytics instalado.

**Contrato novo** (`src/jutta_ped/dispositivo.py`, módulo folha):
* pedido **explícito** → esse dispositivo ou `DispositivoIndisponivel` com explicação (nomes que o OpenVINO oferece); nada é trocado em silêncio. `cpu` → `intel:CPU`; `intel:gpu`/`gpu` → a primeira `GPU.N` (ou `intel:gpu.N` exata); `auto`/vazio → CPU (como o `placement` já documentava); CUDA para modelo OpenVINO é recusada;
* **fallback** só com `permitir_fallback=True` e sempre devolvido (`Resolucao.fallback`, `ModeloOpenVinoEstrito.fallbacks`);
* o dispositivo **efetivo** (`EXECUTION_DEVICES` do compilado) é lido e conferido na primeira inferência de cada dispositivo; divergência = `DispositivoIndisponivel` ("pedido X mas executou em Y");
* **sessões/pipeline:** os modelos OpenVINO do detector de pessoas e do classificador passam por `ModeloOpenVinoEstrito` (um proxy fino que traduz `device=` em `track`/`predict` e delega o resto);
* **especialistas (executor):** o backend resolve estritamente; o executor ganhou `dispositivos_permitidos` (restrição do host, ex.: `("cpu",)`, sem fallback; pedir um dispositivo que o pacote não declara é recusado com explicação) e `info()` passou a devolver `dispositivos_efetivos` e `fallbacks` por modelo (lista vazia = nenhum fallback; o fallback "declarado no manifesto" — ex.: `cuda` → `cpu` — é a ordem do próprio `dispositivos[]` e agora aparece sempre).

**Efeitos a conhecer.** (1) Nesta máquina as sessões que pediam `cpu` rodavam, na verdade, na `GPU.0` (iGPU); agora rodam em CPU, como pedido: **a capacidade medida antes não vale para o novo comportamento** e a iGPU deixa de ser usada implicitamente (usá-la exige `intel:gpu` explícito). (2) `intel:gpu` sem GPU passa a falhar com explicação na primeira inferência da sessão (não há verificação antecipada na partida; o fallback para CPU só existe onde o `placement` já o registra, que é o de CUDA). (3) A UI Qt (`demo_viewer`) não foi auditada neste lote. (4) Preservadas as regressões de ambiente com vários modelos: a leitura de classes não escolhe CUDA depois de outra inferência em CPU (`tests/test_backend_ultralytics_dispositivo.py`) e modelos diferentes no mesmo processo resolvem cada um o próprio dispositivo (`tests/test_dispositivo.py`, 13 testes).

**Verificação com hardware real** (`tools/validar_motor_especialistas.py --dispositivo cpu`, 25 s, só para conferir o contrato; e o roteiro do painel abaixo): detector de pessoas, classificador de cada sessão e especialista real compilaram em `["CPU"]`, `divergente_do_solicitado=false`, sem fallback.

### Fechamento do 05f pelo painel (`tools/validar_painel_05f.py`)

Servidor HTTP real, **go2rtc da mãe** (RTSP 8556; o simulador em 8554 não foi tocado), `IaOrquestrador` e sombra pelos **laços de produção** (`background=True`, sem `reconcile()` manual), Edge headless; banco e pasta isolados em `runtime/validacao_painel_05f_*` (ignorado pelo Git; as capturas de tela contêm quadros de vídeo e não são versionadas). Comando: `.venv\Scripts\python.exe tools\validar_painel_05f.py`. Rodada aprovada: `runtime/validacao_painel_05f_20261005_155747/resultado.json`.

Executado **pelo navegador**: importar o cenário SimulaterDSA; importar as duas receitas (JSON do contrato público); por câmera, ligar a IA no formulário normal, escolher especialista e receita **compatíveis** na tela de análise, preencher crop (h/v/mínimo) e intervalo, salvar; ligar a sombra na tela de Receitas; ligar o diagnóstico do mestre. Câmeras: A=`002_normal_01` e B=`001_bebe_colo_…` (pacote real, ajustes distintos) e C=`003_bebecolo_01` (pacote **determinístico** de conformidade). Resultados:

* **Orquestração:** as três câmeras ficaram `rodando` com sombra `ativa`, lendo `rtsp://127.0.0.1:8556/cam_<id>` (restream do go2rtc); 1 consumidor por câmera no go2rtc.
* **Crops na interface:** a página de diagnóstico mostra quadro, caixas, folgas e a miniatura de cada recorte. Para cada linha foi conferido, a partir dos números que a própria página exibe, que (a) as folgas por lado = `min(max(mínimo, int(fração·dim)), distância até a borda)` (tolerância de 1 px do arredondamento exibido) e (b) a imagem carregada tem as dimensões `L×A` anunciadas. 2, 3 e 4 recortes conferidos em A, B e C, **0 divergências**.
* **Salvar A pelo formulário (horizontal 12 → 30 %):** só o `config_hash` de A mudou; B e C mantiveram montagem, assinatura e motor; a **sessão operacional de A não reiniciou**; o feed de A passou a ter um único hash (só a configuração vigente); os recortes de A refletem 30 % (3 conferidos, 0 divergências) e B segue igual.
* **Sombra × operacional:** 72 eventos no feed, 0 com `alerta_real`, 0 `EventoPediatria` no banco; nenhum erro de JavaScript nem resposta HTTP ≥ 400 durante toda a navegação.
* **Dispositivo:** CPU efetiva em todos os modelos reais (sessões e especialista), sem fallback; o pacote de conformidade não usa modelo.

**Limites:** um vídeo em laço por câmera; não há medição de capacidade nem de qualidade; o popup móvel e o overlay sobre vídeo já foram fechados no fechamento go2rtc e não foram repetidos; a navegação foi headless em desktop (1280 px).

### Critérios do plano 05f (atendidos em 05/10/2026)

| Critério | Evidência | Resultado |
|---|---|---|
| Configuração básica inteiramente operável pelo painel | Roteiro acima, só pela interface | Comprovado |
| Restrições aplicadas no backend | `test_painel_camera_config.py` (modo simples, importação/API) | Comprovado |
| Receitas anteriores preservadas | Mesmos testes (legado identificado, sem conversão) | Comprovado |
| Teste do mestre/crop independente do especialista | Fase 1 com RTSP real, 0 chamadas ao classificador | Comprovado |
| Especialista real pelo caminho normal | Pacote instalado/ativado, executado pela orquestração do painel sobre o go2rtc | Comprovado |
| Isolamento entre câmeras e configurações | Testes determinísticos + salvar A ao vivo | Comprovado |
| Ausência de eventos antigos após alteração | Feed de A só com o hash vigente; testes de corrida | Comprovado |
| Vídeo/overlay/crops conferidos em navegador, sem versionar imagens | Fechamento go2rtc (vídeo/overlay) + conferência dos crops acima; capturas só locais | Comprovado |

O sublote **05f está com seus critérios atendidos**; fica sem commit para revisão e **não** foi arquivado. Isso **não** conclui a etapa 05, nem 02, nem 03. Fora do 05f e ainda em aberto: calibração de cadência/capacidade (hipótese de 5 %/500 ms por câmera, **proposta, não aprovada**), qualidade/equivalência da migração pediátrica e o fluxo de especialista externo (etapa 06).

**Suíte completa ao final (contrato de dispositivo + fechamento do 05f):** 1.350 passaram, 9 pulados (os mesmos 9 de antes: 3 por vídeo/modelo locais ausentes e 6 por exigirem o stream `entrada_social_vis` no go2rtc real), 1 aviso de depreciação, 200,9 s. Novos: `tests/test_dispositivo.py` (13).

**Revisão do contrato de dispositivo ([`revisao_dispositivo_etapa06.md`](../revisao_dispositivo_etapa06.md)), duas brechas corrigidas, sem commit:** (1) a conferência do dispositivo efetivo passou a comparar **identidades completas** — o conjunto de `EXECUTION_DEVICES`, normalizado só quanto a parênteses/espaços/caixa, tem de ser exatamente o dispositivo resolvido (`["CPU", "GPU.0"]` não vale para CPU; `GPU.01` não é `GPU.0`; `HETERO:CPU` não é `CPU`); (2) **telemetria ausente nunca marca o dispositivo como conferido**: no modo estrito é `DispositivoIndisponivel` explícito depois da inferência, tanto no proxy das sessões (estrito quando o fallback não é permitido) quanto no backend do executor (sempre estrito; o executor devolve erro por pedido, sem atributos). Com fallback permitido, o proxy segue sem marcar, conta `nao_conferidos` e reconfere na chamada seguinte. Testes: `tests/test_dispositivo.py` (24, 11 novos); conjunto pertinente 127 passaram, 1 pulado (fumaça do executor: modelo/vídeo locais ausentes). Conferência curta com modelo OpenVINO real: `{"cpu": ["CPU"]}`. O roteiro longo não foi repetido.

# Etapa 03b — Cena genérica e regras espaciais/temporais na mãe

Estado: **em andamento** (lote de [`prompt_cena_generica.md`](../prompt_cena_generica.md)). Vinculado ao plano ([`plano_motor_plugins.md`](../plano_motor_plugins.md)) e à [etapa 03](03_motor_minimo.md), que **continua em andamento**. Tudo permanece **em sombra**: nada chega ao dispatcher de alertas operacionais. Sem commit.

## 0. Lacuna de geração (fechada primeiro)

Reprodução do Codex: inferência da geração 1 em execução + geração 2 aceita pela mesma `PortaSombra` + geração 1 termina → ambas entravam no feed (a identidade da montagem não muda quando a sessão muda; o formatador não preservava a geração).
Correção: o evento de diagnóstico **preserva `geracao`**; `PortaSombra.geracao_vigente` sobe assim que uma geração maior é aceita (no laço da câmera, sem bloquear); o callback de publicação só entra no feed se `evento.geracao == geracao_vigente` e a montagem é a vigente — senão vai **só ao rastro** (`aposentado: true`, `motivo_aposentado: geracao_obsoleta | montagem_aposentada`); ao aceitar a nova geração o feed descarta a anterior (histórico fica no rastro); quadro de geração menor chegando atrasado é recusado na porta. A asserção que mascarava a falha (`geracao ausente → 2`) foi removida: o teste exige `geracao` no evento. Regressões: `tests/test_painel_sombra_corridas.py` (barreiras determinísticas; falham com a trava desativada).

## 1. Cena (contrato)

`src/jutta_ped/motor/cena.py`. `Cena(camera_ref, geracao, quadro_id, recebido_em, largura, altura, deteccoes, quadro)`; `Deteccao(trilha_id, classe, bbox_xyxy, confianca)`.

* **Unidades:** pixels do quadro original (x2/y2 exclusivos); tempo em segundos de relógio **monotônico local** (`recebido_em` = recepção local do quadro, não horário da câmera); confiança em [0, 1].
* **Identidade:** `trilha_id` vale dentro de (câmera, geração). Nenhuma afirmação de identidade entre IDs diferentes ou gerações.
* **Propriedade/cópia:** imutável (tuplas); `quadro` é referência que o produtor não muta depois de publicar (o runtime entrega cópia limpa). Segura para outra thread.
* **Cena vazia é válida:** avança o tempo; ausência de trilha ≠ resultado negativo.
* `criar_cena` valida e levanta `CenaInvalida` (bbox vazia/não finita, confiança fora de [0,1], trilha repetida na classe…); `deteccoes_de_objetos` adapta o que o pipeline atual já produz; `observacoes_de_cena` gera as `Observacao` do despacho aos especialistas.

### Mestre compartilhado (sem segundo detector)

`PersonCropSpecialistPipeline.detectar_pessoas(...)` expõe só a passagem do detector/rastreador de pessoas; o resultado fica reservado para o **mesmo** `frame_index` e é consumido por `detect_and_classify`, que não roda o detector de novo. Em sessão com sombra o runtime faz: captura → **uma** passagem do mestre → `Cena` enfileirada à sombra → pipeline pediátrico segue como antes (classificador e regras de companhia), reutilizando a detecção. A integração genérica (mestre → Cena → especialistas → regras) **não depende** do classificador nem das regras pediátricas.

## 2. Receita contrato 2 (retrocompatível)

Receita sem `contrato` (ou `contrato: 1`) = receita **simples** já persistida: avalia uma trilha por vez (`regra`), **comportamento inalterado**. Receita com `"contrato": 2` troca `regra` por papéis, relações e eventos:

```json
{"id": "...", "versao": "1", "contrato": 2,
 "vinculos": [{"especialista": "...", "margem_recorte": 0.1, "orcamento_ms": 500}],
 "papeis": {"alvo": {"classe": "person", "combinacao": "todas",
                     "condicoes": [{"especialista": "...", "atributo": "...", "igual": "sim", "validade_ms": 1500}]},
            "referencia": {"classe": "person", "combinacao": "todas", "condicoes": [{"especialista": "...", "atributo": "...", "minimo": 0.5}]}},
 "relacoes": {"sem_referencia_perto": {"tipo": "nenhum_proximo", "sujeito": "alvo", "outro": "referencia",
                                      "distancia": {"unidade": "altura_sujeito", "maximo": 1.5}}},
 "eventos": [{"id": "isolado", "sujeito": "alvo", "quando_todas": ["sem_referencia_perto"],
              "duracao_s": 10, "lacuna_max_s": 2, "perda_max_s": 8, "encerrar_negativo_s": 4}]}
```

* **Papel:** predicado de UM sujeito (trilha) da `classe` mestre dada, sobre atributos de especialista: `combinacao` `todas`/`alguma`; condição = `igual` (tri `sim`/`nao`, categoria, número) **ou** `minimo`/`maximo` (só atributos `numero`). Estados `sim`/`nao`/`indeterminado` com a lógica já usada (ausente/expirado/erro/atraso → indeterminado).
* **Relação** (`tipo`): `existe_proximo` | `nenhum_proximo`, `sujeito` e `outro` são papéis; `distancia = {unidade, maximo}`. Para cada sujeito (papel `sim`) e cada candidato `outro` ≠ sujeito: distância = **intervalo entre as caixas** (0 se sobrepostas), em `unidade`: `pixels`, `altura_sujeito` (dividida pela altura da caixa do sujeito) ou `diagonal_quadro` (dividida pela diagonal da imagem). **Não é distância física em metros** (perspectiva, profundidade e lente não são modelados; `altura_sujeito` é um proxy de escala, não de profundidade).
  * `existe_proximo` = `sim` se há candidato com papel `sim` a ≤ máximo; senão `indeterminado` se há candidato próximo com papel `indeterminado`; senão `nao`. `nenhum_proximo` = negação 3-valorada (`indeterminado` continua `indeterminado`). Sujeito com papel `nao` não tem relação (não se aplica); papel `indeterminado` → relação `indeterminado`.
* **Evento** (persistência): para cada sujeito de papel `sim`, a condição é a conjunção 3-valorada de `quando_todas` (relações do mesmo `sujeito`; ids de papéis também valem). Parâmetros por evento: `duracao_s` (obrigatório, > 0), `lacuna_max_s` (padrão 2.0), `perda_max_s` (padrão 10.0), `encerrar_negativo_s` (padrão 5.0). **Tempo vem de `recebido_em` das Cenas, nunca de contagem de quadros.**

### Semântica temporal (por câmera, geração, receita/configuração, sujeito, evento)

Estado: `positivo_s` (soma de intervalos entre evidências `sim` consecutivas separadas por ≤ `lacuna_max_s`), `ultimo_sim`, `negativo_desde`/`ultimo_nao`, estado `inativo`→`ativo`.

* amostra `sim` (evidência positiva): sem corrida em curso → inicia; com corrida e intervalo ≤ `lacuna_max_s` → `positivo_s += intervalo`; intervalo maior → a sustentação foi perdida e a corrida **recomeça** (o tempo da lacuna **não** conta). Zera o negativo.
* amostra `nao` (evidência negativa): zera a corrida positiva; se ativo, acumula tempo negativo sustentado (intervalos ≤ `lacuna_max_s`).
* amostra `indeterminado` ou sujeito **ausente** (cena vazia, trilha sumiu): **não** soma tempo positivo, **não** zera por si, **não** conta como negativa; registra `idade_ultima_evidencia_s` e `sustentacao_perdida`.
  * **Pausa, não ponte (correção da revisão de 05/10/2026):** a amostra indeterminada/ausente **pausa** a corrida (positiva e negativa). O tempo já confirmado é **preservado** se a pausa for curta (≤ `lacuna_max_s` desde a última evidência), mas o intervalo **através** da amostra explicitamente indeterminada **não é creditado** quando a evidência volta: a primeira evidência válida da retomada só fixa a nova referência e as seguintes voltam a acumular. Quadros simplesmente **não amostrados** (fila cheia descartou) não pausam: o intervalo segue sujeito só a `lacuna_max_s`. Regressões: `test_motor_regras.py` (reproduções da revisão, ausência, vários indeterminados, pausa longa, negativo, sem vazamento da pausa).
* **iniciar** evento: só com `positivo_s ≥ duracao_s` por evidências `sim` válidas (indeterminado nunca completa o tempo).
* **encerrar** evento ativo — com motivos distintos: `resolvido_evidencia_negativa` (negativo sustentado ≥ `encerrar_negativo_s`), `perda_de_observacao` (sem evidência `sim` nem `nao` por mais de `perda_max_s`), `sujeito_ausente_prolongado` (trilha ausente por mais de `perda_max_s`), `geracao_encerrada` / `reconfigurado` / `sessao_encerrada` (descarte de estado, não é resolução). **Indeterminado nunca encerra como resolvido.**
* Trilha que desaparece e reaparece com o **mesmo ID** é tratada como o mesmo sujeito apenas pelas regras acima (lacunas); IDs diferentes são sujeitos diferentes — nada de fusão de identidade.
* Cena com `recebido_em` **anterior ou igual** à última aceita da câmera/geração é rejeitada (fora de ordem); geração menor que a vigente é recusada; geração maior descarta o estado anterior.
* Reconfiguração (outra receita/versão/config_hash) descarta o estado (`reconfigurado`); estado de uma câmera nunca vaza para outra.

### Saída (evento de sombra, `tipo: "cena"`, `alerta_real: false`)

`{versao_evento, modo:"sombra", alerta_real:false, tipo:"cena", camera_ref, geracao, quadro_id, capturado_em, avaliado_em, receita:{id,versao,config_hash}, sujeitos:[{trilha_id, papeis:{id: estado}, relacoes:{id:{estado, proximos:[trilha...]}}}], eventos:[{id, trilha_id, estado:"inativo|candidato|ativo", positivo_s, idade_ultima_evidencia_s, sustentacao_perdida:bool, transicao:null|"iniciado"|"encerrado", motivo:null|"..."}], especialistas:[...], politica_cache, erros, descartes}`.

### Decisões da implementação onde o contrato era ambíguo

* Sujeito = **(classe, trilha_id)**: o mesmo ID em classes diferentes é outro sujeito; trilha repetida só é rejeitada dentro da mesma classe.
* Papel `nao` é **evidência negativa** do evento; papel `indeterminado` é indeterminado; a relação de um sujeito de papel `nao` é `nao_se_aplica`.
* **Operadores de condição de papel** (exatamente um por condição): `igual`, `minimo`, `maximo` (só `numero`) e **`maior_que: {especialista, atributo}`** (compara dois atributos `numero`; empate = `nao`; qualquer lado ausente/expirado/indeterminado → indeterminado, motivo `comparado:<motivo>`). Isto permite expressar "a confiança de uma classe é maior que a da outra" na **mãe**, sem mudar o protocolo do pacote.
* **Métrica de distância** opcional em `relacoes[*].distancia.metrica`: `intervalo_bbox` (padrão; 0 se sobrepostas), `centro` (entre centros das caixas), `ponto_de_apoio` (centro da borda inferior de cada caixa). As unidades valem para todas. **Nenhuma é distância física.**
* Contrato 2 rejeita **chaves desconhecidas em todos os níveis**; `eventos` obrigatório; classe do papel deve coincidir com a `classe_mestre` do especialista usado.
* `sustentacao_perdida` = último `sim` mais velho que `lacuna_max_s` com evento candidato/ativo; candidato sem evidência por mais de `perda_max_s` volta a inativo sem transição; ausência prolongada tem precedência sobre `perda_de_observacao`.
* Descartes (`reconfigurado`, `geracao_encerrada`, `sessao_encerrada`) reportam os eventos que estavam **ativos** no evento seguinte da câmera (`encerramentos_por_descarte`, `transicao:"encerrado"`); reconfigurar com receita de **mesmo hash** preserva o estado; o cache de atributos é sempre limpo.
* Contrato 1 via `processar_cena` delega a `processar` (eventos idênticos); `processar` (v1) numa câmera de contrato 2 devolve `[]` e conta `receita_contrato2_exige_cena`.
* Fila do despacho cheia só remove **amostras** (as mais antigas): para as regras isto apenas aumenta intervalos; a duração é medida por `recebido_em`.
* **Fim de sessão:** o runtime avisa a porta/despacho (`encerrar_sessao(geracao)`), que manda o motor descartar o estado como `sessao_encerrada` — só se a geração ainda é a vigente (sessão antiga terminando atrasada não encerra a nova). O evento de encerramento é reportado quando a próxima cena da câmera chegar.

## 3. Suporte da interface

O formulário atual edita **somente contrato 1**, e só os campos que ele mostra. Receita de contrato 2 entra por **importação JSON** (administrador; CSRF; auditoria): validada contra os especialistas ativos, vira versão imutável (nome existente = próxima versão), erro não grava nada, `id`/`versao` no JSON só valem se coincidirem com o gravado. **Editar** pelo formulário uma receita cujo conteúdo ele não preserva (contrato 2; ou, no contrato 1, `parametros`/`versoes_aceitas` num vínculo, campos extras, mais condições que as 4 linhas) é **recusado com explicação**, nada é gravado e nada é apagado. O feed de sombra mostra eventos `tipo:"cena"` (sujeitos, papéis, relações, estado/transição dos eventos de persistência). Receitas simples já persistidas seguem iguais.

## 4. Especialista de papel (classificador criança/adulto atual)

Inspeção do modelo em produção (`pediatria_child_detector_v6_jutta`, YOLO11n **detecção**, classes `adult`/`child`, 640×640, OpenVINO batch 1) e do caminho legado (`PersonCropSpecialistPipeline._classify_crop`):

| Aspecto | Legado | Pacote declarativo (`tools/pacote_papel.py`) | Cabe? |
|---|---|---|---|
| Entrada | recorte da pessoa (BGR) com folga 12 % × 8 % (mín. 8 px) | recorte da pessoa; `margem_recorte` única (proporção igual nos dois eixos) | **aproximado** (receita usa 0,10) |
| Pré-processamento | `predict(crop)` com letterbox do Ultralytics | `RGB 640×640 letterbox 0-1` aplicado pelo backend | sim |
| Classes e saída | maior confiança entre `child`/`adult` (`conf=0.03`), aceita ≥ 0,25 senão `uncertain` | atributos `numero` `confianca_crianca`/`confianca_adulto` = maior confiança de cada classe (backend usa `conf=0.01`) | sim |
| Decisão de papel | `argmax` + limiar | **receita contrato 2**: `minimo` + `maior_que` (host) | sim, na mãe |
| Tamanho mínimo | `min(largura, altura) ≥ 24` px | `altura_minima_px: 24` (só altura) | **aproximado** |
| Lote | 1 | `lote_max: 1` (o modelo exportado tem batch fixo 1) | sim |

**Evidência real (fumaça, não qualidade):** `tests/test_pacote_papel_paridade.py` passa o **mesmo recorte** ao caminho legado e ao pacote pelo executor genérico (backend real OpenVINO, CPU): 12 recortes de quadros locais, **diferença máxima de confiança 0,0000**. Isso prova entrada/pré-processamento/classes/saída; **não** prova equivalência da migração nem qualidade.

Dois defeitos **reais** apareceram na fumaça e foram corrigidos: (1) o backend Ultralytics só carrega pasta OpenVINO cujo nome termina em `_openvino_model` → o validador agora recusa (`openvino_nome`) e o manual registra a convenção; (2) export OpenVINO com **batch fixo** recebia lote > 1 → o backend fatia o lote pelo `batch` do `metadata.yaml` (`test_backend_ultralytics_lote.py`).

**O que NÃO está migrado (a adaptação pediátrica é parcial):** a receita de exemplo `exemplos/receita_pediatria_sombra_contrato2.json` (valida contra o pacote, `test_receita_pediatria_sombra.py`) cobre papel por confiança, proximidade criança–adulto (métrica `ponto_de_apoio`, razão 1,75 = `adult_near_distance_ratio`) e persistência (3,0 s = `unbound_child_alone_grace_seconds`). Os valores são **iniciais, derivados dos parâmetros atuais, não validados**. Falta, e continua só no caminho operacional atual: cache de classificação por trilha (TTL, gate por IoU/área, orçamento de especialista por quadro e prioridade); `PediatricRoleContextAdjuster`; `identity_stabilizer`; `weak_child_promoter`/`resolve_role_conflicts`; vínculo criança–adulto **confirmado e lembrado** (`relationship_confirmation`), tolerância de oclusão do adulto, o estado `CHILD_SEPARATED` (adulto visível porém **longe** ≥ 3,25 — a relação atual só expressa distância **máxima**) e o tratamento de `UNCERTAIN`. Um pacote de bebê no colo **não** substitui este classificador.

## 5. Evidências do lote

| Evidência | Onde |
|---|---|
| Geração antiga em execução não publica no feed vigente; desligar/trocar receita seguem funcionando (falha com a trava desativada) | `test_painel_sombra_corridas.py` |
| Cena sem passagem pela lógica pediátrica; mestre **uma vez** por quadro compartilhado (contagem de chamadas); stride respeitado; operacional idêntico com/sem sombra | `test_motor_cena_mestre.py`, `test_motor_cena.py` |
| Regras com relógio controlado: geometria, relações 3-valoradas, persistência, indeterminado não completa tempo nem resolve, motivos de encerramento, cena vazia, desaparecimento/reaparecimento, IDs distintos, cena fora de ordem, reinício, troca de receita | `test_motor_regras.py` |
| Integração no motor: duas câmeras, várias trilhas, expiração, resultado atrasado, perda/troca de versão do especialista (`E_CONTRATO_INCOMPATIVEL` → indeterminado), contrato 1 idêntico, **classe mestre ≠ `person`** (`veiculo`) | `test_motor_regras_motor.py` |
| Cadeia completa sessão → mestre → Cena → despacho → motor real → regras → encerramento de sessão | `test_motor_cena_runtime_real.py` |
| Painel: importação v2, recusas, edição sem perda silenciosa, receita simples inalterada, câmeras isoladas, reinício/troca/desligar, feed de regras | `test_painel_cena_regras.py` |
| Fumaça real do pacote de papel (paridade com o legado) e receita pediátrica válida | `test_pacote_papel_paridade.py`, `test_receita_pediatria_sombra.py` |
| Navegador (Edge real): importação inválida e válida, receita contrato 2 sem "Nova versão", feed com cena/estado/transição, sem rolagem horizontal | capturas locais `%TEMP%\jutta_dashboard_shots\4*.png` (dados fictícios) |

Resultado: suíte completa **1096 passaram, 6 puladas** (as mesmas 6: navegador de vídeo que exige go2rtc real). Não se repetiu a bateria de capacidade.

## 6. Pendências e limites

* A **migração** da pediatria **não está concluída** (ver seção 4); a comparação com a referência humana (etapa 02) continua requisito para aprová-la, e a etapa 02 segue aberta.
* Sombra apenas: nenhum evento chega ao dispatcher de alertas; ativação operacional de receitas não existe.
* Política de alerta **já ativo** definida só para a sombra (indeterminado não resolve; perda distinta de resolução); **política operacional** (rebaixar/manter/notificar) ainda é decisão a tomar.
* Interface: sem editor visual para papéis/relações/eventos (importação JSON); `invalidar_ao_errar` por receita não existe.
* A relação de distância só tem **máximo**; faltam distância mínima (separação), memória de vínculo confirmado e oclusão.
* Fumaça real só do pacote de papel; revisão visual com vídeo/overlay go2rtc real e popups em tela estreita seguem pendentes.
* Despacho: uma thread de sombra para todas as câmeras; fila compartilhada com justiça fina sob carga é etapa 04.
* O evento de `sessao_encerrada` é reportado quando a próxima cena da câmera chegar (não há evento avulso no encerramento).

## 7. Fora do lote

Nós remotos, térmico, treino, executor compartilhado sob carga, alertas operacionais a partir da sombra, editor visual das relações.

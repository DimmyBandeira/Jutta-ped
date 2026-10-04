# Manual do especialista

**Versão do documento:** 0.2 (rascunho) · **Contrato:** `protocolo: 1` · **Estado:** schema e validador
estático **implementados** (`src/jutta_ped/especialistas/`); instalador, executor e inferência **não**. A
seção 12 consolida as decisões que o validador aplica; o restante do texto continua sendo proposta
para revisão onde estiver marcado.

> **Conformidade estática ≠ modelo aprovado.** O validador confere estrutura, referências, arquivos e hashes.
> Ele não carrega nem executa pesos, não verifica funcionamento, desempenho, classes dos `.pt` nem autoria.

Convenções: **DEVE / NÃO DEVE** = obrigação do contrato. **(proposta)** = ainda em avaliação; não
construa sobre isso sem confirmar. Exemplos são ilustrativos e não representam um pacote entregue.

Documentos relacionados: [protocolo dos nós](protocolo_nos.md) (como o mesmo contrato roda em outro
servidor), [motor de análise](motor_de_analise.md) (arquitetura), [plano](plano_motor_plugins.md).

---

## 1. Para que serve

Um **especialista** é um pacote que, dado um recorte de imagem (ou um quadro), devolve **atributos**
tipados sobre aquilo que viu. Exemplos: "a pessoa carrega um bebê no colo", "o adulto está de costas",
"há carrinho". Nada além disso.

Quem faz o quê:

| Responsabilidade | Sistema-mãe (host) | Especialista |
|---|---|---|
| Câmeras, captura, detector mestre e rastreamento | sim | não |
| Recortar a pessoa e entregar ao especialista | sim | não |
| Rodar o modelo e produzir atributos | sim (pacote declarativo) | descreve como, no manifesto |
| Decidir validade, cache e expiração | sim | só declara limites |
| Regras, pontuação, alerta, popup | sim | **nunca** |
| Escolher o dispositivo (CPU/GPU/nó remoto) | sim | declara preferências |

Na versão 1 só existem **especialistas declarativos**: manifesto + pesos, **sem código**. O host
executa o modelo. O pacote NÃO DEVE conter Python, DLLs, scripts de instalação nem qualquer código
executável. Especialista com código fica fora do `protocolo: 1`.

## 2. Estrutura do pacote

Um `.zip` com esta árvore (nomes fixos):

```
meu-especialista-1.0.0.zip
├── manifest.json          # obrigatório
├── modelos/
│   └── bebe_colo.pt       # ou pasta OpenVINO (.xml + .bin), ou .onnx
├── LEIAME.md              # opcional, mostrado ao admin na instalação
└── amostras/              # opcional: recortes de teste e saídas esperadas
    ├── 001.jpg
    └── 001.esperado.json
```

Regras: caminhos relativos e sem `..`; sem links simbólicos; só os tipos `.json .md .txt .jpg .jpeg .png .pt
.xml .bin .onnx .yaml .yml`; limites e regras de nomes na seção 12.

## 3. Manifesto (`manifest.json`)

Codificação UTF-8. Campos desconhecidos no nível raiz DEVEM ser rejeitados (evita erro de digitação
passar despercebido); extensões usam o objeto `x` (livre, ignorado pelo host).

### 3.1 Campos

| Campo | Obrig. | Tipo | Descrição |
|---|---|---|---|
| `protocolo` | sim | inteiro | Versão do contrato. Aqui, `1`. O host recusa valor que não conhece. |
| `pacote.id` | sim | texto | Identidade estável, minúsculas, `[a-z0-9.-]`, formato domínio-reverso (`br.jutta.bebe-no-colo`). Nunca muda entre versões. |
| `pacote.versao` | sim | texto | SemVer (`1.2.0`). Versão do **pacote**, independente do protocolo e dos pesos. |
| `pacote.nome` | sim | texto | Nome para a tela do admin (≤ 60). |
| `pacote.descricao` | não | texto | O que o especialista mede (≤ 400). |
| `pacote.autor` | sim | texto | Quem responde pelo pacote. Informativo: não prova autoria. |
| `entrada.fonte` | sim | `visivel` \| `termica` | Qual imagem da câmera ele consome. |
| `entrada.alvo` | sim | objeto | `{"tipo":"recorte","classe_mestre":"person"}` ou `{"tipo":"quadro"}`. |
| `entrada.altura_minima_px` | não | inteiro | Altura mínima do recorte no quadro original. Abaixo disso o host NÃO chama o especialista e o atributo fica **indeterminado** (`recorte_pequeno`). Padrão: 0. |
| `entrada.formato` | sim | objeto | Pré-processamento esperado (seção 3.2). |
| `entrada.lote_max` | não | inteiro | Maior lote aceito. Padrão: 1. |
| `modelos[]` | sim (≥1) | lista | Pesos usados (seção 3.3). |
| `atributos[]` | sim (≥1) | lista | O que o especialista produz (seção 3.4). |
| `parametros[]` | não | lista | Ajustes permitidos (seção 3.5). |
| `custo` | não | objeto | `{"ms_por_recorte":18,"hardware":"RTX 3050 fp16, lote 8"}`. Referência de medição, não garantia. |
| `compatibilidade.protocolo_min` | não | inteiro | Reservado. No protocolo 1 só aceita o valor `1` (igual a `protocolo`); não implica compatibilidade com protocolos menores (seção 12.2). |

### 3.2 `entrada.formato`

```json
{ "cor": "RGB", "largura": 224, "altura": 224,
  "redimensionamento": "letterbox", "normalizacao": "0-1" }
```

* `cor`: `RGB` \| `BGR` \| `GRAY`. `redimensionamento`: `letterbox` (mantém proporção, preenche) \|
  `esticar`. `normalizacao`: `0-255` \| `0-1`.
* O host entrega o recorte **já nesse formato** (o pré-processamento é do host, igual para todos os
  nós). O especialista NÃO DEVE depender de pixels além do que declarou.
* Coordenadas de qualquer caixa retornada são relativas ao recorte entregue, normalizadas em `[0,1]`.

### 3.3 `modelos[]`

```json
{ "id": "principal", "arquivo": "modelos/bebe_colo.pt", "formato": "pt",
  "sha256": "<64 hex>", "dispositivos": ["cuda", "cpu"] }
```

* `formato`: `pt` \| `openvino` \| `onnx`. `pt` e `onnx` apontam um arquivo; `openvino` aponta a **pasta**
  do modelo (exatamente um `.xml` e um `.bin` com o mesmo nome-base, mais `metadata.yaml` opcional) e
  `sha256` usa o hash canônico de pasta `jutta-dir-v1` (seção 12.1).
* `sha256` é OBRIGATÓRIO e identifica os **pesos**. Pesos diferentes = hash diferente, mesmo com
  `pacote.versao` igual; por isso o host e os nós comparam o hash, não só a versão.
* `dispositivos`: preferências em ordem. `cpu` DEVE estar presente (reserva universal).

### 3.4 `atributos[]` e como sair do modelo

Cada atributo tem um tipo. O especialista declarativo liga o atributo à saída do modelo em `de`.

```json
{
  "nome": "bebe_no_colo",
  "tipo": "tri",
  "descricao": "A pessoa carrega um bebê no colo.",
  "validade_ms": 1500,
  "de": { "modelo": "principal", "tipo": "deteccao", "classes": ["bebe_colo"],
          "agregacao": "maior_confianca",
          "limiar_presente": 0.40, "limiar_ausente": 0.15 }
}
```

| `tipo` | Valores | Observação |
|---|---|---|
| `tri` | `sim` \| `nao` \| indeterminado | Tri-estado. |
| `numero` | número, com `faixa:[min,max]` e `unidade` | |
| `categoria` | um de `categorias[]` | |

Mapeamento `de.tipo`: as combinações válidas de tipo do atributo, mapeamento, agregação e campos estão na
tabela da **seção 12.3**; qualquer outra é recusada (`combinacao_invalida`). Para `tri`: confiança ≥
`limiar_presente` → `sim`; < `limiar_ausente` → `nao`; entre os dois → **indeterminado**
(`baixa_confianca`). Nenhuma detecção → `nao`.

Regras:

1. Nomes de atributo são únicos no pacote, `[a-z][a-z0-9_]*`. São o que regras e receitas referenciam,
   portanto renomear é mudança incompatível (nova versão maior).
2. `validade_ms` (OBRIGATÓRIO) é o **máximo** de tempo, contado a partir do momento do quadro
   observado, em que o valor ainda pode ser usado pelas regras. A receita pode **reduzi-lo**, nunca
   ampliá-lo.
3. **Indeterminado não é "não".** `nao` afirma ausência; indeterminado diz que o especialista não
   pôde concluir. O host e as regras NÃO DEVEM tratá-los igual.

### 3.5 `parametros[]`

```json
{ "nome": "limiar_presente", "tipo": "numero", "padrao": 0.40, "min": 0.2, "max": 0.9,
  "editavel_por": "receita", "descricao": "Confiança mínima para afirmar presença." }
```

`editavel_por`: `fixo` (só o autor, via nova versão) \| `admin` (instalação) \| `receita` (por receita
e por câmera). Todo parâmetro com `min`/`max`; valor fora da faixa é recusado pelo host. Um parâmetro só tem efeito se um
campo de `de` o referenciar com `{"$parametro": "nome"}` (seção 12.4). Alterar um
parâmetro gera um novo `config_hash` (seção 4.1), nunca muda um resultado já emitido.

### 3.6 Exemplo válido completo

```json
{
  "protocolo": 1,
  "pacote": { "id": "br.jutta.bebe-no-colo", "versao": "1.0.0", "nome": "Bebê no colo",
              "descricao": "Detecta bebê no colo e bebê no conforto.", "autor": "Equipe Jutta" },
  "entrada": { "fonte": "visivel", "alvo": { "tipo": "recorte", "classe_mestre": "person" },
               "altura_minima_px": 160, "lote_max": 8,
               "formato": { "cor": "RGB", "largura": 640, "altura": 640,
                            "redimensionamento": "letterbox", "normalizacao": "0-1" } },
  "modelos": [ { "id": "principal", "arquivo": "modelos/bebe_colo.pt", "formato": "pt",
                 "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
                 "dispositivos": ["cuda", "cpu"] } ],
  "atributos": [
    { "nome": "bebe_no_colo", "tipo": "tri", "validade_ms": 1500,
      "de": { "modelo": "principal", "tipo": "deteccao", "classes": ["bebe_colo"],
              "agregacao": "maior_confianca", "limiar_presente": { "$parametro": "limiar_presente" }, "limiar_ausente": 0.15 } },
    { "nome": "bebe_no_conforto", "tipo": "tri", "validade_ms": 1500,
      "de": { "modelo": "principal", "tipo": "deteccao", "classes": ["bebe_conforto"],
              "agregacao": "maior_confianca", "limiar_presente": { "$parametro": "limiar_presente" }, "limiar_ausente": 0.15 } }
  ],
  "parametros": [ { "nome": "limiar_presente", "tipo": "numero", "padrao": 0.40,
                    "min": 0.2, "max": 0.9, "editavel_por": "receita" } ],
  "custo": { "ms_por_recorte": 16, "hardware": "referência da equipe, não garantido" }
}
```

O `sha256` acima é um marcador; um pacote real DEVE ter o hash verdadeiro dos pesos. Este exemplo
não corresponde a um pacote executável entregue.

### 3.7 Exemplos inválidos (o validador DEVE recusar)

| Problema | Motivo |
|---|---|
| `"protocolo": 2` | Contrato desconhecido pelo host. |
| `modelos[0]` sem `sha256` | Pesos sem identidade. |
| dois atributos `bebe_no_colo` | Nome duplicado. |
| `limiar_ausente` > `limiar_presente` | Faixa de indeterminação invertida. |
| `validade_ms` ausente ou ≤ 0 | Sem limite de uso do atributo. |
| `modelos[].dispositivos` sem `cpu` | Sem reserva universal. |
| `arquivo`: `../outro/modelo.pt` | Caminho fora do pacote. |
| arquivo `instalar.py` no zip | Pacote declarativo NÃO DEVE conter código. |
| `de.classes` com nome que o modelo não tem | Atributo nunca poderia ser produzido. |

## 4. Execução: pedido e resultado

O mesmo contrato vale para execução local e em nó remoto ([protocolo dos nós](protocolo_nos.md)). Os
nomes de campo abaixo são normativos; o transporte não os altera.

### 4.1 Pedido

Metadados em JSON; a imagem segue como bytes (JPEG ou bruto) junto do pedido.

```json
{
  "pedido_id": "p-000123",
  "tentativa": 1,
  "camera_ref": "c7",
  "geracao": 12,
  "trilha_id": 41,
  "quadro_id": 90871,
  "especialista": { "id": "br.jutta.bebe-no-colo", "versao": "1.0.0", "config_hash": "sha256:ab12…" },
  "orcamento": { "idade_na_emissao_ms": 120, "restante_ms": 380 },
  "recorte": { "codec": "jpeg", "largura": 640, "altura": 640, "fonte": "visivel",
               "bbox_quadro": [412, 130, 570, 442] },
  "contexto": { "altura_pessoa_px": 312, "quadro_largura": 960, "quadro_altura": 720 },
  "parametros": { "limiar_presente": 0.40 }
}
```

| Campo | Significado |
|---|---|
| `pedido_id` | Identidade **lógica** do pedido. Não muda entre tentativas. |
| `tentativa` | 1, 2… Cada nova tentativa é outro envio do mesmo pedido lógico. |
| `camera_ref` | Referência opaca da câmera. NÃO identifica a câmera real nem o local. |
| `geracao` | Número da sessão de análise da câmera; incrementa a cada reinício. |
| `trilha_id` | Identificador da pessoa rastreada **dentro da geração**. Não vale entre gerações. |
| `quadro_id` | Quadro de origem, crescente por câmera e geração. |
| `config_hash` | Hash dos pesos + parâmetros efetivos desta chamada. |
| `orcamento.idade_na_emissao_ms` | Idade do quadro quando o pedido foi emitido (duração, não horário). |
| `orcamento.restante_ms` | Tempo restante, **neste instante**, para o resultado ainda ser útil. |

Tempo: o contrato usa **somente durações relativas**. NÃO há horário de relógio nas mensagens, então
host e nó NÃO precisam ter relógios sincronizados. Cada lado mede as durações no seu próprio relógio.

### 4.2 Resultado

```json
{
  "pedido_id": "p-000123", "tentativa": 1, "geracao": 12, "trilha_id": 41, "quadro_id": 90871,
  "especialista": { "id": "br.jutta.bebe-no-colo", "versao": "1.0.0" },
  "estado": "ok",
  "atributos": [
    { "nome": "bebe_no_colo", "estado": "definido", "valor": "sim", "confianca": 0.83 },
    { "nome": "bebe_no_conforto", "estado": "indeterminado", "motivo": "baixa_confianca" }
  ],
  "tempos": { "fila_ms": 4, "inferencia_ms": 18 },
  "erro": null
}
```

* `estado` do resultado: `ok` \| `erro` \| `recusado`. Em `erro`/`recusado`, `atributos` é vazio e
  `erro` traz `{codigo, mensagem}`.
* Por atributo: `estado` = `definido` (com `valor`, e `confianca` quando houver) ou `indeterminado`
  (com `motivo`: `recorte_pequeno`, `baixa_confianca`, `fora_do_campo`, `modelo_indisponivel`,
  `prazo_esgotado`).
* O resultado DEVE repetir `pedido_id`, `tentativa`, `geracao`, `trilha_id`, `quadro_id` e a identidade
  do especialista. Resultado sem eles é descartado.

### 4.3 Códigos de erro

| Código | Quando |
|---|---|
| `E_ESPECIALISTA_DESCONHECIDO` | Id ou `config_hash` não instalado neste executor. |
| `E_VERSAO_INCOMPATIVEL` | Versão/pesos diferentes dos pedidos. |
| `E_RECORTE_INVALIDO` | Imagem ilegível ou fora do formato declarado. |
| `E_MODELO_INDISPONIVEL` | Modelo não carregou (memória, dispositivo). |
| `E_PRAZO_ESGOTADO` | `restante_ms` já não comporta a execução; nada foi processado. |
| `E_SOBRECARGA` | Executor sem capacidade agora; pode ser tentado em outro lugar. |
| `E_INTERNO` | Falha inesperada. |

### 4.4 Validade e o que o host faz com o resultado (obrigações do host)

1. **Validade por atributo.** O atributo vale até `momento_do_quadro + min(validade_ms do manifesto,
   validade_ms da receita)`. Passado isso ele vira **indeterminado** para as regras, nunca "último
   valor conhecido como certeza".
2. **Prazo não renova.** Nova tentativa NÃO DEVE aumentar o orçamento. `restante_ms` só decresce.
3. **Resultado antigo não substitui novo.** O host aceita um resultado apenas se `geracao` for a atual
   da câmera e `quadro_id` for **maior que** o do último aceito para aquele `(trilha_id, atributo)`.
4. **No máximo um resultado válido por pedido lógico.** O primeiro aceito vence; os demais são
   descartados (trabalho duplicado é permitido, efeito duplicado não).
5. **Falha não é segurança.** Ausência de resultado produz indeterminado, nunca `nao`.
6. O host registra a versão do pacote e o `config_hash` usados em cada alerta.

## 5. O que o especialista recebe (e não recebe)

Recebe: o recorte já no formato declarado; `altura_pessoa_px`, dimensões do quadro; os parâmetros
efetivos. NÃO recebe: nome real ou localização da câmera, identificação de pessoa, o quadro inteiro
(salvo `alvo: quadro`), alertas, regras nem histórico de outros quadros.

## 6. Molde declarativo mínimo

Para começar um especialista no workspace separado:

```
meu-especialista/
├── manifest.json        # copie o exemplo da seção 3.6 e ajuste
├── modelos/             # seus pesos (.pt, pasta openvino ou .onnx)
├── amostras/            # 5-20 recortes reais + .esperado.json (valores que deve produzir)
└── LEIAME.md
```

Passos: (1) treinar e exportar o modelo; (2) escolher o formato de entrada que o modelo espera; (3)
mapear classes → atributos em `atributos[].de`; (4) medir `ms_por_recorte` e gravar em `custo`; (5)
calcular o `sha256` com `python -m src.jutta_ped.especialistas hash <arquivo-ou-pasta>`; (6) rodar o validador
(seção 7); (7) empacotar em `.zip` com `manifest.json` na raiz. O ponto de partida está em
[`especialistas_molde/`](../especialistas_molde/LEIAME.md): sem pesos, reprovado pelo validador até ser preenchido.

`amostras/*.esperado.json` tem o formato do campo `atributos` da seção 4.2. Servem para o validador
conferir, quando houver executor, que o pacote produz o que o autor afirma. Os pesos do exemplo da
seção 3.6 são de referência; este manual não entrega nenhum pacote executável.

## 7. Validador (implementado, estático)

```
python -m src.jutta_ped.especialistas validar <pasta-ou-zip> [--formato texto|json] [--host-protocolo 1]
python -m src.jutta_ped.especialistas hash <arquivo-ou-pasta>      # SHA-256 / hash canônico para preencher o manifesto
```

Requer as dependências de `requirements-mvp.txt` (`jsonschema`, `pyyaml`). Não importa nem carrega nada do
pacote. Código-fonte: `src/jutta_ped/especialistas/`; schema: `esquemas/manifest.v1.schema.json`.

| Saída | Significado |
|---|---|
| `0` | **Conformidade estática** (pode haver avisos). Não é aprovação do modelo para execução. |
| `1` | Erro de uso ou de E/S (caminho inexistente ou ilegível). |
| `2` | Manifesto inválido: JSON, schema, duplicados, referências, combinações, faixas, classe inexistente. |
| `3` | Arquivos: ausentes, nomes perigosos, links, tipos proibidos, código/executável, amostras. |
| `4` | `sha256` dos pesos não confere. |
| `5` | Incompatível com o host (`protocolo` fora do suportado). |
| `6` | Limites excedidos ou ZIP inválido/criptografado. |

Com mais de uma categoria de erro o código é o da primeira presente em **6, 3, 5, 2, 4**; o relatório JSON
lista todos os problemas, em ordem determinística. Cada etapa só é confiável se as anteriores passaram: sem
ler o pacote não há manifesto, sem entender o protocolo não há schema, sem manifesto válido não há hashes
esperados.

A saída `json` traz `conformidade_estatica`, `codigo_saida`, `aviso_escopo`, `pacote`, `hashes_calculados`,
`problemas[]` (`categoria`, `codigo`, `caminho`, `mensagem`), `avisos[]`, `verificacoes_nao_realizadas[]` e
os `limites` aplicados. A saída texto repete o aviso de escopo no rodapé.

## 8. Instalação e segurança (requisitos para o instalador)

* Quem instala: somente administrador, pela tela do admin. Origem: arquivo enviado pela equipe. O hash
  garante integridade, **não** autoria; confiança vem de quem entrega o pacote.
* Limites de extração: **política inicial** da seção 12.6 (aplicada pelo validador e a ser reaplicada
  pelo instalador).
* Instalar em área isolada, validar tudo, **só então** ativar; manter a versão anterior para retorno.
* O host NÃO DEVE executar nada do pacote além de carregar o modelo declarado.
* Pesos `.pt` do PyTorch podem executar código ao carregar (formato pickle). Por isso, na v1,
  `.pt` só de origem da equipe; para terceiros prefira `openvino` ou `onnx`.

## 9. Versionamento e compatibilidade

Três versões independentes: **protocolo** (este contrato), **pacote** (SemVer) e **pesos** (`sha256`).

* Mudança que renomeia/remove atributo, altera seu tipo, ou reduz `validade_ms` é **maior**.
  Atributo novo é **menor**. Pesos novos com mesmos atributos: **menor** ou **correção**, mas o
  `sha256` muda sempre.
* Receitas referenciam `pacote.id` + versão (faixa). Ao atualizar um pacote, receitas que dependem de
  atributo removido ficam **inválidas** e o host DEVE avisar antes de ativar.
* Política de protocolo: seção 12.2 (igualdade exata com um protocolo suportado; menor **não** é
  presumido compatível).

## 10. Cenários de referência

Cenários de rede (queda do nó, tentativa local concorrente, resposta após reinício) estão com mensagens
completas no [protocolo dos nós](protocolo_nos.md#10-cenários). Os de contrato:

| Cenário | Resultado esperado |
|---|---|
| Recorte nítido, bebê visível | `bebe_no_colo`: `definido/sim`, confiança 0,83. |
| Recorte de 90 px (mínimo 160) | Host nem chama; `indeterminado/recorte_pequeno`. |
| Confiança 0,25 (entre 0,15 e 0,40) | `indeterminado/baixa_confianca`; **não** `nao`. |
| Nenhuma detecção | `definido/nao`. |
| Resultado chega com `restante_ms` esgotado | Descartado; atributo fica indeterminado (`prazo_esgotado`). |
| Atributo com 2 s de idade, `validade_ms` 1500 | Tratado como indeterminado pelas regras. |
| Pacote com `protocolo: 2` | Validador sai com código 5; instalação recusada. |
| Pacote sem `sha256` nos pesos | Validador sai com código 2. |
| Resultado da `geracao` 11 com a câmera na 12 | Descartado. |

## 11. Questões abertas (com impacto)

| # | Questão | Impacto se não fechar |
|---|---|---|
| 1 | ~~Hash canônico de pasta OpenVINO~~ | **Fechado** na seção 12.1 (`jutta-dir-v1`). |
| 2 | Atributos derivados de outros atributos (ex.: orientação a partir de pose) | **Fora do subconjunto v1** (12.7). Exige `de.tipo` novo ou lógica no host. |
| 3 | Atributo estruturado (pose: caixas/pontos) | **Fora do subconjunto v1** (12.7). |
| 4 | Extrator térmico (calor por região): declarativo ou do host? | **Fora do subconjunto v1** (12.7); define se a fusão térmica cabe neste contrato. |
| 5 | Limites numéricos de extração e de lote | Valores da 12.6 são política inicial, ajustáveis. |
| 6 | Assinatura de pacote (autoria) | Hoje só hash; relevante antes de aceitar terceiros. |
| 7 | Quem pode editar cada parâmetro na tela (permissão fina) | Afeta tela de receitas. |
| 8 | Inspeção das classes de `.pt` e `.onnx` sem executar código | Hoje "não verificado"; pode exigir conversão para OpenVINO ou ferramenta própria. |

Os itens 2 a 4 continuam os que mais pesam: pose e térmico, previstos no motor, **não cabem** no
`protocolo: 1` como implementado.

## 12. Decisões consolidadas do contrato implementável (protocolo 1)

Esta seção é **normativa para o validador**. O que conflitar com seções anteriores vale o que está aqui.

### 12.1 Hash dos pesos

* `pt`, `onnx`: SHA-256 dos bytes do arquivo.
* `openvino`: algoritmo `jutta-dir-v1`. Para cada arquivo regular da pasta, ordenado pelos **bytes UTF-8**
  do caminho relativo (separador `/`, Unicode NFC), monta-se o registro
  `b"F" + uint32_BE(len(caminho)) + caminho_utf8 + uint64_BE(tamanho) + sha256(conteúdo)` (32 bytes).
  O hash é o SHA-256 da concatenação dos registros. Os comprimentos delimitam os campos, então nomes
  diferentes nunca geram a mesma sequência; a ordem de listagem do disco ou do ZIP não influencia. Pastas
  vazias não entram e links são proibidos. O comando `hash <pasta>` calcula esse valor.
* Todos os arquivos da pasta contam (inclusive `metadata.yaml`). SHA-256 prova **integridade**, não autoria.

### 12.2 Versões suportadas

* O host declara um conjunto de protocolos suportados (hoje `{1}`) e o validador recebe
  `--host-protocolo`. Um pacote é compatível se, e somente se, `protocolo` é **igual** a um protocolo
  suportado. Protocolo menor **não** é presumido compatível: cada versão do contrato diz explicitamente o
  que ainda aceita.
* `protocolo` não inteiro, ausente ou booleano é manifesto inválido (código 2); inteiro não suportado é
  incompatibilidade (código 5) e o resto do manifesto não é interpretado.
* `compatibilidade.protocolo_min` só aceita `1` e não altera a regra acima (campo reservado).
* Três versões independentes: `protocolo`, `pacote.versao` (SemVer) e pesos (`sha256`).

### 12.3 Combinações válidas de atributo

| `tipo` | `de.tipo` | `de.agregacao` | Exige | Proíbe | Observação |
|---|---|---|---|---|---|
| `tri` | `deteccao` | `maior_confianca` | `classes`, `limiar_presente`, `limiar_ausente` | `classe`, `faixa`, `categorias` | Sem detecção → `nao` |
| `tri` | `classificacao` | `probabilidade` | `classe`, `limiar_presente`, `limiar_ausente` | `classes`, `faixa`, `categorias` | Probabilidade da classe |
| `numero` | `deteccao` | `contagem` | `classes`, `faixa` `[0, N]` (N inteiro) | `classe`, `categorias`, limiares | |
| `numero` | `deteccao` | `maior_confianca` | `classes`, `faixa` `[0, 1]` | `classe`, `categorias`, limiares | |
| `numero` | `classificacao` | `probabilidade` | `classe`, `faixa` `[0, 1]` | `classes`, `categorias`, limiares | |
| `categoria` | `classificacao` | `classe_mais_provavel` | `categorias` (≥ 2, únicas) | `classe`, `classes`, `faixa`, limiares | Categorias = nomes de classe |

Toda outra combinação é `combinacao_invalida`. `limiar_ausente` ≤ `limiar_presente` (igualdade = sem faixa
de indeterminação). Limiares estão em [0, 1].

### 12.4 Parâmetros ligados aos campos

* Os campos configuráveis são `de.limiar_presente` e `de.limiar_ausente`. Cada um é um número **ou**
  `{"$parametro": "nome"}`, que o liga a um item de `parametros[]` (tipo `numero`).
* No protocolo 1 todo parâmetro é um limiar: `min`, `padrao` e `max` estão em [0, 1], com
  `min < max` e `min ≤ padrao ≤ max`. Referência a parâmetro inexistente é erro; parâmetro não referenciado
  gera **aviso**.
* Os **valores padrão** já são conhecidos e são verificados: se `limiar_ausente` padrão (literal ou do
  parâmetro) for maior que `limiar_presente` padrão, o pacote é recusado (`limiares_invertidos`, código 2).
* Os valores **efetivos** de receita/câmera só existem no host: se as faixas `min`/`max` dos parâmetros
  permitem inverter os limiares, o validador emite **aviso** e o host DEVE validar o valor efetivo.

### 12.5 O que é estático e o que exigiria carregar o modelo

| Estático (feito) | Exige carregar/executar (NÃO feito) |
|---|---|
| JSON estrito (sem chaves duplicadas, sem `NaN`), schema, combinações, referências, unicidade | Funcionamento do modelo e compatibilidade com o dispositivo declarado |
| Existência dos arquivos, estrutura OpenVINO, hashes | Classes internas de `.pt` (pickle executa código ao carregar) e de `.onnx` |
| Nomes de caminho, tipos permitidos, assinaturas de executável, limites | Custo `ms_por_recorte`, precisão, reprodução de `amostras/*.esperado.json` |
| Classes de OpenVINO **se** houver `metadata.yaml` (lido com `safe_load`, só dados) | Autoria do pacote |

Toda execução do validador lista em `verificacoes_nao_realizadas` o que ficou de fora. Sem
`metadata.yaml` a conferência de classes de OpenVINO também é registrada como não realizada.

### 12.6 Limites e nomes (política inicial, ajustável pela equipe)

* ≤ 200 arquivos; ≤ 500 MiB descompactados no total; ≤ 400 MiB por arquivo; ≤ 20 níveis; caminho ≤ 240
  caracteres e componente ≤ 100; razão de compressão por arquivo ≤ 50:1; `manifest.json` ≤ 256 KiB.
* A leitura nunca confia no tamanho declarado: é limitada pelos bytes realmente lidos.
* JSON: chaves duplicadas, `NaN`/`Infinity` e números fora da capacidade de ponto flutuante (`1e400`) ou
  inteiros gigantes são manifesto inválido (código 2); o validador nunca deve terminar sem relatório (falha
  inesperada vira `falha_interna_validador`, a reportar à equipe).
* Valem para **arquivos e para entradas de pasta** (ex.: `../escape/` no ZIP é recusado). Um arquivo não
  pode ter o mesmo nome de uma pasta, explícita ou implícita (`LEIAME.md` e `LEIAME.md/sub.txt`), sem
  diferenciar caixa nem normalização Unicode (`colisao_arquivo_pasta`).
* Recusados: caminho absoluto ou com letra de unidade (`C:`), `..`, componentes vazios, barra invertida,
  caracteres de controle, `<>:"|?*` (inclui fluxos alternativos `arquivo:fluxo`), nomes reservados do Windows
  (`CON`, `PRN`, `AUX`, `NUL`, `COM0-9`, `LPT0-9`, com ou sem extensão), nomes terminando em ponto ou espaço,
  links simbólicos e junções, ZIP criptografado ou com compressão diferente de STORED/DEFLATE, entradas
  duplicadas e colisões por caixa/normalização Unicode (`Modelos/a` × `modelos/a`).
* Extensões permitidas: `.json .md .txt .jpg .jpeg .png .pt .xml .bin .onnx .yaml .yml`. Extensões de código
  (`.py .dll .exe .sh .bat .ps1 .js` etc.) são `codigo_executavel`. Textos e imagens que começam com
  assinatura de executável/script, ou imagens cujo cabeçalho não confere com a extensão, são recusados.
  Pesos (`.pt .bin .onnx`) **não** passam por essa checagem de assinatura (bytes arbitrários dão falso
  positivo).

### 12.7 Fora do subconjunto implementado (explícito)

Não cabem no `protocolo: 1` e o validador não os aceita: **pose estruturada** (caixas/pontos), **atributos
derivados** de outros atributos ou de vários modelos, **térmico/fusão de duas fontes** (o valor
`entrada.fonte: termica` é aceito apenas como fonte única de um recorte, sem calibração nem fusão), pacotes
com código, pacotes de terceiros. Cada um depende de decisão própria e de revisão do contrato.

## 13. Instalação local (implementada; sem painel)

`src/jutta_ped/especialistas/instalador.py`. Estado em arquivos sob `--raiz` (padrão
`runtime/especialistas/`, ignorada pelo git): `registro.json` (+`.bak`), `pacotes/<id>/<versão>/`,
`staging/`, `pendentes/`, `quarentena/`, `operacoes.jsonl`, `.lock`.

```
python -m src.jutta_ped.especialistas instalar <pasta-ou-zip>      # valida, extrai isolado, registra (fica INATIVO)
python -m src.jutta_ped.especialistas ativar <id> <versao>         # confere integridade e torna ativa
python -m src.jutta_ped.especialistas reverter <id>                # volta à última versão anterior válida
python -m src.jutta_ped.especialistas remover <id> <versao>        # só versão inativa
python -m src.jutta_ped.especialistas listar | recuperar           # estado | concluir/desfazer operações interrompidas
```

Regras (obrigações do instalador):

1. **Só entra o que o validador aprovou antes e depois da extração**, com os mesmos hashes. Reprovado: nada é
   gravado.
2. **Extração isolada:** em `staging/<token>/`, sem sobrescrever, só dentro do destino, com os limites da
   seção 12.6 reaplicados sobre os bytes realmente escritos. Depois uma troca atômica de pasta para
   `pacotes/` e gravação atômica do registro (arquivo temporário + `replace`, com cópia `.bak`).
3. **Versão imutável:** mesmo `id`+`versão` com os mesmos pesos é idempotente; com pesos diferentes é recusado
   (`versao_imutavel`). Para mudar pesos, publique outra versão.
4. **Recuperação após queda** (`recuperar`, também executada antes de cada instalação): descarta `staging`;
   instalação interrompida depois de mover mas antes de registrar é **concluída** se o conteúdo ainda confere,
   ou vai para `quarentena/`; diretório fora do registro vai para `quarentena/` (nunca é apagado); versão
   registrada sem arquivos fica `ausente`, e se era a ativa volta à última válida do histórico; registro
   ilegível usa o `.bak` e, se ambos falharem, é reconstruído a partir de `pacotes/` (sem versão ativa).
5. **Ativação** confere a integridade na hora (hashes dos pesos); falhou: a versão fica `corrompida` e a ativa
   não muda. O histórico guarda as últimas 10 ativações. **Reverter** pula versões anteriores corrompidas e
   mantém a versão atual instalada, inativa.
6. **Exclusão mútua** por `.lock` (pid); lock de processo morto é ignorado.

Códigos de saída da CLI de instalação: `0` ok · `1` uso/E-S · `10` reprovado pelo validador · `11` conflito de
versão/destino · `12` operação em andamento · `13` falha de E/S · `14` integridade · `15` estado (versão
inexistente, sem anterior válida, registro ilegível) · `16` versão em uso.

**Limites desta entrega:** não carrega modelos nem executa nada do pacote; não integra o painel (upload e
telas); não vincula receitas; não autentica autoria; o host consulta `caminho_ativo(id)` para saber qual pasta
usar. Quando houver banco para o registro (painel), este arquivo passa a ser fonte de reconciliação.

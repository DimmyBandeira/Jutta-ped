# Protocolo dos nós de execução

**Versão do documento:** 0.1 (rascunho) · **Protocolo de nós:** `nos/1` · **Estado:** proposta para
revisão. Nada aqui está implementado.

Convenções como no [manual do especialista](manual_especialista.md): **DEVE / NÃO DEVE** = obrigação;
**(proposta)** = ainda em avaliação. Este documento NÃO redefine o formato de pedido, resultado, atributos
nem códigos de erro: usa os da [seção 4 do manual](manual_especialista.md#4-execução-pedido-e-resultado).
Aqui está só o que é próprio do transporte e da gestão de capacidade.

---

## 1. Papéis

* **Mãe** (sistema-mãe): captura, mestre, rastreamento, regras, alertas, painel. Decide o que é enviado,
  para onde e o que é aceito de volta.
* **Nó**: servidor com GPU numa rede privada. Executa especialistas declarativos instalados nele e
  devolve resultados. **Sem estado entre pedidos**, não grava imagens em disco, não decide alerta.
* O nó executa **o mesmo contrato** do executor local. Para a mãe, local e nó são destinos de execução.

## 2. Decisões fechadas e alternativas em avaliação

| Tema | Situação |
|---|---|
| Quem inicia a conexão | **Fechado:** o nó conecta na mãe (funciona atrás de NAT/firewall; a mãe sabe quando cai). |
| Local primeiro; remoto só no transbordo | **Fechado.** |
| Pedido lógico ≠ tentativa; um resultado válido por pedido | **Fechado** (seção 6). |
| Só durações relativas nas mensagens | **Fechado** (seção 5). |
| Resultado fora do prazo, duplicado ou de geração antiga é descartado | **Fechado.** |
| Falta de resultado vira indeterminado | **Fechado.** |
| Transporte: WebSocket com quadros binários | **(proposta)**; alternativas na seção 11. |
| Autenticação por token de nó emitido pelo admin | **(proposta)** — em rede privada dispensa TLS mútuo; TLS opcional. |
| Controle de fluxo por créditos | **(proposta)** (seção 7). |
| Limiares numéricos (espera local, prazos, saúde) | **Em aberto:** dependem de medição (seção 8). |

## 3. Conexão e autenticação (proposta)

1. O admin cadastra o nó na mãe e recebe um **token** (mostrado uma vez, guardado como hash). Pode
   revogar a qualquer momento.
2. O nó abre `GET /nos/ws` (WebSocket) com `Authorization: Bearer <token>`. A mãe recusa token
   desconhecido ou revogado e derruba a conexão ao revogar.
3. Cada conexão recebe da mãe uma **`sessao_no`** (inteiro crescente por nó). Tudo o que o nó enviar
   DEVE repeti-la. Resultado com `sessao_no` diferente da atual é descartado (resposta de uma conexão
   anterior).
4. O nó NÃO DEVE aceitar conexões de entrada para executar pedidos. Só fala pela conexão que abriu.

## 4. Mensagens

Cada mensagem tem um envelope JSON e, quando há imagens, um bloco binário.

```json
{ "nos": 1, "tipo": "…", "msg_id": "m-881", "sessao_no": 7, "corpo": { … } }
```

Quadro WebSocket: `[4 bytes: tamanho do JSON][JSON UTF-8][bytes das imagens, concatenados]`. No
`corpo` de um lote, cada pedido traz `{"offset":0,"tamanho":18234}` para localizar sua imagem no bloco.

| Tipo | Sentido | Corpo |
|---|---|---|
| `ola` | nó → mãe | Apresentação (seção 4.1). |
| `aceito` / `recusado` | mãe → nó | Resultado da admissão. |
| `creditos` | nó → mãe | `{"adicionar": 16}` (seção 7). |
| `lote` | mãe → nó | `{"pedidos":[ pedido…]}` (pedido = manual §4.1, mais `offset`/`tamanho`). |
| `resultados` | nó → mãe | `{"resultados":[ resultado…]}` (manual §4.2). |
| `cancelar` | mãe → nó | `{"pedido_id":"p-000123"}` — pedido já não interessa. |
| `metricas` | nó → mãe | Amostra periódica (seção 9). |
| `ping` / `pong` | ambos | Vitalidade (a cada 5 s; 3 perdidos = conexão morta). |

### 4.1 Apresentação (`ola`)

```json
{ "nos": 1, "tipo": "ola", "corpo": {
  "no_id": "gpu-01", "agente_versao": "0.1.0", "protocolos_nos": [1],
  "dispositivos": [ { "tipo": "cuda", "nome": "RTX 3060", "memoria_mb": 12288 } ],
  "especialistas": [ { "id": "br.jutta.bebe-no-colo", "versao": "1.0.0",
                       "pesos_sha256": "ab12…", "lote_max": 8 } ],
  "capacidade": { "concorrencia": 2 } } }
```

A mãe responde `aceito` com a lista de especialistas **habilitados** para esse nó (id + versão +
`pesos_sha256` iguais aos que ela espera) ou `recusado` com motivo (`token`, `protocolo`,
`nenhum_especialista_compativel`). Especialista presente no nó com versão ou hash diferente **não é
habilitado** e é mostrado como divergência no painel. A mãe NÃO DEVE enviar pedidos de especialista não
habilitado.

## 5. Tempo e orçamento (um só orçamento)

Três coisas diferentes, que não se misturam:

| Conceito | Definição | Quem define |
|---|---|---|
| **Idade máxima da observação** | Quanto tempo depois do quadro um resultado ainda serve à regra | Receita / `validade_ms` do atributo (manual §3.4) |
| **Orçamento do pedido** (`restante_ms`) | O que sobra da idade máxima quando o pedido sai: `idade_max − idade_na_emissão − margem` | A mãe, na emissão |
| **Validade do atributo** | Por quanto tempo o valor, depois de aceito, vale para as regras | Manifesto, reduzível pela receita |

O **mesmo** orçamento é consumido, em ordem, por: fila local, codificação, rede de ida, fila do nó,
inferência, rede de volta e decodificação. Nenhuma etapa tem prazo próprio extra.

* O pedido sai com `restante_ms`. A mãe DEVE descontar o tempo que passar antes de enviar uma nova
  tentativa: o orçamento da tentativa 2 é o que **sobrou**, nunca o inicial.
* O nó mede só durações próprias (`fila_ms`, `inferencia_ms`) no seu relógio. A mãe mede o tempo total
  no dela. A rede é estimada como `total − fila_ms − inferencia_ms`. **Não** se exige sincronismo de
  relógio entre as máquinas e nenhuma mensagem leva horário absoluto.
* O nó DEVE responder `E_PRAZO_ESGOTADO` sem processar quando `restante_ms` (descontada a espera dele)
  não comporta o `custo.ms_por_recorte` do especialista. A mãe, por sua vez, descarta qualquer resultado
  que chegar depois do seu próprio prazo, mesmo que o nó ache que estava no tempo.

## 6. Pedido lógico, tentativa e duplicidade

* O **pedido lógico** (`pedido_id`) representa "quero estes atributos deste recorte". Cada envio é uma
  **tentativa** (`tentativa` 1, 2…). O contrato admite trabalho duplicado, não efeito duplicado.
* **Remoto no máximo uma vez:** a mãe envia a um nó **no máximo uma tentativa** por pedido lógico. Pode,
  além disso, executar localmente (tentativa de outro destino) se o orçamento restante comportar o
  custo local.
* **Um resultado válido por pedido.** O primeiro resultado aceito (dentro do prazo, com `sessao_no`,
  `geracao`, `trilha_id` e `quadro_id` corretos e `quadro_id` maior que o último aceito para aquele
  `(trilha_id, atributo)`) vence. Qualquer outro resultado do mesmo pedido é descartado e contado.
* Queda do nó **não cancela** a execução dele: o nó pode terminar um pedido depois que a conexão
  caiu. Esse resultado nunca é entregue: a conexão morta não existe mais, e a nova `sessao_no`
  descartaria o que viesse.
* Reenvio do mesmo pedido após reconexão é uma **nova tentativa** com orçamento reduzido, nunca um
  pedido novo com prazo cheio.

## 7. Admissão de carga e controle de fluxo (proposta)

* **Créditos.** O nó anuncia quantos pedidos aceita agora (`creditos`). A mãe NÃO DEVE ter mais pedidos
  em voo para ele do que os créditos concedidos. Cada resultado devolvido libera o crédito
  correspondente; o nó pode reforçar com `creditos`.
* **Sem crédito = sem transbordo.** Faltando crédito, o pedido segue na fila local. O remoto nunca
  forma fila na mãe.
* **Fila local limitada.** Se a fila local também enche, descarta-se o recorte **mais antigo** (vale o
  mais recente); cada descarte é contado e o atributo fica indeterminado (`prazo_esgotado`).
* **Quando transbordar** (proposta): só quando a espera estimada local passar de um limite. Valor
  inicial a medir (hipótese: 150 ms), sem ser contrato.
* **Teto de banda por nó**, configurável pelo admin, para o transbordo não saturar a VLAN.

## 8. Falha e retomada

| Situação | Comportamento da mãe |
|---|---|
| Conexão cai com pedidos em voo | Marca-os "perdidos no remoto". Para cada um, executa localmente **se** `restante_ms` ≥ custo local estimado; senão o atributo fica indeterminado (`prazo_esgotado`). Não renova prazo. |
| Nó volta | Nova conexão, novo `ola`, nova `sessao_no`. Nada do que veio antes é aceito. |
| Reconexões repetidas | Backoff exponencial no nó (1 s → 30 s). Disjuntor na mãe: após N falhas/timeouts seguidos o nó sai da rotação e volta por **sonda** (um pedido de teste que não afeta câmera). |
| Nó responde `E_SOBRECARGA` | Pedido segue local; mãe reduz o uso do nó por um tempo. |
| Nó responde `E_ESPECIALISTA_DESCONHECIDO` / `E_VERSAO_INCOMPATIVEL` | Especialista sai da lista habilitada desse nó até novo `ola`. |
| Câmera reinicia a sessão (`geracao` +1) | Pedidos em voo da geração anterior são cancelados (`cancelar`); resultados atrasados são descartados pela `geracao`. |
| Mãe reinicia | Todos os nós reconectam; nada em voo sobrevive. |

Limiares (N falhas, janela, tempo de disjuntor) ficam **em aberto** e são definidos com medição.

## 9. Métricas

O nó envia `metricas` a cada 5 s; a mãe soma as suas e guarda histórico agregado por minuto.

| Origem | Campos |
|---|---|
| Nó | `gpu_uso_pct`, `gpu_memoria_mb`, `fila_pedidos`, `pedidos_executados`, `inferencia_ms_p50/p95`, `erros_por_codigo` |
| Mãe, por nó | `bytes_enviados`, `bytes_recebidos`, `mbit_s`, `pedidos_s`, `rtt_total_ms_p50/p95`, `rede_ms_estimada_p50/p95`, `timeouts_pct`, `retornos_ao_local_pct`, `tardios_pct`, `duplicados_pct`, `descartes`, `breaker_aberto` |
| Mãe, geral | `espera_fila_local_ms`, `transbordo_pct`, `uso_cpu_pct`, `uso_gpu_local_pct`, `idade_observacao_ms_p95` por receita, `indeterminados_por_motivo`, `divergencia_versao` |

**Selo de saúde** (saudável / atenção / crítico / **sem dados**): NÃO DEVE depender só de conectividade.
Considera ao menos: idade das observações usadas nas regras, espera de fila, latência completa p95,
taxa de timeout, descartes e uso de cache. Os limites numéricos dependem de medição e da tolerância de
cada receita: **em aberto**.

## 10. Cenários

Mensagens abreviadas (`…` omite campos já definidos). `tK` indica tempo na mãe, em ms, só para ler.

**C1 — Sucesso.** t0: mãe emite `lote` com `p-1` (`tentativa 1`, `restante_ms 400`, `geracao 12`). t52:
`resultados` de `p-1` com `bebe_no_colo: definido/sim`, `tempos {fila 4, inferencia 18}`. Mãe: total 52
ms, rede estimada 30 ms; aceita; descarta nada.

**C2 — Indeterminado.** Confiança 0,25 entre os limiares: o nó devolve `estado:"indeterminado",
motivo:"baixa_confianca"`. A regra trata como indeterminado, não como "não".

**C3 — Pacote incompatível.** `ola` traz `pesos_sha256` diferente do esperado. Mãe: `aceito` sem esse
especialista e painel mostra "divergência de versão". Nenhum pedido dele é enviado ao nó; segue local.

**C4 — Prazo expirado.** `p-2` sai com `restante_ms 120`; o nó está ocupado e levaria 200 ms. Nó
responde `E_PRAZO_ESGOTADO` sem processar. A mãe, sem tempo para o local, marca o atributo
`indeterminado/prazo_esgotado`. Nenhum retry renova o prazo.

**C5 — Queda do nó.** t0: `p-3` enviado (restante 400). t130: conexão cai. Mãe: perdido no remoto;
custo local 40 ms ≤ restante (270) → executa local, resultado em t190. Se o restante fosse 30 ms, o
atributo ficaria indeterminado. O nó, ao reconectar, tem `sessao_no` nova: se ainda enviar a resposta
de `p-3`, é descartada.

**C6 — Tentativa local concorrente.** `p-4`: nó lento. Em t150 a mãe começa execução local (tentativa
de outro destino). t170 o local termina e é aceito. t210 chega a resposta do nó: `pedido_id` já
resolvido → descartada e contada em `duplicados_pct`. Nenhuma das duas estende o prazo.

**C7 — Resposta antiga após reinício de sessão.** A câmera reinicia (`geracao` 12 → 13). `p-5` (geração
12) ainda está no nó. Mãe envia `cancelar p-5`. O nó termina mesmo assim e devolve com `geracao 12`; a
mãe descarta (geração atual 13). A `trilha_id 41` da geração 13 é outra pessoa; o resultado antigo NÃO
a contamina.

**C8 — Resultado mais antigo sobre atributo mais novo.** Aceitos para `(trilha 41, bebe_no_colo)` o
`quadro_id 90871`. Chega resultado do `quadro_id 90860` (tardio, mesma geração). Descartado: o
`quadro_id` não é maior que o último aceito.

**C9 — Falta de crédito.** Nó com 0 crédito; o transbordo estava indicado. A mãe NÃO enfileira para o
nó: o pedido continua na fila local (ou é descartado se ela estiver cheia, com contagem).

**C10 — Token revogado.** Admin revoga; a mãe fecha a conexão. O nó reconecta e recebe `recusado`
(`token`). Pedidos em voo seguem o C5.

## 11. Transporte: alternativas (decisão em aberto)

| Opção | A favor | Contra |
|---|---|---|
| **WebSocket** (proposta) | Já no FastAPI; sem dependência nova; quadros binários; funciona bem no Windows e offline | Sem controle de fluxo nativo (por isso os créditos); formato de mensagem próprio |
| gRPC | Contrato tipado, streaming bidirecional, fluxo e prazos nativos | Protobuf e geração de código; mais dependências; operação offline/Windows mais pesada |
| Fila (Redis, ZeroMQ) | Desacopla produtor e consumidor | Novo componente a operar; semântica de prazo e descarte tem de ser reinventada |

Critério de escolha (a medir antes de fechar): tamanho das mensagens, latência p95 com lote de 8,
comportamento sob perda de rede, esforço de manutenção para 2–3 nós, dependências disponíveis offline.

## 12. Evolução do protocolo

* `nos` é inteiro. O nó anuncia `protocolos_nos` que suporta; a mãe escolhe o maior em comum ou
  recusa (`protocolo`).
* Campo novo opcional: pode entrar sem mudar `nos`. Campo obrigatório novo ou mudança de significado:
  novo número. Campos desconhecidos em mensagens recebidas DEVEM ser ignorados (compatibilidade para
  frente), exceto no manifesto, que é estrito (manual §3).
* O contrato do pedido/resultado evolui com o `protocolo` do especialista, não com `nos`.

## 13. Questões abertas (com impacto)

| # | Questão | Impacto se não fechar |
|---|---|---|
| 1 | Escolha do transporte | Define esforço e dependências; bloqueia a etapa 08. |
| 2 | Limiares: transbordar, breaker, selo de saúde | Sem medição, risco de alternar entre local e remoto sem ganho. |
| 3 | Margem usada ao calcular `restante_ms` | Margem alta desperdiça nó; baixa produz resposta tardia. |
| 4 | Formato do recorte (JPEG vs bruto, qualidade) | Altera banda e pode afetar a precisão do especialista; medir. |
| 5 | TLS dentro da VLAN | Hoje opcional; decidir pela política de rede. |
| 6 | Distribuição de pacotes para o nó (mãe empurra vs instalação manual) | Define se o nó precisa de canal de instalação e de verificação própria. |
| 7 | Especialistas que não cabem no contrato atual (pose, térmico, derivados) | Não podem ser executados em nó até o manual §11 itens 2–4 fecharem. |

## 14. Ambiguidades registradas para resolução posterior

Identificadas ao consolidar o contrato do especialista (lote 1). Nenhuma foi resolvida aqui: dependem de
decisão e de medição na etapa 08 e **não** são pré-requisito do validador de pacotes. Enquanto não forem
fechadas, nenhum nó deve ser implementado.

| # | Ambiguidade | Onde aparece | Por que importa |
|---|---|---|---|
| A1 | **Reenvio remoto.** A seção 6 diz "no máximo uma tentativa remota por pedido lógico" e também que o reenvio após reconexão é "nova tentativa com orçamento reduzido". Não está definido se essa nova tentativa pode ir de novo a um nó (o mesmo ou outro) ou só ao executor local. | §6, §8 (queda do nó) | Define o limite de trabalho duplicado e se uma queda pode multiplicar carga sobre outro nó. Hoje as duas leituras são compatíveis com o texto. |
| A2 | **Créditos.** Faltam: valor inicial no `aceito`; se o crédito é por nó ou por especialista; devolução de crédito quando o resultado é descartado (atrasado, duplicado, geração antiga) ou o pedido é cancelado; tratamento de créditos após reconexão (zeram?) e de resultado que chega sem crédito correspondente. | §7 | Crédito mal contado trava o transbordo (vazamento) ou sobrecarrega o nó (excesso). |
| A3 | **Validade contada desde a captura.** O manifesto define `validade_ms` a partir do quadro observado (manual §3.4, §4.4), mas as mensagens carregam só durações (`idade_na_emissao_ms`, `restante_ms`) medidas pela mãe. Não está definido: quando o relógio da validade começa se o quadro ficou na fila local antes de emitir o pedido; se a validade é conferida na chegada do resultado ou no uso pela regra; como a validade se relaciona com `restante_ms` (o resultado pode chegar dentro do prazo e já vir com pouca validade útil). | manual §4.4, protocolo §5 | Determina se um resultado aceito pode ser usado por regras logo em seguida e o que significa "idade máxima da observação" por receita. |
| A4 | **Cancelamento e crédito.** `cancelar` é melhor esforço; o nó pode terminar assim mesmo. Não está definido se o crédito do pedido cancelado é devolvido na hora ou só quando o nó confirmar. | §4, §6, §7 | Interage com A2. |

Critério de saída: cada item vira regra escrita com cenário no capítulo 10 (mensagens completas) antes de
qualquer implementação de rede.

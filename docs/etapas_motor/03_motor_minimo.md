# Etapa 03 — Motor mínimo em sombra (câmera → crop → especialista → receita)

Estado: **em andamento** (caminho genérico implementado e testado com backend determinístico e uma fumaça com modelo real; a migração da pediatria **não** começou). Registro do lote pedido em [`retorno_claude_motor_minimo.md`](../retorno_claude_motor_minimo.md).

## Fronteiras

| Pertence à mãe (`src/jutta_ped/motor`) | Pertence ao especialista (pacote) | Fica fora deste lote |
|---|---|---|
| Observação (câmera, geração, quadro, trilha, instante de captura, bbox), recorte e transformação, orçamento desde a captura, cache com validade, receita, evento de sombra | Atributos (tri/número/categoria) a partir de uma entrada declarada; nada de câmera, trilha, persistência ou alerta | Contexto espacial/companhia da pediatria, alerta operacional, nós remotos, fila compartilhada (etapa 04), repositório externo, alvo `quadro` inteiro |

Regras de projeto: o host **não tem condicionais por nome de especialista**; um pacote compatível entra por manifesto + receita. O motor só despacha especialistas cujo `entrada.alvo` seja `recorte` da classe mestre declarada e cuja `fonte` esteja disponível (hoje só `visivel`); outras combinações são **recusadas na configuração**, com mensagem.

## Arquivos

| Arquivo | Papel |
|---|---|
| `src/jutta_ped/motor/observacao.py` | `Observacao`, `Recorte` (origem, bbox no quadro e no crop, `para_quadro`/`para_recorte`), `ajustar_bbox`, `extrair_recorte` (bbox ajustada ao quadro; vazio/NaN/inf/invertida → descartado) |
| `src/jutta_ped/motor/receita.py` | Formato da receita, `validar_receita` contra os contratos dos especialistas, `combinar` (`todas`/`alguma`, indeterminado explícito), `hash_config` |
| `src/jutta_ped/motor/motor.py` | `MotorSombra` (habilitação explícita, associação câmera→receita versionada, despacho ao `Executor`, cache por (câmera, trilha, especialista, atributo), evento de sombra) e `RastroJsonl` |
| `src/jutta_ped/motor/adaptador.py` | `proxima_geracao()` e `observacoes_de_deteccoes()` (converte as detecções do runtime em observações) |
| `src/jutta_ped/especialistas/executor.py` | **Novo** `Executor.contrato(id)`: versão, identidade, entrada, validade por atributo e faixas de parâmetros do ativo |
| `src/jutta_ped/service/runtime.py` | Gancho opcional: `SessionStartConfig.motor_sombra` (padrão `None`), `_alimentar_sombra` chamado em `_process_frame`; erro do motor é contado e nunca afeta a análise real |
| `tests/test_motor_sombra.py`, `tests/test_motor_sombra_runtime.py` | Contrato, isolamento, validade, ativação/reversão, generalidade, laço real da sessão, fumaça com modelo real |

## Garantias implementadas (cada uma tem teste)

1. **Desabilitado por padrão**: sem `habilitado=True` não recorta, não executa e não emite. Sem `motor_sombra` na configuração da sessão o laço é idêntico ao anterior.
2. **Nunca alerta**: todo evento tem `modo="sombra"`, `alerta_real=False`, vai para o `destino` próprio (`RastroJsonl`), não para `events.jsonl` da sessão.
3. **Identidade no evento**: câmera, geração, quadro, trilha, instante de captura e avaliação, receita (id, versão, `config_hash`), especialistas (id, versão, identidade do pacote), condições com idade e quadro do atributo.
4. **Orçamento desde a captura**: `restante_ms = orcamento_ms − (agora − capturado_em)`; vencido antes de enviar não chega ao modelo; resultado que volta fora do prazo é descartado e **não entra no cache**. O instante de captura do laço real é tomado logo após `capture.read()`.
5. **Validade**: vale o menor entre a validade do atributo no manifesto e a da condição da receita (a receita só pode apertar, nunca ampliar). Ausente, expirado, erro, atrasado, recorte vazio/pequeno ou indeterminado do especialista → **indeterminado** na receita, nunca "nao".
6. **Sessão e ordem**: geração anterior à vigente é descartada; geração nova limpa a câmera (a mesma `trilha_id` não herda nada); câmeras não compartilham cache; resultado de quadro/captura mais antigo não substitui o mais novo; observação de outra câmera/geração ou do futuro é rejeitada.
7. **Versões**: ativar ou reverter muda (versão, identidade) → atributos daquele especialista são descartados e a receita passa a usar só a nova; `versoes_aceitas` da receita transforma versão fora da lista em indeterminado (`versao_nao_aceita`).
8. **Configuração atômica**: receita inválida (atributo inexistente, parâmetro fora da faixa, especialista sem ativo, alvo `quadro`, fonte indisponível, validade ampliada…) levanta `ValueError` e **mantém a configuração anterior**.

## Evidências — três coisas diferentes

| Verificação | O que prova | O que **não** prova |
|---|---|---|
| Contrato (`test_motor_sombra.py`, backend falso, dois pacotes de conformidade + um terceiro entrando só por manifesto/receita) | Fluxo completo, isolamento, validade, atraso, indeterminado, ativação/reversão, ausência de domínio no host (o teste varre o código do motor) | Qualidade de qualquer modelo |
| Fumaça com modelo real (`test_fumaca_modelo_real_pelo_motor_ate_a_receita`, pula sem `src/models/Bebe-no-colo.pt` e vídeo local) | O trajeto roda com um `.pt` real via Ultralytics, sem erro, com evento bem-formado | Que o veredito esteja certo; usa um quadro inteiro como "recorte" |
| Comparação de qualidade da migração | **Não feita.** Depende da referência humana da etapa 02 | — |

## Adaptação pediátrica: o que falta (não inventado)

A pediatria atual decide por companhia entre criança e adulto, persistência e vínculo entre trilhas — contexto **espacial e temporal** que fica na mãe. Um pacote "bebê no colo" produz um atributo por pessoa; **não** substitui o classificador criança/adulto nem a regra de companhia. Para migrar falta: (a) um especialista declarativo de papel (criança/adulto) com pesos próprios e `classe_mestre` compatível; (b) as regras de companhia/persistência como receita/regra do host sobre múltiplas trilhas (a receita atual avalia **uma observação por vez**, sem relações entre trilhas); (c) política explícita de indeterminado para alertas já ativos; (d) comparação em sombra contra a etapa 02 já com a referência humana confirmada.

## Limites conhecidos

* **Decisão de projeto sobre erro novo × cache válido:** um atributo ainda dentro da validade continua valendo mesmo que o quadro seguinte falhe (erro, atraso, recorte vazio); o motivo do erro fica em `erros` do evento. Só expira por validade, troca de versão/receita, perda do ativo ou nova geração. Declarar `validade_ms` curta **não** equivale a invalidar na primeira falha (são políticas diferentes); a política `invalidar_ao_errar` ainda não existe e precisa ser implementada antes de ser exigida por uma receita.
* Cache podado só 60 s após o vencimento (mantém o motivo `expirado` logo depois); relações entre trilhas e execução paralela não existem.
* Revisão independente (subagente somente leitura) apontou 14 problemas; os reais foram corrigidos e cobertos por teste (receita malformada → `ValueError`, `igual` validado contra o tipo do atributo, observações malformadas/NaN/trilha repetida, orçamento relido por vínculo, troca de receita limpa o cache, perda do ativo descarta atributos, versão que muda alvo/fonte não derruba o laço, falha do rastro não derruba o laço, poda do cache).
* `MotorSombra` puro é síncrono; para ao vivo use `DespachoSombra` (uma thread de sombra para todas as câmeras, fila de 1 por câmera, sem justiça fina entre câmeras). Fila compartilhada sob carga = etapa 04.
* Despacho só de `recorte`; alvo `quadro` inteiro e fonte térmica recusados na configuração.
* A receita combina condições de **uma** trilha; relações entre trilhas e persistência temporal ainda não existem.
* O cache de atributos é em memória e por processo; reiniciar o processo zera tudo (comportamento desejado: indeterminado).
* `config_hash` do evento identifica receita+associação; o hash dos pesos está em `especialistas[].identidade`.

## Correções da revisão do Codex (`revisao_motor_sombra.md`)

| Achado | Correção | Teste |
|---|---|---|
| 1. Troca de pacote incompatível dava "nao" (tri → número com o mesmo nome) | A cada mudança de (versão, identidade) o motor **revalida** a parte da receita que usa o especialista (`validar_vinculo`: tipo e `igual`, categorias, atributo, parâmetros, alvo, fonte; resultado memorizado por identidade). Incompatível → atributos do especialista descartados, condição **indeterminada** com motivo `E_CONTRATO_INCOMPATIVEL` e as razões em `incompatibilidades` do evento. Reverter volta a funcionar sem reconfigurar | `test_troca_de_tri_para_numero…`, remoção de atributo/categoria, alvo `quadro`, parâmetro compatível |
| 2. Sombra síncrona atrasa a publicação operacional | `DespachoSombra` (`motor/despacho.py`): o laço só **enfileira** (não bloqueante); thread própria executa; fila limitada por câmera com descarte do mais antigo. O runtime **recusa** motor síncrono em fonte ao vivo (`network`/`device`) e o conta; síncrono só em arquivo/ensaio isolado. O lock global do motor agora só segura a thread da sombra | especialista lento e destino travado não bloqueiam o enfileirar (20 envios < 0,5 s); laço de 30 quadros com destino travado 5 s termina em < 2 s; recusa ao vivo × aceite em arquivo |
| 3. Idade do quadro ignorava o buffer da captura ao vivo | `LatestFrameCapture` guarda o instante monotônico **local de recepção** do quadro (`ultimo_recebido_em`); o laço o usa como `capturado_em` (fallback: depois do `read()`, para fontes de arquivo). Não é horário físico da câmera | recepção anterior ao consumo; laço usa a recepção, não o consumo |

**Política de cache v1 = `manter_ate_expirar`** (campo `politica_cache` do evento): o último atributo válido segue valendo até expirar, **contando a idade desde a captura original** (erro não renova). Resultado novo explicitamente indeterminado **substitui** o anterior. Erro de contrato/identidade/integridade **invalida** o cache afetado. Cada condição informa `reutilizado`, `idade_ms`, `quadro_do_atributo` e `falha_recente`. Diminuir `validade_ms` **não** equivale a invalidar na primeira falha; uma política `invalidar_ao_errar` por receita ainda não existe e precisa ser implementada antes de ser exigida. Em aberto: como as regras tratam alerta **já ativo** quando a observação fica indeterminada.

## Próximo lote (executado em [03b](03b_cena_regras.md); a lista abaixo é o plano original)

Ordem: mestre/contexto genéricos → regras entre trilhas → pacote de papel → comparação. Sem nós, sem painel de habilitação, sem ampliar a bateria.

1. **Mestre e contexto de cena genéricos na mãe.** Hoje `person_detections` vem de `PediatriaService.process_frame` (que roda também o classificador e a análise de companhia). Extrair, reutilizando `modulo/pediatria/crop_pipeline` e o detector/rastreador existentes (sem carregar modelos duplicados), uma interface `Cena` por quadro: todas as trilhas (bbox, classe mestre, confiança, idade da trilha) + instante de recepção. O serviço pediátrico passa a consumir essa mesma `Cena`; critério: saída pediátrica idêntica nos testes existentes.
2. **Regras entre trilhas e persistência na mãe.** Estender receita/regra para relações geométricas entre trilhas (sobreposição/distância declaradas), agregação temporal (condição mantida por N s, com ausente/indeterminado explícitos) e **política para alerta ativo** (manter, rebaixar ou indeterminar quando faltar observação). Companhia continua na regra do host, nunca dentro do pacote de papel.
3. **Pacote de papel (criança/adulto).** Verificar se o classificador atual cabe no contrato publicado (entrada, formato, classes, `classe_mestre`); documentar incompatibilidades **antes** de qualquer treino ou modelo novo. Só então empacotar e escrever a receita pediátrica sobre (1) e (2).
4. **Generalidade e comparação.** Repetir o fluxo com um pacote **não pediátrico** de classe mestre diferente de `person`, sem condicional de domínio no núcleo. Comparar a migração em sombra com a referência da etapa 02 (que continua aberta) — só depois, e sem declarar equivalência antes disso.

Fora deste lote: execução compartilhada sob carga (etapa 04); a separação de workspaces só estará demonstrada quando um pacote produzido **fora** entrar pelo contrato sem tocar o host.

## Como habilitar em sombra

```python
motor = MotorSombra(Executor(Instalador(raiz)), habilitado=True, destino=RastroJsonl("reports/sombra/rastro.jsonl"))
motor.configurar([receita_dict], {"cam1": {"receita": "r.id", "versao": "1"}})
SessionStartConfig(source=..., camera_id="cam1", motor_sombra=motor)
```

Em **câmera ao vivo** use `motor_sombra=DespachoSombra(motor)`; um `MotorSombra` síncrono só é aceito em fonte de arquivo/ensaio isolado (o runtime recusa nas demais). Ainda **não há** chave no painel/API: a habilitação é por código (explícita, de propósito) até a migração. Não há benchmark de impacto temporal em câmera operacional: a garantia é estrutural (o laço não espera), testada com especialista e destino lentos.

# Bateria de referência — procedimento

Etapa 02 do [plano do motor](../plano_motor_plugins.md). Mede o **caminho atual** (sessões de IA com modelos por câmera, `PediatriaSessionManager`)
antes de migrar a pediatria, para que a migração seja comparada contra números e não contra impressão. Ferramenta:
`tools/bateria_referencia.py` (módulos em `tools/bateria/`). É **diagnóstico**: não roda na suíte de testes (a lógica pura tem testes em
`tests/test_bateria_referencia.py`).

**Os quadros de revisão (JPG) contêm pessoas de câmeras reais e NÃO são versionados** (`.gitignore`); regenere com `episodios`. Resultados de cada rodada ficam em `docs/referencia/<data>/` (`RELATORIO.md` + JSON brutos). O relatório só transcreve dados; a interpretação
fica em `leitura.md` da mesma pasta.

## 1. Como repetir

Pré-requisitos: `pip install -r requirements-mvp.txt -r requirements-dev.txt` (inclui `nvidia-ml-py` para GPU NVIDIA), os modelos de
`src/models/` e os vídeos de `videos/`. **Feche outros programas pesados**: a bateria usa a máquina inteira e qualquer carga paralela contamina
as medições. Anote a energia (tomada/bateria) e o plano de energia do Windows.

```
python tools/bateria_referencia.py ambiente   --saida docs/referencia/AAAA-MM-DD
python tools/bateria_referencia.py matriz     --saida docs/referencia/AAAA-MM-DD      # ~15 min
python tools/bateria_referencia.py perfil     --saida docs/referencia/AAAA-MM-DD
python tools/bateria_referencia.py escala     --saida docs/referencia/AAAA-MM-DD      # ~8 min
python tools/bateria_referencia.py episodios  --saida docs/referencia/AAAA-MM-DD      # analisa 3 vídeos; cobertura + pacote de revisão
python tools/bateria_referencia.py reavaliar  --saida docs/referencia/AAAA-MM-DD      # recalcula sem inferência (após confirmar)
python tools/bateria_referencia.py relatorio  --saida docs/referencia/AAAA-MM-DD
```

Opções úteis: `matriz --repeticoes 3` (variância entre execuções), `--cameras 1 2 4`, `--analysis-fps 0 5`, `--extra-cuda 1 4`,
`--aquecimento 15 --estavel 45`, `--videos <arquivos>` (padrão: os dois DVR de 19/06 + os dois corredores).

## 2. O que cada passo registra

| Passo | Registra |
|---|---|
| `ambiente` | SO, CPU, RAM, GPUs (NVML e OpenVINO), versões de bibliotecas, commit e arquivos modificados, **hash dos modelos** (arquivo: SHA-256; pasta OpenVINO: `jutta-dir-v1`), **hash e propriedades dos vídeos**, configuração padrão da sessão |
| `matriz` | Um ensaio por configuração, **cada um em processo novo**: 1, 2 e 4 câmeras × `analysis_fps` 0 (sem limite) e 5 (padrão atual) no dispositivo `auto` (OpenVINO em CPU), mais 1 e 4 câmeras com `cuda` (PyTorch/GPU) a 5 fps |
| `perfil` | Uma câmera, sem e com cProfile: fatia do tempo em funções Python × chamadas nativas, por pacote |
| `escala` | 4 câmeras como **threads** × 4 **processos** de 1 câmera, janelas alinhadas; veredito por regra pré-registrada |
| `episodios` | Linha do tempo de alertas dos dois vídeos DVR, pacote de revisão e, se houver revisão, métricas |

## 2.1 Validade de um ensaio

Cada ensaio grava `validade` e o `estado_final_das_sessoes`. É **inválido** se alguma câmera analisou 0 quadros na janela estável ou a sessão não estava em
execução no fim dela. Ensaio inválido aparece marcado (`⚠ INVÁLIDO`) no relatório e fica fora da variação entre repetições; **não apague**: preserve-o
(`..._INVALIDO_<motivo>.json`) e refaça o ensaio. A matriz acumula repetições em `ensaios/indice.json` (`--repeticao-inicial N`).

## 3. Fases de um ensaio (nunca misturadas)

1. **Importação** do runtime e **carga inicial** por câmera (carregar os dois modelos e abrir a fonte) e **primeiro quadro analisado**.
2. **Aquecimento** (15 s): descartado.
3. **Janela estável** (45 s): todas as métricas de operação. As amostras de recursos (1 Hz) e de idade (20 Hz) usadas são só as da janela.

Cada "câmera" é um arquivo de vídeo reproduzido **no ritmo nativo e em laço**, embrulhado pelo mesmo `LatestFrameCapture` das fontes de rede do runtime
(quadros acumulados são descartados quando a IA é mais lenta).

## 4. Métricas

* **fps efetivo** por câmera: quadros analisados na janela ÷ duração real da janela; **descartados** = quadros que a fonte entregou e a IA não leu.
* **CPU**: do processo em % do total da máquina (threads lógicos) e em núcleos equivalentes; CPU do sistema. **RAM**: RSS do processo (média da janela e pico do ensaio).
* **GPU**: utilização e memória usada (NVML, GPU NVIDIA); **VRAM Δ** = média da janela − uso antes de carregar modelos.
* **Idade da observação** (três medidas, todas em ms, a partir do instante em que a fonte entregou o quadro):
  * *na publicação*: quadro entregue → resultado disponível;
  * *em uso*: `agora − entrega do quadro do último resultado`, amostrada a cada 50 ms (o que um consumidor que lê o resultado mais recente enxerga; inclui a espera entre resultados);
  * *latência de processamento*: só o tempo dentro de `_process_frame`.

## 5. Hipótese do GIL (separada da capacidade)

O perfil mostra **onde está o tempo** (Python × nativo); a comparação threads × processos testa **se o ganho aparece sem o GIL**. A regra de decisão é fixada
**antes** de olhar os dados (ver `tools/bateria/escala.py`): razão ≥ 1,30 com CPU do sistema < 80 % ⇒ consistente com o GIL; CPU ≥ 80 % ⇒ limitado por CPU;
razão < 1,10 ⇒ sem diferença; senão inconclusivo. Nenhum dos dois resultados, sozinho, "prova" o GIL; o relatório descreve os confundidores.

## 6. Qualidade dos alertas: episódios revisados (avaliador v2)

A bateria **não rotula nada e não confirma revisão**. `episodios` analisa cada vídeo inteiro (todos os quadros), grava `analise_<vídeo>.json` com a **cobertura**
e entrega em `episodios_v2/revisao/<vídeo>/` os quadros de cada alerta, `linha_do_tempo.jpg` e `episodios.rascunho.json` (esses JPG mostram pessoas e não são versionados).

### 6.1 Duas coisas diferentes: proposta e confirmação

| | Proposta (ex.: de IA) | Anotação confirmada |
|---|---|---|
| `origem_revisao` / `status` | `ia` ou `humana` / `proposta_nao_confirmada` | `humana` / `confirmada` |
| Onde fica | `docs/referencia/<data>/propostas_ia/` (**não se edita**: é a proposta como foi feita) | `docs/referencia/episodios/<vídeo>.json` |
| O que o avaliador faz | `leitura_provisoria`: mostra o que sairia **se** fosse confirmada; rotulada `oficial=false` dentro do próprio corte e com nomes que **não** são os das métricas (`alertas_fora_dos_episodios_propostos`, não "falsos") | `avaliar`: métricas oficiais, **se** todas as condições abaixo valem |

`revisor`, `data`, `origem_revisao` e `procedencia` são **declarações** num arquivo editável. O avaliador **não verifica identidade, não prova que a revisão ocorreu e não detecta arquivos reeditados com campos coerentes**.
Ele recusa o que é inconsistente (ex.: IA com `status=confirmada`, `revisor` com nome de IA em anotação "humana", `integral=true` sem campos que o sustentem). A confiança vem do processo humano.

### 6.2 Contrato da anotação (esquema 2)

```json
{"esquema": 2, "video": "x.dav", "sha256": "<do vídeo>",
 "origem_revisao": "humana", "status": "confirmada", "revisado": true,
 "revisor": "Nome", "data": "AAAA-MM-DD", "revisao_completa_do_video": true, "revisao_cega": false,
 "procedencia": {"confirmada": true, "fonte": "DVR do 5º andar, exportado em 19/06", "tipo": "real", "confirmado_por": "Nome"},
 "episodios": [{"id": "e1", "sujeito_local": "opcional", "inicio_s": 9.2, "fim_s": 31.9, "criterio_inicio": "adulto sai do campo",
                "estado_esperado": "CHILD_ALONE|CHILD_SEPARATED|qualquer", "observacao": "..."}],
 "negativo_confirmado": false,
 "associacoes": [{"t_s": 6.333, "trilha": 1, "episodio": "e1"}]}
```

* **`episodios` é obrigatória.** `[]` só vale com `negativo_confirmado: true` (um negativo precisa ser **declarado**); sem a lista, a anotação é inválida (antes virava negativo em silêncio).
* `criterio_inicio` (texto) é **obrigatório em cada episódio** para métricas oficiais: ele define o que significa o atraso (ex.: "adulto sai do campo" ≠ "início do afastamento").
* `procedencia.confirmada = true` exige `fonte`, `tipo` (`real` | `sintetico`) e `confirmado_por`. Vídeos com marca de IA/“Veo” são suspeitos de sintéticos até confirmação.
* `associacoes` (opcional) são **decisões humanas** que resolvem ambiguidade por alerta (`episodio: null` = o alerta não pertence a nenhum); as que contradizem a janela/estado aparecem em `associacoes_manuais_fora_da_janela`.
* Campos livres, ignorados pelo cálculo: `metodo`, `observacao`, `instrucoes`, `derivada_de_proposta`.

**Métricas oficiais só existem quando TODAS valem:** anotação válida; `origem_revisao=humana`; `status=confirmada` e `revisado=true`; `revisao_completa_do_video=true`; procedência confirmada (com fonte, tipo e responsável);
`criterio_inicio` em todo episódio; revisor sem cara de IA; SHA-256 igual ao do vídeo; e **análise com cobertura integral consistente** (6.4). O que faltar aparece em `anotacao.pendencias`.
`revisao_cega` ausente/falso gera **aviso** (o revisor pode ter visto os alertas do sistema), não bloqueio.

### 6.3 Como o avaliador conta (determinístico, independe da ordem dos alertas)

* **Candidatos de um alerta:** episódios com estado compatível. Se o alerta cai **dentro** do intervalo `[inicio, fim]` de algum episódio, só esses competem (a tolerância não compete com um intervalo que já o contém). Só um alerta fora de todos os intervalos usa a janela `[inicio − tol, fim + tol]` (tol padrão 2 s).
* **Associação exclusiva:** cada alerta conta para **no máximo um** episódio. **Detecção** = casamento máximo entre alertas e episódios; preferência entre candidatos: (distância ao intervalo, `inicio_s`, `id`). Os demais alertas vão ao candidato preferido (**repetidos**) ou, sem candidato, são **falsos** (exige revisão completa).
* **Ambiguidade:** um alerta com 2+ candidatos não carrega “qual pessoa” (o registro não tem essa informação e nada é inventado). Aparece em `alertas_ambiguos`; `detectados` é **limite superior** sob ambiguidade e `detectados_sem_ambiguidade` conta só alertas de candidato único ou associação manual. Episódios sobrepostos são listados e, sem `sujeito_local` distintos, geram aviso.
* **Trilhas:** `trilhas_adicionais` = trilhas distintas − 1 (pode ser fragmentação **ou** outra criança); `transicoes_de_trilha` = mudanças de trilha na ordem do tempo (1→2→1 = 2). Nenhuma das duas prova fragmentação.
* **Negativos:** vídeo sem episódio e `negativo_confirmado`; todo alerta é falso. Negativo com menos de 600 s é **teste de sanidade** (`aviso_negativo_curto`), não estima falsos por hora.

### 6.4 Cobertura integral

A análise registra `cobertura`: terminou por **EOF** (sem timeout, sem erro) **e** `quadros_analisados == quadros_do_arquivo`. O motivo é lido **antes** de parar a sessão (um `stop()` apagaria a diferença entre fim normal e timeout).
O avaliador **recalcula** `integral` a partir dos campos-base e recusa inconsistências; análises sem `cobertura` (avaliador v1) não geram métricas. Arquivos cuja contagem de quadros o contêiner não declara com exatidão ficam sem cobertura comprovada (conservador).

### 6.5 Caminho para confirmar (sem fabricar confirmação)

1. Uma pessoa revê o vídeo **inteiro** (`linha_do_tempo.jpg` ajuda a navegar), confere cada episódio da proposta e fecha o `criterio_inicio` de cada um.
2. Confirma a origem do arquivo (real ou sintético; quem confirmou).
3. Registra a declaração, uma flag por afirmação, nenhuma com valor padrão que confirme por você:

```
python tools/bateria_referencia.py confirmar docs/referencia/2026-10-04/propostas_ia/dvr1.json --revisor "Nome da pessoa" \
   --vi-o-video-inteiro --episodios-conferidos --procedencia-confirmada --procedencia-tipo real \
   --procedencia-fonte "DVR do 5º andar, exportado em 19/06" --criterio-inicio dvr1-e1="adulto sai do campo" [--revisao-cega]
python tools/bateria_referencia.py reavaliar --saida docs/referencia/2026-10-04      # sem refazer a inferência
python tools/bateria_referencia.py relatorio --saida docs/referencia/2026-10-04
```

`confirmar` recusa revisor com cara de IA, copia a proposta para `docs/referencia/episodios/` com `derivada_de_proposta` (inclui o `metodo` original, que **não** vale para a confirmada) e **não altera** o arquivo da proposta.
Se uma proposta for de negativo (`episodios: []`), `confirmar` o declara negativo — só use depois de rever o vídeo todo.

### 6.6 Versões e histórico

`episodios/` (rodada de 04/10/2026) foi gerada pelo **avaliador v1** e fica como histórico (sem cobertura, associação não exclusiva, “troca de trilha”): **não use para métricas**. `episodios_v2/` é do **avaliador v2**
(`versao_avaliador: 2`). Mudanças v1 → v2: associação exclusiva e determinística (`tolerância` só fora dos intervalos); validação da anotação e proveniência; `trocas_de_trilha` substituída por `trilhas_adicionais` e `transicoes_de_trilha`;
cobertura obrigatória e consistente; `criterio_inicio`/procedência obrigatórios; leitura provisória separada. Nenhum número oficial de qualidade foi gerado nem recalculado.

Candidatos: `DVR-1-Ch.7-8 5-- PAV-Hall-ELEV SERVI.dav` e `DVR-3-Ch.0-1 ACESSO-BLINDEX.avi` (positivos prováveis, ver [revisão visual por IA](2026-10-04/revisao_codex.md)) e `stream-normal.mp4` (**candidato a negativo**, origem não confirmada; os vídeos com marca “Veo” são suspeitos de sintéticos).
Sem a revisão humana, os números de qualidade ficam **indisponíveis**, não estimados.

## 7. Como ler e como comparar depois

* Compare **mesmos vídeos, mesmo hardware, mesmo procedimento**; confira o hash dos modelos e dos vídeos em `ambiente.json`.
* Uma repetição por configuração não dá variância: para decidir a migração use `--repeticoes 3` e compare pelo intervalo, não por um valor.
* Para o modo sombra da etapa 03, o critério de qualidade é o arquivo de episódios revisados, reaplicado ao motor novo com `avaliar`.

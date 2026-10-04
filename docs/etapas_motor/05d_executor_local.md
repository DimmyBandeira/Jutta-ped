# Lote 05d — Executor local mínimo e segundo pacote (etapa 05)

Estado: **concluído em 04/10/2026**; commit: ver `git log` da branch ("executor local minimo").
Plano: [sistema-mãe, especialistas e nós](../plano_motor_plugins.md) · depende de [05a](05a_schema_validador.md),
[05b](05b_instalacao_local.md) e [05c](05c_painel_especialistas.md). **A etapa 05 NÃO está concluída**: faltam receitas
e a migração da pediatria (etapa 03). Sem alertas operacionais neste lote.

## Escopo entregue

Executor que resolve o especialista **ativo**, confere a integridade, carrega o modelo (formatos `pt` e `openvino`) e
processa recortes devolvendo atributos conforme o contrato; CLI de diagnóstico `executar`. Contrato em
[manual, seção 14](../manual_especialista.md#14-executor-local-mínimo-implementado). Um **segundo pacote** (não pediátrico)
exercita a execução ao lado do primeiro.

Fora deste lote: receitas, regras, alertas, câmeras, executor compartilhado entre câmeras, fila/justiça, transporte
remoto, formato `onnx`, térmico, migração da pediatria.

## Arquivos

| Arquivo | Função |
|---|---|
| `src/jutta_ped/especialistas/executor.py` | `Executor`, `Pedido`, mapeamento de atributos, validações, `calcular_config_hash` |
| `src/jutta_ped/especialistas/backend_ultralytics.py` | Backend `pt`/`openvino` (importado só ao carregar um modelo) |
| `src/jutta_ped/especialistas/instalador.py` | `obter_ativo()` (versão ativa com verificação opcional; somente leitura) |
| `src/jutta_ped/especialistas/cli.py` | subcomando `executar` |
| `tests/test_especialistas_executor.py` | 23 testes |

## Validação executada (Windows, Python 3.12.7)

* `tests/test_especialistas_executor.py`: **23 passaram**, com backend falso determinístico (pesos sintéticos): contrato do
  resultado; sim/não/indeterminado por limiares; contagem e fora da faixa; parâmetros efetivos e validação; limiares invertidos;
  `config_hash`; altura mínima sem chamar o modelo; pedidos inválidos isolados e na mesma ordem; prazo; divisão em lotes
  (3, 3, 1); pacote adulterado antes da carga (falha fechada, nada carregado); reconferência após `descarregar`; classe
  inexistente; `onnx` recusado; queda de `cuda` para `cpu`; ativar/reverter recarregando sozinho; **segundo pacote
  independente** (categoria, tri e número) intercalado com o primeiro; ativar um pacote não recarrega o outro; teste que
  analisa o código do executor e do backend e não encontra termos da pediatria; CLI.
* **Modelo real:** o teste de fumaça empacota `src/models/Bebe-no-colo.pt` (arquivo local, não versionado; o teste pula sozinho
  se ele ou o vídeo faltarem), instala, ativa e executa em quadros de `videos/Corredor pediatria.mp4`: os três atributos saíram
  `definido/nao` e `carrinhos=0` (nenhum bebê/carrinho nesses quadros), modelo em **`cuda:0`**, 960 px. Tempos: primeira
  chamada ~5,8 s (conferência de integridade + carga + aquecimento, contados em `fila_ms`/`inferencia_ms`), depois ~22 ms e ~23 ms
  por quadro em média em 10 execuções (RTX 3050, fp16). São medidas de fumaça de uma execução, não benchmark.
* Suíte completa: **518 passaram, 6 puladas** (testes de navegador de vídeo, puladas como antes).

## Limitações

* Não valida **qualidade** de nenhum modelo; o resultado "nao" nos quadros reais não prova detecção correta.
* A verificação de integridade só ocorre ao carregar; adulteração posterior só é vista no próximo carregamento.
* Um executor por processo e sem fila: não há justiça entre câmeras nem controle de memória/VRAM (etapa 04).
* `onnx` não executa (sem `onnxruntime`); `termica`/`quadro` só como fonte única, sem fusão.
* `.pt` executa pickle ao carregar: só pacotes da equipe, sempre depois da conferência de integridade.
* Pré-processamento depende do Ultralytics; `cor`/`normalizacao` do manifesto não são aplicadas por este backend.

## Próximo lote recomendado

Receitas (etapa 05): ligar especialistas ativos a regras e câmeras, com versão registrada nos alertas; ou a bateria de
referência (etapa 02) antes de migrar a pediatria (etapa 03). Os nós seguem aguardando A1–A4.

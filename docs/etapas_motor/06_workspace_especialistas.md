# Etapa 06 — Workspace de especialistas (registro de preparação)

Estado: **pendente — itens registrados, nada implementado** (05/10/2026). Plano: [`plano_motor_plugins.md`](../plano_motor_plugins.md).
Critério de conclusão da etapa (do plano): *um pacote produzido **fora** do repositório é validado e instalado no sistema-mãe*.
A etapa 02 (qualidade pediátrica) **não** é pré-requisito; a mãe genérica e os especialistas externos evoluem sem ela.
O 05f ([registro](05f_configuracao_camera.md)) está com critérios atendidos e **não** depende de nada daqui.

## Itens registrados (origem: revisão do 05f)

Verificados no código em 05/10/2026. Impacto em ordem decrescente.

| # | Item | Situação | O que falta |
|---|---|---|---|
| 1 | **Ferramental não é distribuível.** `validar`, `hash` e `executar` só rodam como `python -m src.jutta_ped.especialistas` dentro do repositório da mãe; os módulos importam `src.jutta_ped...` (caminho absoluto) e dependem de `jsonschema` e `pyyaml`. | Aberto | Artefato versionado só com schema, validador, hash e executor, sem clonar a mãe (ver "Kit mínimo proposto") |
| 2 | **`executar` exige instalar e ativar** o pacote no registro da mãe (`Instalador`); o autor não roda uma pasta/zip com os próprios recortes. | Aberto | Executar sem instalar, reutilizando o **mesmo** `Executor` (o executor só precisa de `obter_ativo`; um adaptador de pacote local basta) |
| 3 | **Amostras `amostras/*.esperado.json` não são reproduzidas.** O validador as lista entre os "não verificados". | Aberto | Comando que execute o pacote sobre as amostras e compare com os esperados |
| 4 | **Classes de `.pt`/`.onnx` só aparecem ao carregar** (questão aberta 8 do manual). | Aberto | Fechado de fato pelo comando do item 3 (a conferência de classes já ocorre ao carregar) |
| 5 | **Dispositivo executado não era reportado; dispositivo explícito não era honrado.** | **Resolvido na mãe em 05/10/2026** (contrato de dispositivo: `src/jutta_ped/dispositivo.py`, `Executor.dispositivos_permitidos`, `info()` com `dispositivos_efetivos` e `fallbacks`) | O kit precisa **expor** isso ao autor (`--dispositivo` estrito, efetivo e fallbacks no relatório) para ele declarar `custo` com honestidade |
| 6 | **Crops representativos.** O autor não sabe que recortes a mãe enviará (margens por câmera, mínimo em px, sem redimensionar; o alvo `quadro` não é habilitado). | Aberto | Guia curto, apoiado na página de diagnóstico do mestre (miniaturas/recortes) para obter amostras reais |
| 7 | **Experimentar numa receita fora do repositório.** Só existe um exemplo de receita contrato 2 (pediátrico); o autor não precisa escrevê-las, mas precisa ver os atributos do seu pacote numa receita mínima. | Aberto | Receita mínima de exemplo por tipo de atributo + caminho documentado (importação por JSON na mãe) |
| 8 | **Aceite da etapa não executado.** Nenhum pacote não pediátrico com modelo real foi produzido fora do repositório e instalado. | Aberto | É o teste que prova o contrato público; autoria/assinatura (questão aberta 6) só importa para terceiros |

Fora desta etapa e **não** iniciados: nós remotos, treinamento, executor compartilhado (04), térmico.
Um **segundo modelo real não pediátrico** não é bloqueio do 05f; ele entra aqui apenas como o pacote do aceite (item 8).

## Kit mínimo proposto (não implementado neste lote)

Objetivo: um autor, em outro workspace e **sem abrir o código da mãe**, valida e experimenta o pacote com o contrato público.
Proposta, do menor para o maior; só o núcleo é necessário para o item 8:

**Núcleo (um único artefato versionado — roda por `pipx`/`pip` ou `.pyz` — com versão atrelada ao `protocolo` do manifesto):**
1. `validar <pasta|zip>` — o validador estático atual + schema, com os mesmos códigos de saída.
2. `hash <arquivo|pasta>` — o hash canônico (`jutta-dir-v1` para pasta OpenVINO).
3. `executar <pasta|zip> <imagem...> [--parametro k=v] [--altura px] [--dispositivo cpu|intel_gpu] [--sem-fallback]` — carrega o pacote **sem instalar**, executa o mesmo `Executor` e imprime atributos, tempos e, **sempre**, dispositivo pedido, efetivo e fallbacks. Falha com explicação se o dispositivo explícito não for honrado.
4. `conferir-amostras <pasta|zip>` — roda as `amostras/` e compara com `*.esperado.json` (itens 3 e 4).
5. Molde (`especialistas_molde/`) e manual público empacotados como arquivos de dados, para o autor trabalhar offline.

**Opcional, depois do núcleo:** `receita-minima <pacote>` que gera, a partir do manifesto, uma receita contrato 1/2 de exemplo para importar na mãe (item 7), e um pacote de **conformidade** autocontido (sem pesos, backend determinístico) para o autor testar o próprio fluxo.

**Fora do kit (continua na mãe):** `Instalador`/registro, painel, motor, receitas em execução, câmeras, alertas, nós.

**Como evitar um fork:** o kit é **construído** a partir de `src/jutta_ped/especialistas/` e `src/jutta_ped/dispositivo.py` por um script de build (cópia com reescrita do prefixo de importação, ou conversão para importações relativas), nunca mantido à mão. O único acoplamento real do executor com a mãe é `Instalador.obter_ativo`: um adaptador de pasta/zip local o satisfaz sem alterar o executor.

**Dependências do kit:** `jsonschema`, `pyyaml`, `numpy`, `opencv-python`; `ultralytics` + `openvino` como extra para executar (o validador não os exige).

**Decisões que dependem do responsável pelo produto:** (a) forma de distribuição (wheel num índice privado, `.pyz` anexado ao repositório ou pasta versionada); (b) política de versão (kit = versão do protocolo, com compatibilidade declarada); (c) se `.pt` (pickle) é aceito no kit para autores internos, como hoje, ou só OpenVINO/ONNX; (d) se a receita mínima entra no kit ou fica na mãe.

## Critérios de conclusão e evidências

1. Um pacote **não pediátrico, com modelo real**, produzido fora do repositório só com o kit e o contrato público.
2. Esse pacote passa em `validar` e em `conferir-amostras`, e `executar` o roda sem instalar, reportando o dispositivo efetivo.
3. O mesmo pacote é instalado e ativado **na mãe** e executa por uma câmera (sombra), sem editar o host.
4. O que depender de edição da mãe é registrado como defeito do contrato, não contornado.

Evidências: nenhuma ainda. Não declarar a etapa iniciada além deste registro até haver decisão sobre o kit.

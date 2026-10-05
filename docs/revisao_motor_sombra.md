# Revisão do motor mínimo em sombra

Data: 2026-10-04. Base: working tree após `d16dab6`. Revisão pelo Codex, sem alterar a implementação ou commitar.

## Parecer

O desenho está alinhado ao sistema-mãe: observações e recortes na mãe, atributos no especialista, validade e receitas na mãe. O caminho genérico existe, mas a captura/mestre ainda é alimentada pelo serviço pediátrico. Isso é uma ponte de integração em sombra, não a separação final do host nem a migração da pediatria.

Executei os dois arquivos de testes do motor, excluindo a fumaça com modelo real: **56 passaram, 1 deselecionado**. Não reexecutei a suíte completa de 708 nem a fumaça. Fiz também a reprodução abaixo com instalador, executor e motor reais, pesos sintéticos e backend falso.

## Achados a corrigir antes de encerrar este lote

### 1. P1 — troca de contrato incompatível pode gerar “não” em vez de indeterminado

Em `src/jutta_ped/motor/motor.py:149`, o despacho verifica se o atributo ainda existe, mas não revalida tipo, categorias e condições da receita contra o contrato recém-ativado. A validação completa só ocorre em `configurar`.

Reproduzido: configurei a receita com `bebe_no_colo` tri e `igual="sim"`; ativei a versão 2.0.0 com o mesmo nome de atributo, agora numérico, mapeado para contagem. Os dois pacotes são válidos. O motor passou de `sim` para `nao`, com `erros={}`. Ao chamar o validador da receita contra o contrato novo, ele corretamente reclama que `igual` deveria ser numérico.

Correção: revalidar compatibilidade ao mudar a identidade ativa, limpar os atributos afetados e produzir indeterminado com motivo explícito quando a receita deixa de ser válida. Cobrir alteração de tipo, remoção de categoria e demais restrições usadas pela receita. Não exigir reconfiguração manual para detectar incompatibilidade de uma ativação feita pelo painel.

### 2. P1 para uso ao vivo — sombra síncrona atrasa a publicação operacional

Em `src/jutta_ped/service/runtime.py:651`, `_alimentar_sombra` roda antes de atualizar overlay e publicar os alertas daquele quadro. Ela espera os especialistas e o destino do rastro. O prazo descarta resultados, mas não interrompe inferência ou gravação bloqueada. O lock global do motor também serializa câmeras que compartilhem a instância.

Achado por inspeção do fluxo, não por benchmark. Capturar exceções impede que a falha derrube o laço; não garante ausência de impacto temporal. O teste atual usa `NO_CHILD` e verifica ausência de eventos de sombra no arquivo operacional, sem exercitar o tempo de entrega de um alerta real.

Para continuar com esta implementação síncrona, restringir o ensaio a arquivo offline/processo de teste isolado e documentar o impacto. Antes de habilitar em câmera operacional, criar despacho desacoplado com fila limitada/descarte e teste de especialista e destino lentos, sem bloquear publicação operacional. Isso não exige antecipar toda a distribuição de GPU, mas exige uma fronteira de execução segura para a sombra ao vivo.

### 3. P2 — idade do frame ao vivo começa depois do buffer local

Em `runtime.py:537`, o relógio é amostrado depois de `capture.read()`. Para `LatestFrameCapture`, essa leitura consome um quadro que a thread leitora já recebeu antes. A espera entre recepção e consumo é perdida, pois o buffer guarda o frame sem o timestamp de recepção. Logo, o orçamento não cobre toda a idade local da observação.

Achado por inspeção. Registrar timestamp monotônico junto ao quadro na thread leitora e propagá-lo à observação, preservando a compatibilidade da captura. Testar frame recebido antes do prazo e consumido depois de vencê-lo. Não confundir esse timestamp local com horário físico da câmera, que segue fora da medição.

## Decisão sobre cache após erro

Manter o último atributo válido até sua expiração é coerente como política explícita de reutilização, desde que sua idade continue contando desde a captura original e a falha nova apareça no evento. Não renovar validade por erro. Resultado novo explicitamente indeterminado deve substituir o anterior; erro de contrato/identidade deve invalidar o cache afetado.

Não afirmar que diminuir `validade_ms` equivale a invalidar imediatamente na primeira falha: são políticas diferentes. Para a v1, documentar a política atual como `manter_ate_expirar`; se uma receita exigir invalidação imediata, implementar essa opção explicitamente antes de habilitá-la. Reutilização deve aparecer como tal na condição do evento, além de idade e quadro de origem. Ainda falta decidir como regras tratarão alerta já ativo quando a observação ficar indeterminada.

## Próxima entrega, após as correções

Priorizar **separação do mestre e contexto de cena na mãe**, antes de nós ou painel de habilitação:

1. Expor detecção/rastreamento genéricos e contexto de todas as trilhas do quadro sem depender da execução integral da análise pediátrica. Reutilizar código existente; não duplicar modelos para simular independência.
2. Evoluir receitas/regras do host para relações entre trilhas e persistência temporal, com estados ausente/indeterminado e política para alertas ativos. Especialistas continuam produzindo atributos; companhia não deve ser escondida dentro do pacote de criança/adulto.
3. Verificar se o classificador já existente pode ser empacotado com o contrato publicado. Documentar incompatibilidades antes de propor treinamento ou modelo novo. Criar o pacote de papel criança/adulto e a receita pediátrica sobre essas capacidades.
4. Demonstrar o fluxo também com um pacote não pediátrico, sem condicionais de domínio no núcleo. Depois comparar a migração com a referência revisada, mantendo a etapa 02 aberta enquanto faltarem declarações e fontes.

A execução compartilhada sob carga fica na etapa 04; a separação dos especialistas em outro workspace só estará demonstrada quando um pacote produzido fora entrar pelo contrato, sem alteração do host. Não declarar a mãe pronta apenas pela fumaça de um quadro inteiro tratado como crop.

## Retorno curto para o Claude

```text
Leia docs/revisao_motor_sombra.md. Corrija primeiro os três achados,
reproduzindo a troca incompatível de tipo e adicionando regressões pertinentes.
Documente a política de cache; validade curta não equivale a invalidar ao errar.
Não habilite sombra síncrona em câmera operacional.
Depois detalhe o próximo lote: mestre/contexto genéricos e regras entre trilhas
na mãe, com reaproveitamento do classificador atual para o primeiro pacote de papel.
Não implemente nós nem amplie a bateria de capacidade. Preserve alterações
anteriores e pesos locais. Informe testes executados e limites, brevemente.
```

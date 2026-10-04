# Plano de trabalho — sistema-mãe, especialistas e nós

Estado: **ativo — etapa 01 em andamento (documentação)**.
Início: 04/10/2026.
Branch: `feature/motor-plugins-nos`.
Base: `2e8b06a7e7228f497a5220baad3065313e8a21be`, originada de
`feature/service-layer-evidencias-ui`.

## Objetivo e ponto de partida

Preparar o sistema-mãe para receber especialistas desenvolvidos em outro workspace, distribuídos
como pacotes. Câmeras, execução, receitas, regras, alertas e painel pertencem ao sistema-mãe.
A pediatria será migrada como primeiro caso de validação. Os nós executam o mesmo contrato dos
especialistas, com transporte e gestão de capacidade próprios.

A criação da branch preservou as alterações locais existentes: `motor_de_analise.md` modificado,
`prompt_discussao_motor.md` e `transicao_motor_de_analise.md` ainda não rastreados, além de
`src/models/Bebe-no-colo.pt`. Esses arquivos não compõem o commit-base acima. Este registro não
declara um snapshot commitado das alterações locais nem autoriza incluir o modelo em um commit.

Referências de arquitetura: [motor](motor_de_analise.md),
[transição](transicao_motor_de_analise.md) e [roteiro de revisão](prompt_discussao_motor.md).
Este plano organiza o trabalho; os contratos vigentes ficam nos documentos técnicos próprios.

## Etapas e critérios de conclusão

| Etapa | Entrega | Critério para concluir | Estado |
|---|---|---|---|
| 01 — Contratos autossuficientes | `docs/manual_especialista.md` e `docs/protocolo_nos.md` | Um autor externo consegue entender pacote, entrada, saída, validade e compatibilidade sem abrir o código da mãe; exemplos e decisões pendentes identificados | Em andamento: [registro](etapas_motor/01_contratos.md) |
| 02 — Referência reproduzível | Bateria em `tools/`, relatório e vídeos/episódios de referência identificados | Custo e qualidade atuais medidos com procedimento reproduzível; hipótese de GIL avaliada com perfil | Pendente |
| 03 — Motor mínimo e migração | Mestre, especialista pediátrico e receita no contrato novo | Comparação em modo sombra com a etapa 02; alertas perdidos, falsos e repetidos avaliados; diferenças justificadas | Pendente |
| 04 — Executor compartilhado | Modelos compartilhados por configuração compatível, lotes e filas limitadas | Memória, latência, justiça entre câmeras, expiração e falhas validadas sob carga | Pendente |
| 05 — Pacotes e prontidão da mãe | Schema executável, molde declarativo, comando validador, instalação/ativação/retorno de versão e receitas | Pediatria e segundo pacote de conformidade funcionam sem caminhos especiais; pacote compatível entra sem editar o host | Em andamento parcial: schema, validador estático e molde antecipados no [lote 05a](etapas_motor/05a_schema_validador.md) (sem commit); instalação, ativação, retorno de versão, receitas e segundo pacote pendentes |
| 06 — Workspace de especialistas | Projeto separado com contrato, molde e validação utilizáveis | Pacote produzido fora do repositório é validado e instalado no sistema-mãe | Pendente |
| 07 — Fusão térmica | Calibração, associação de fontes e atributos relativos | Erro geométrico, sincronismo e perda de fonte exercitados com dados reais | Pendente |
| 08 — Nós de execução | Um nó inicialmente, transporte, capacidades e métricas | Ganho sobre o local demonstrado; queda, duplicação, expiração e incompatibilidade tratadas | Pendente |

As etapas 01 e 02 são frentes independentes. A referência não bloqueia escrever os contratos;
ela é necessária para avaliar a migração. A operação remota vem depois da validação local.
A posição da fusão térmica pode ser revista explicitamente conforme disponibilidade das fontes.
O schema, molde e validador podem ser implementados antes da etapa 05, quando necessários;
essa etapa marca sua entrega integrada ao ciclo de instalação.
Essa antecipação ocorreu em 04/10/2026 (lote 05a). A etapa 05 só será concluída com o ciclo
de instalação integrado e os demais critérios da tabela.

A ação atual é documental. Esta lista não inicia automaticamente código, benchmarks, criação do
workspace externo ou execução de nós. Os números de 4 câmeras, idade de observação ~1 s e alerta
~2 s continuam metas de ensaio com condições de medição a fechar, conforme a transição.

## Como conduzir uma etapa

Ao iniciar uma etapa, criar seu registro em `docs/etapas_motor/NN_nome.md`. Registrar objetivo,
escopo, entregas, dependências, critérios de conclusão e evidências. Criar apenas os registros
das etapas efetivamente iniciadas; a tabela acima basta para as futuras.

O registro da etapa deve deixar claro quais arquivos serão editados e quem está trabalhando
neles quando houver mais de um assistente. Para a etapa 01, o trabalho técnico deve se concentrar
nos dois documentos novos, evitando edição concorrente dos três documentos de arquitetura.

Estados: **pendente → em andamento → concluída e arquivada**. Quando houver bloqueio, registrar
motivo e dependência sem marcar a etapa como concluída. Mudança de escopo exige atualizar o plano
e o registro da etapa; não esconder trabalho faltante no histórico.

## Encerramento e histórico

Ao concluir cada etapa:

1. Conferir seus critérios e preencher o resultado: entregas, validações, limitações e pendências
   transferidas explicitamente para outra etapa. Incluir commits quando existirem.
2. Marcar o registro como **concluído**, com data, e movê-lo para
   `docs/historico/motor_plugins/NN_nome.md` (criar a pasta no primeiro arquivamento).
3. Atualizar esta tabela para **concluída e arquivada**, apontando para o registro no histórico.
   Corrigir links relativos afetados pela mudança. Não manter uma cópia ativa do mesmo registro.

O histórico guarda registros encerrados. Manual do especialista, protocolo dos nós, schemas,
exemplos e validador permanecem em seus locais de uso, com versões atualizadas: são contratos e
ferramentas necessários ao desenvolvimento separado.

Quando todas as etapas do escopo estiverem concluídas, registrar o resultado global e arquivar
este plano em `docs/historico/motor_plugins/plano_motor_plugins.md`, corrigindo os links.
Remover sua cópia ativa e atualizar eventuais referências. Trabalho novo abre outro plano;
este histórico não volta a funcionar como lista de tarefas.

Nenhum arquivo foi arquivado agora: a transição ainda está em andamento. Commit e eventual
integração da branch devem conter somente arquivos revisados e pertinentes ao trabalho.

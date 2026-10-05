# Revisão do fechamento 05f e preparação da etapa 06

Data: 05/10/2026. Revisão do working tree, sem commit ou alteração da implementação.

## Reteste após as correções

As duas brechas descritas abaixo foram corrigidas e conferidas no código:
comparação por conjunto de identidades completas; telemetria ausente falha no
proxy estrito e no backend do executor. O proxy com fallback permitido mantém
o estado não conferido e tenta novamente. A conferência só é marcada após sucesso.

Reexecutei dispositivo, backend/dispositivo, backend/lote e executor:
**67 passaram, 1 pulado** (fumaça com modelo/vídeo locais ausentes).
Não repeti a suíte completa nem o ensaio real. **Correção aprovada neste escopo.**
Os achados abaixo ficam como histórico da revisão, não pendências atuais.
A etapa 06 não foi iniciada nem o 05f arquivado por este reteste.

## Evidência verificada

Li o resultado `runtime/validacao_painel_05f_20261005_155747/resultado.json`:
três câmeras, configuração pelo navegador, restream da mãe, crops conferidos e
troca de A sem alterar B/C. O arquivo registra CPU efetiva e ausência de erros.
Não reexecutei esse roteiro nem a suíte completa. Reexecutei 35 testes de
dispositivo, multicâmera e medição: todos passaram.

O relato permanece no escopo. A evidência do painel não deve ser refeita por
causa dos ajustes de contrato abaixo, que podem ser provados deterministicamente.

## Dois ajustes antes de encerrar a correção de dispositivo

1. `conferir_efetivo` usa `any(alvo in e.upper() ...)`. Reproduzi que pedido CPU
   aceita `EXECUTION_DEVICES = ['CPU', 'GPU.0']`; pedido GPU.0 aceita `['GPU.01']`.
   O contrato explícito precisa comparar identidades normalizadas completas e
   verificar todos os dispositivos efetivos, não apenas encontrar um compatível.
   Acrescentar regressões para lista mista e nomes de prefixo semelhante. Se o
   backend produzir sufixos legítimos, documentar e normalizar esses casos exatos.

2. Quando a leitura do dispositivo efetivo retorna `None`, `ModeloOpenVinoEstrito`
   devolve o resultado e grava a resolução em `_conferidos`; nunca tenta conferir
   novamente aquele pedido. Reproduzi com modelo sem telemetria. Separar resolução
   do pedido de verificação efetiva: desconhecido não pode virar "conferido".
   No modo estrito OpenVINO, falhar explicitamente se não puder verificar após
   inferência, ou manter o estado não verificado conforme contrato documentado,
   sem afirmar cumprimento e sem bloquear uma nova tentativa de inspeção.
   Aplicar a mesma semântica ao backend do executor.

Essas reproduções usam modelos falsos que devolvem as propriedades indicadas;
não demonstram que a rodada real em CPU executou em hardware errado.

## Direção recomendada para o kit (decisões de projeto)

- Distribuição inicial: **wheel instalável em venv**, entregue como artefato local
  versionado. Índice privado é opcional posterior; não precisa criar infraestrutura
  de publicação para o primeiro autor. Inferência tem dependências nativas, então
  `.pyz` não elimina a instalação dessas dependências.
- Versão do kit independente do protocolo: começar em `0.1.0`, declarar
  explicitamente protocolos suportados (`[1]`) e testar compatibilidade com a mãe.
  Não obrigar mudança de protocolo para cada correção do CLI.
- `.pt` permanece restrito aos autores internos/equipe como na v1 existente.
  Reutilizar as restrições de confiança; não ampliar para código de terceiros.
- Receitas continuam executadas pela mãe. Entregar exemplo estático mínimo e
  instrução de importação; gerador de receitas fica fora do núcleo inicial.
- Reutilizar os mesmos módulos por estrutura/importações de pacote adequadas.
  Evitar copiar fontes e reescrever imports por substituição textual no build.
- `executar pasta/zip` usa validação e resolução segura, sem ativar no registro da
  mãe. ZIP exige as mesmas regras de caminhos, limites e integridade; não basta
  fornecer `obter_ativo` a partir de uma extração sem essas garantias.
- Não prometer ONNX executável antes de haver backend e dependências suportados.
  Validar manifesto e reproduzir inferência são capacidades diferentes.

Após os dois ajustes e testes focados, registrar fechamento do 05f conforme o
procedimento do plano, preservando documentação normativa acessível. A etapa 06
tem aceite próprio: autor em workspace separado, sem importar código da mãe,
produz pacote e o instala/executa nela sem editar o host. Não vincular esse aceite
à referência clínica da etapa 02 nem iniciar nós/executor compartilhado.

# Próximo lote — conectar sistema-mãe, recortes e especialistas

Base revisada: `d16dab6`, branch `feature/motor-plugins-nos`. Direção reafirmada pelo usuário: foco no sistema-mãe extensível; pediatria é um caso de validação. Este documento prepara o próximo lote; a integração ainda não foi implementada.

## Situação comprovada

`src/jutta_ped/especialistas/executor.py` recebe `Pedido` com recorte, câmera, geração, trilha, quadro, orçamento e parâmetros. Instalação, ativação e CLI existem. As referências de uso encontradas no código produtivo são a CLI e o backend; `service/runtime.py` carrega `modulo.pediatria.crop_pipeline.PersonCropSpecialistPipeline`. O novo executor ainda não integra o fluxo de câmeras. Não confundir crops de evidência do runtime com despacho de recortes aos pacotes instalados.

O vídeo da pessoa com bolsa foi um negativo de sanidade da **pediatria atual**, para procurar alertas indevidos de criança sozinha. Não é teste de detecção de bolsa, de pacote novo ou de integração genérica. Zero alertas em 13 s não prova prontidão da mãe.

## Entrega prioritária

Construir um caminho local em sombra: **câmera/arquivo → detector mestre e trilha → observação e crop → especialista instalado/ativo → atributos com validade → receita → evento de sombra**. O especialista produz atributos; acompanhamento, persistência, combinação e alertas pertencem à mãe. Os recortes necessários dependem da entrada declarada pelo especialista; não presumir que todo modelo aceita crop de pessoa nem eliminar contexto espacial necessário às regras.

1. Ler as seções pertinentes de `docs/manual_especialista.md`, `docs/transicao_motor_de_analise.md`, o executor, o ponto de captura/análise do runtime e o pipeline atual. Criar registro `docs/etapas_motor/03_motor_minimo.md` com fronteiras e arquivos antes de implementar. Não reabrir decisões de arquitetura já tomadas.
2. Reutilizar captura e detecção existentes onde apropriado, definindo a fronteira genérica da observação. Registrar câmera, geração de sessão, quadro, trilha, instante de captura e geometria do recorte. Delimitar/corrigir bbox e descartar recorte vazio. Preservar transformação entre coordenadas do quadro e do crop.
3. Implementar associação mínima e versionada câmera/receita/especialista, validando entrada e atributos requeridos. Despachar ao executor existente, com orçamento restante calculado desde a captura. Resolver ativo e mudanças de versão pelo serviço existente.
4. Consumir atributos com expiração por atributo e receita; ausência, erro ou atraso viram indeterminado. Rejeitar resultado de sessão anterior ou mais antigo. Manter contexto espacial e temporal das regras na mãe.
5. Produzir eventos e rastros de sombra com identidades, versões e configuração. A habilitação do caminho deve ser explícita. Não substituir alertas operacionais antes da comparação da migração; evitar que ensaios emitam alertas reais duplicados.

## Evidência de que o fluxo é genérico

Usar dois pacotes declarativos de conformidade, com backend determinístico nos testes, e comprovar o caminho completo até a receita. Cobrir isolamento entre câmeras/sessões, crop e coordenadas corretos, validade, atraso, indeterminado e ativação/reversão sem mistura de versões. Um pacote compatível novo deve entrar por manifesto/configuração sem acrescentar condicionais por nome no host. Teste que apenas procura palavras pediátricas no código não comprova isso.

Separar evidências: teste de contrato com backend falso, fumaça de integração com modelo real compatível e comparação de qualidade da migração são verificações diferentes. Não tratar a primeira como prova das demais. A adaptação pediátrica deve explicitar como preservará contexto/companhia e o que ainda falta; não inventar que um pacote de bebê no colo substitui o classificador criança/adulto.

## Dependências e escopo

A etapa 02 permanece aberta. A referência humana é requisito para **aprovar a migração**, não para escrever/testar o fluxo genérico. Critérios de início e revisão não cega foram definidos em `docs/referencia/2026-10-04/fechamento/README.md`; confirmação nominal/procedência e negativo longo ainda faltam. O usuário informou que já assistiu, mas não declarou nome nem origem dos arquivos. Não fabricar esses dados.

Não repetir bateria de capacidade nem ampliar o avaliador neste lote sem defeito que bloqueie a integração. Não implementar nós, térmico, fila compartilhada da etapa 04 ou repositório externo agora. Preservar vídeos, pesos e propostas originais; nenhum push/publicação.

Se usar agentes: no máximo dois, um para núcleo de integração e outro para revisão somente leitura dos contratos/testes, sem edições concorrentes. Coordenador integra e valida. Não usar `/batch` para mudanças dependentes no mesmo fluxo. Relatar evidência do trajeto completo, arquivos, testes e pendências, sem recontar o histórico.
